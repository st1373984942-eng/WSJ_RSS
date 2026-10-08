#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文 WSJ 政治板块：把 Google News RSS 的加密链接解回真实 URL，并试其它镜像源。

Google News RSS 是唯一确认"新鲜"的发现源（items 日期是当天），
但 <link> 是 news.google.com 的加密跳转。这里试三种还原办法：
  1. base64 解 payload（老格式里直接嵌了 URL）
  2. 不带重定向 GET，看 Location 头
  3. batchexecute 接口（新格式的官方还原路径）

用法：python probe_en_gn.py
"""

from __future__ import annotations

import base64
import json
import re
import sys
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
CDX = "http://web.archive.org/cdx/search/cdx"

GN = ("https://news.google.com/rss/search?q=site:wsj.com/politics"
      "&hl=en-US&gl=US&ceid=US:en")


def get(url, headers=None, timeout=60.0, **kw):
    try:
        return httpx.get(url, headers=headers or H, follow_redirects=True, timeout=timeout, **kw)
    except Exception as exc:  # noqa: BLE001
        return exc


def try_base64(link: str) -> str | None:
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", link)
    if not m:
        return None
    seg = m.group(1)
    for pad in ("", "=", "=="):
        try:
            raw = base64.urlsafe_b64decode(seg + pad)
        except Exception:  # noqa: BLE001
            continue
        if b"AU_yqL" in raw:
            return "新格式（payload 里没有明文 URL，需要 batchexecute）"
        found = re.findall(rb"https?://[\x20-\x7e]{10,200}", raw)
        if found:
            return found[0].decode("utf-8", "replace")
    return None


def try_batchexecute(link: str) -> str | None:
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", link)
    if not m:
        return None
    aid = m.group(1)
    inner = json.dumps(["garturlreq", [["en-US", "US", ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"],
                                       None, None, 1, 1, "US:en", None, 180, None, None, None, None,
                                       None, 0, None, None, [1608992183, 723341000]],
                                      "en-US", "US", 1, [2, 3, 4, 8], 1, 0, "655000234", 0, 0, None, 0],
                          aid])
    body = "f.req=" + httpx.QueryParams({"x": json.dumps([[["Fbv4je", inner, None, "generic"]]])}).get("x")
    try:
        r = httpx.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                       headers={**H, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                       content=body, timeout=60.0, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return f"POST 失败 {type(exc).__name__}"
    urls = re.findall(r"https?://(?:www\.)?wsj\.com/[^\\\"\s]+", r.text)
    return urls[0] if urls else f"HTTP {r.status_code}，响应里没有 wsj.com 链接"


def main() -> int:
    print("=== 1. Google News RSS 单条 item 结构 ===")
    r = get(GN)
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}: {r}")
        return 1
    root = ET.fromstring(r.text)
    items = list(root.iter("item"))
    print(f"  {len(items)} 条，最新：{items[0].findtext('pubDate')}")
    first = items[0]
    for tag in ("title", "link", "guid", "pubDate", "source"):
        el = first.find(tag)
        val = (el.text or "") if el is not None else ""
        print(f"    {tag:<8}: {val[:110]}")
        if tag == "source" and el is not None:
            print(f"             url={el.get('url')}")
    desc = first.findtext("description") or ""
    print(f"    description 前 150: {desc[:150]!r}")
    print(f"    description 里的 href: {re.findall(r'href=\"([^\"]+)', desc)[:2]}")

    print("\n=== 2. 还原真实 URL 的三种办法 ===")
    links = [it.findtext("link") or "" for it in items[:3]]
    for link in links:
        print(f"\n  {link[:90]}")
        print(f"    base64 : {try_base64(link)}")
        rr = get(link, follow_redirects=False)
        if isinstance(rr, Exception):
            print(f"    no-redir: ERR {type(rr).__name__}")
        else:
            print(f"    no-redir: HTTP {rr.status_code} Location={rr.headers.get('location', '—')[:80]}")
        print(f"    batchx : {try_batchexecute(link)}")

    print("\n=== 3. 其它镜像源 ===")
    for url in ["https://plink.anyfeeder.com/wsj",
                "https://plink.anyfeeder.com/wsj/en",
                "https://plink.anyfeeder.com/wsj/politics",
                "https://feedx.net/rss/wsj.xml",
                "https://rsshub.app/wsj/en-us/politics"]:
        rr = get(url)
        if isinstance(rr, Exception):
            print(f"  [ERR] {url}: {type(rr).__name__}")
            continue
        items_n = rr.text.count("<item") + rr.text.count("<entry")
        dates = re.findall(r"<pubDate>([^<]+)</pubDate>", rr.text)[:1]
        print(f"  [{rr.status_code}] items={items_n:<3} {dates} {url}")

    print("\n=== 4. 兜底：Wayback 里 24 小时内新增的 wsj.com 文章数 ===")
    import datetime as dt
    frm = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
    params = {"url": "www.wsj.com/articles*", "output": "json", "from": frm,
              "fl": "timestamp,original", "collapse": "urlkey", "limit": "8"}
    rr = get(CDX, params=params, timeout=120.0)
    if isinstance(rr, Exception):
        print(f"  ERR {type(rr).__name__}")
    elif rr.status_code == 200 and rr.text.strip().startswith("["):
        rows = rr.json()
        print(f"  24h 内归档的 wsj.com/articles 唯一 URL（前 8 条，总数受 limit 限制）:")
        for row in rows[1:]:
            print(f"    {row[0]}  {row[1][:80]}")
    else:
        print(f"  CDX HTTP {getattr(rr, 'status_code', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
