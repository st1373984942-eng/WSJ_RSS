#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文 WSJ 政治板块：验证"Wayback 板块页做发现 + 文章快照做正文"这条路。

思路：
  1. 取 https://www.wsj.com/politics 的最新 Wayback 快照；
  2. 从快照 HTML 里解析出 /articles/ 链接（这些天然就是政治板块的文章）；
  3. 再取每篇文章自己的快照，抽出正文，看是不是全文。

用法：python probe_en_wayback.py
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import sys
import time

import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA}
CDX = "http://web.archive.org/cdx/search/cdx"
SECTION_PAGES = ["https://www.wsj.com/politics", "https://www.wsj.com/news/politics"]
ART_RE = re.compile(r"https?://(?:www\.)?wsj\.com/articles/[A-Za-z0-9%\-\._]+")
PROMO = re.compile(r"wsj\.com/articles/(?:[a-z0-9\-]*promo|.*-promo-)")


def get(url: str, timeout: float = 120.0, attempts: int = 3):
    last = None
    for n in range(attempts):
        try:
            r = httpx.get(url, headers=H, follow_redirects=True, timeout=timeout)
            if r.status_code in (429, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(4 * (n + 1))
                continue
            return r
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(3 * (n + 1))
    return RuntimeError(last)


def newest_snapshot(page: str) -> tuple[str | None, str | None]:
    params = {"url": page, "output": "json", "limit": "-5", "fl": "timestamp,original,statuscode",
              "filter": "statuscode:200"}
    r = get(CDX + "?" + "&".join(f"{k}={v}" for k, v in params.items()))
    if isinstance(r, Exception) or r.status_code != 200:
        return None, None
    try:
        rows = r.json()
    except ValueError:
        return None, None
    if not rows or len(rows) < 2:
        return None, None
    ts, original = rows[-1][0], rows[-1][1]
    return ts, f"https://web.archive.org/web/{ts}id_/{original}"


def main() -> int:
    print("=== 1. 板块页的最新快照 ===")
    links: list[str] = []
    for page in SECTION_PAGES:
        ts, snap = newest_snapshot(page)
        if not snap:
            print(f"  {page}: 没有可用快照")
            continue
        age = (dt.datetime.now() - dt.datetime.strptime(ts, "%Y%m%d%H%M%S")).days
        print(f"  {page}\n    最新快照 {ts}（{age} 天前）")
        r = get(snap)
        if isinstance(r, Exception):
            print(f"    取快照失败: {r}")
            continue
        found = sorted(set(ART_RE.findall(r.text)))
        print(f"    页面 {len(r.content)}B，解析出 {len(found)} 个文章链接")
        for u in found[:5]:
            print(f"      · {u}")
        links.extend(found)

    links = [u for u in dict.fromkeys(links) if not PROMO.search(u)]
    if not links:
        print("\n没有拿到文章链接")
        return 1

    print(f"\n=== 2. 这些文章的快照里有多少正文（取前 4 篇）===")
    for url in links[:4]:
        params = {"url": url, "output": "json", "limit": "-1", "fl": "timestamp,statuscode"}
        # 用 /web/2/ 直接拿最新快照
        r = get(f"https://web.archive.org/web/2/{url}")
        if isinstance(r, Exception):
            print(f"  [ERR] {url[:70]}: {r}")
            continue
        payload = r.text
        if "/web/2/" in str(r.url) and "not archived" in payload.lower():
            print(f"  [无快照] {url[:70]}")
            continue
        try:
            text = trafilatura.extract(payload, include_comments=False) or ""
        except Exception:  # noqa: BLE001
            text = ""
        words = len(re.findall(r"[A-Za-z']+", text))
        snap_ts = re.search(r"/web/(\d{14})", str(r.url))
        sec_m = re.search(r'"articleSection"\s*:\s*"([^"]+)"', payload)
        print(f"  [{r.status_code}] {words:>5} words  {len(text):>6} chars  "
              f"section={sec_m.group(1) if sec_m else '?'}  snap={snap_ts.group(1) if snap_ts else '?'}")
        print(f"      {url[:100]}")
        if text:
            print(f"      {text[:110]}".replace("\n", " "))
            print(f"      ...tail: {text[-110:]}".replace("\n", " "))
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
