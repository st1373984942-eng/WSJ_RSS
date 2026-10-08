#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文 WSJ 政治板块：还有没有别的"列表页"能拿到文章链接？

试：
  A. Googlebot / Google-InspectionTool UA 打 /politics（很多站会给爬虫服务端渲染版）
  B. 各种 wsj.com sitemap / news sitemap
  C. archive.today（archive.ph）有没有 WSJ 政治板块的存档
  D. WSJ 自己的 pf/api 内容接口

用法：python probe_en_gbot.py
"""

from __future__ import annotations

import re
import sys

import httpx

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_INSPECT = ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36 "
              "(compatible; Google-InspectionTool/1.0)")
UA_BING = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"

ART = re.compile(r"https?://(?:www\.)?wsj\.com/articles/[A-Za-z0-9%\-\._]+")

COMBOS = [
    ("googlebot", {"User-Agent": UA_GOOGLEBOT, "Referer": "https://www.google.com/"}),
    ("inspect", {"User-Agent": UA_INSPECT, "Referer": "https://www.google.com/"}),
    ("bingbot", {"User-Agent": UA_BING}),
    ("chrome+gref", {"User-Agent": UA_CHROME, "Referer": "https://www.google.com/"}),
]

TARGETS = [
    ("/politics", "https://www.wsj.com/politics"),
    ("/news/politics", "https://www.wsj.com/news/politics"),
    ("sitemap", "https://www.wsj.com/sitemap.xml"),
    ("news-sitemap", "https://www.wsj.com/sitemap-news.xml"),
    ("robots", "https://www.wsj.com/robots.txt"),
]


def get(url, headers, timeout=60.0):
    try:
        return httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def main() -> int:
    print("=== A/B. UA 变体 × 列表页/sitemap ===")
    for label, url in TARGETS:
        print(f"\n  --- {label}: {url}")
        for cname, headers in COMBOS:
            r = get(url, headers)
            if isinstance(r, Exception):
                print(f"    {cname:<12} ERR {type(r).__name__}")
                continue
            links = ART.findall(r.text)
            extra = ""
            if "sitemap" in url and "<loc>" in r.text:
                extra = f" loc={r.text.count('<loc>')}"
            if "robots" in url and r.status_code == 200:
                extra = " | " + " ".join(
                    ln for ln in r.text.splitlines() if "sitemap" in ln.lower())[:150]
            print(f"    {cname:<12} [{r.status_code}] {len(r.content):>8}B "
                  f"article_links={len(set(links))}{extra}")

    print("\n=== C. archive.today ===")
    for url in ["https://archive.ph/newest/https://www.wsj.com/politics",
                "https://archive.ph/https://www.wsj.com/politics"]:
        r = get(url, {"User-Agent": UA_CHROME})
        if isinstance(r, Exception):
            print(f"  [ERR] {url}: {type(r).__name__}")
            continue
        print(f"  [{r.status_code}] {len(r.content):>8}B links={len(set(ART.findall(r.text)))} {url}")

    print("\n=== D. WSJ pf/api 接口（试探连通性）===")
    for url in ["https://www.wsj.com/pf/api/v3/content/fetch/collection-stories?query=%7B%7D",
                "https://www.wsj.com/pf/api/v3/content/fetch/seo-metadata?query=%7B%7D"]:
        r = get(url, {"User-Agent": UA_CHROME, "Referer": "https://www.google.com/"})
        if isinstance(r, Exception):
            print(f"  [ERR] {url[:60]}: {type(r).__name__}")
            continue
        print(f"  [{r.status_code}] {len(r.content):>7}B {url[:70]}")
        print(f"        {r.text[:110]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
