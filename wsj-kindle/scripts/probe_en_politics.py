#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文 WSJ 政治板块：确认发现源（RSS）+ 正文可得性（线上 vs Wayback）。

要回答两个问题：
  1. 政治板块有没有可用的 RSS，且不被 DataDome 挡？
  2. 英文站的正文，Wayback 快照是全文还是预览？（中文站实测两者都是预览）

用法：python probe_en_politics.py
"""

from __future__ import annotations

import re
import sys
from xml.etree import ElementTree as ET

import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
GRE = {"User-Agent": UA, "Referer": "https://www.google.com/",
       "Accept-Language": "en-US,en;q=0.9"}
PLAIN = {"User-Agent": UA}

FEEDS = [
    "https://feeds.a.dj.com/rss/RSSPolitics.xml",
    "https://feeds.a.dj.com/rss/RSSUSnews.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://feeds.a.dj.com/rss/RSSOpinion.xml",
    "https://www.wsj.com/xml/rss/3_7031.xml",
    "https://www.wsj.com/politics",
]

PAYWALL_EN = [
    "to read the full story", "continue reading", "subscribe to continue",
    "already a subscriber", "sign in to continue", "wsj subscription",
    "unlock this article", "members only",
]


def get(url: str, headers: dict, timeout: float = 45.0):
    try:
        return httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def main() -> int:
    print("=== 1. 候选发现源 ===")
    politics_urls: list[str] = []
    for url in FEEDS:
        r = get(url, GRE)
        if isinstance(r, Exception):
            print(f"  [ERR] {url}: {type(r).__name__}: {r}")
            continue
        body = r.text
        items = body.count("<item")
        is_feed = items > 0
        print(f"  [{r.status_code}] items={items:<3} {len(r.content):>7}B  {url}")
        if not is_feed:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        got = 0
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            if link and "/articles/" in link:
                politics_urls.append(link)
                if got < 3:
                    print(f"        · {(it.findtext('title') or '')[:60]}"
                          f"  [{it.findtext('pubDate')}]")
                got += 1

    if not politics_urls:
        print("\n没拿到任何文章 URL，后续测试跳过")
        return 1

    print(f"\n=== 2. 正文可得性（取前 3 篇）===")
    for url in politics_urls[:3]:
        print(f"\n  {url}")
        for label, target, headers in [
            ("live   ", url, GRE),
            ("archive", f"https://web.archive.org/web/2/{url}", PLAIN),
        ]:
            r = get(target, headers, timeout=90.0)
            if isinstance(r, Exception):
                print(f"    {label} [ERR] {type(r).__name__}: {r}")
                continue
            try:
                text = trafilatura.extract(r.text, include_comments=False) or ""
            except Exception:  # noqa: BLE001
                text = ""
            low = r.text.lower()
            pay = [m for m in PAYWALL_EN if m in low]
            body_paras = len(re.findall(r"<p[^>]*>", r.text))
            print(f"    {label} [{r.status_code}] text={len(text):>6} 字  <p>={body_paras:<4}"
                  f" paywall={pay[:2]}")
            if text:
                print(f"           {text[:100]}".replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
