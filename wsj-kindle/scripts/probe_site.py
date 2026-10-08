#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 cn.wsj.com 各入口的可达性与可用性（诊断用）。

用法：python probe_site.py [--json]
"""
from __future__ import annotations

import json
import sys

import httpx

URLS = [
    "https://cn.wsj.com/zh-hans",
    "https://cn.wsj.com/zh-hans/rss",
    "https://cn.wsj.com/zh-hans/news/business",
    "https://cn.wsj.com/zh-hans/news/types/cn-nlt",
    "https://cn.wsj.com/zh-hans/news/archive/2026/05/14",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://www.wsj.com/xml/rss/3_7031.xml",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def probe(url: str) -> dict:
    try:
        r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"}
    ctype = r.headers.get("content-type", "")
    body = r.text
    is_feed = "<rss" in body[:2000] or "<feed" in body[:2000]
    links = body.count("<item") + body.count("<entry")
    return {
        "url": url,
        "status": r.status_code,
        "final": str(r.url),
        "ctype": ctype.split(";")[0],
        "bytes": len(r.content),
        "is_feed": is_feed,
        "feed_items": links if is_feed else None,
        "articles_in_html": len(set(__import__("re").findall(
            r"https?://cn\.wsj\.com/zh-hans/articles/[A-Za-z0-9\-]+", body))),
        "head": body[:160].replace("\n", " "),
    }


def main() -> int:
    results = [probe(u) for u in URLS]
    if "--json" in sys.argv:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for r in results:
        if "error" in r:
            print(f"[ERR ] {r['url']}\n        {r['error']}")
            continue
        extra = f"feed_items={r['feed_items']}" if r["is_feed"] else f"articles={r['articles_in_html']}"
        print(f"[{r['status']}] {r['url']}\n        {r['ctype']} {r['bytes']}B {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
