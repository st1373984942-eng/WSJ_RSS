#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文 WSJ 政治板块：找一个能用的"发现"来源。

已知：feeds.a.dj.com 里 WorldNews/Opinion 能返回 200，但内容停在 2025-01；
www.wsj.com 的线上页面和 xml/rss 都 401（Google Referer 对英文站无效）。

这里试四条路：
  A. feeds.a.dj.com 的其它 feed 名（也许 Politics 只是文件名不同）
  B. Google News RSS（site: 查询）
  C. Bing News RSS
  D. Wayback：WSJ 政治板块页 /politics 的快照新鲜度 + 能不能解析出文章链接

用法：python probe_en_discover.py
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
CDX = "http://web.archive.org/cdx/search/cdx"

FEED_NAMES = [
    "RSSPolitics.xml", "RSSPoliticsMain.xml", "RSSUSnews.xml", "RSSUSNews.xml",
    "RSSWorldNews.xml", "RSSOpinion.xml", "WSJcomUSBusiness.xml", "RSSMarketsMain.xml",
    "RSSWSJD.xml", "RSSLifestyle.xml", "RSSRealEstate.xml", "RSSAutos.xml",
    "RSSHeardOnTheStreet.xml", "RSSJournalReports.xml", "RSSUSBusiness.xml",
]


def get(url: str, headers: dict | None = None, timeout: float = 45.0, **kw):
    try:
        return httpx.get(url, headers=headers or H, follow_redirects=True, timeout=timeout, **kw)
    except Exception as exc:  # noqa: BLE001
        return exc


def newest_pubdate(xml_text: str) -> str:
    dates = re.findall(r"<pubDate>([^<]+)</pubDate>", xml_text)
    return dates[0] if dates else "—"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    section("A. feeds.a.dj.com 全部候选 feed")
    for name in FEED_NAMES:
        url = f"https://feeds.a.dj.com/rss/{name}"
        r = get(url)
        if isinstance(r, Exception):
            print(f"  [ERR] {name}: {type(r).__name__}")
            continue
        items = r.text.count("<item")
        note = f"items={items:<3} 最新={newest_pubdate(r.text)}" if items else f"{r.text[:60]!r}"
        print(f"  [{r.status_code}] {name:<28} {note}")

    section("B. Google News RSS")
    gq = "https://news.google.com/rss/search?q=site:wsj.com/politics&hl=en-US&gl=US&ceid=US:en"
    r = get(gq)
    if isinstance(r, Exception):
        print(f"  [ERR] {type(r).__name__}: {r}")
    else:
        print(f"  [{r.status_code}] items={r.text.count('<item')} bytes={len(r.content)}")
        try:
            root = ET.fromstring(r.text)
            for it in list(root.iter("item"))[:3]:
                link = (it.findtext("link") or "")
                print(f"    · {(it.findtext('title') or '')[:58]}")
                print(f"      {it.findtext('pubDate')}")
                print(f"      link: {link[:120]}")
        except ET.ParseError as exc:
            print(f"  解析失败: {exc}")

    section("C. Bing News RSS")
    for label, url in [
        ("bing news", "https://www.bing.com/news/search?q=site%3Awsj.com%2Fpolitics&format=RSS"),
        ("bing web ", "https://www.bing.com/search?q=site%3Awsj.com+section%3Apolitics&format=rss"),
    ]:
        r = get(url)
        if isinstance(r, Exception):
            print(f"  [{label}] ERR {type(r).__name__}: {r}")
            continue
        print(f"  [{label}] [{r.status_code}] items={r.text.count('<item')} bytes={len(r.content)}")
        try:
            root = ET.fromstring(r.text)
            for it in list(root.iter("item"))[:3]:
                print(f"      · {(it.findtext('title') or '')[:58]} | {(it.findtext('link') or '')[:90]}")
        except ET.ParseError:
            print(f"      非 XML，前 100 字: {r.text[:100]!r}")

    section("D. Wayback 里的政治板块页")
    today = dt.date.today()
    frm = (today - dt.timedelta(days=14)).strftime("%Y%m%d")
    for pattern in ["www.wsj.com/politics", "wsj.com/politics*", "www.wsj.com/news/politics*"]:
        params = {"url": pattern, "output": "json", "from": frm, "limit": "20",
                  "collapse": "timestamp:8", "fl": "timestamp,original,statuscode"}
        r = get(CDX, params=params, timeout=120.0)
        if isinstance(r, Exception):
            print(f"  {pattern}: ERR {type(r).__name__}")
            continue
        if r.status_code != 200:
            print(f"  {pattern}: HTTP {r.status_code}")
            continue
        rows = r.json() if r.text.strip().startswith("[") else []
        print(f"  {pattern}: {max(0, len(rows)-1)} 条")
        for row in rows[1:6]:
            print(f"      {row[0]}  {row[1][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
