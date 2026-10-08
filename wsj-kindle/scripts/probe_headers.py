#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试不同 UA / Referer 组合能否拿到 WSJ 中文的 RSS、栏目页和文章全文。

关键假设：BPC 对 85 个站点用 googlebot 伪装拿全文；bpc-fetch 因为只认英文付费墙
标记，从没对 wsj.com 触发过这个降级，所以我们要自己验证。

用法：python probe_headers.py
"""
from __future__ import annotations

import sys

import httpx
import trafilatura

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_INSPECTION = ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36 "
                 "(compatible; Google-InspectionTool/1.0)")

COMBOS = [
    ("chrome", {"User-Agent": UA_CHROME}),
    ("chrome+gref", {"User-Agent": UA_CHROME, "Referer": "https://www.google.com/"}),
    ("googlebot", {"User-Agent": UA_GOOGLEBOT}),
    ("googlebot+gref", {"User-Agent": UA_GOOGLEBOT, "Referer": "https://www.google.com/"}),
    ("inspection+gref", {"User-Agent": UA_INSPECTION, "Referer": "https://www.google.com/"}),
    ("drudgereport+chrome", {"User-Agent": UA_CHROME, "Referer": "https://www.drudgereport.com/"}),
]

ARTICLE = "https://cn.wsj.com/articles/新东方公布疲弱财报后股价大跌-561840f2"
RSS = "https://cn.wsj.com/zh-hans/rss"
SECTION = "https://cn.wsj.com/zh-hans/news/business"

TARGETS = [("RSS", RSS), ("栏目页", SECTION), ("文章", ARTICLE)]


def main() -> int:
    for name, url in TARGETS:
        print(f"\n===== {name}: {url}")
        for combo_name, headers in COMBOS:
            h = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "Accept-Language": "zh-CN,zh;q=0.9", **headers}
            try:
                r = httpx.get(url, headers=h, follow_redirects=True, timeout=45.0)
            except Exception as exc:  # noqa: BLE001
                print(f"  {combo_name:22} ERR {type(exc).__name__}: {exc}")
                continue
            body = r.text
            is_rss = body.lstrip()[:5] in ("<?xml", "<rss", "<feed")
            items = body.count("<item") if is_rss else 0
            text = ""
            if not is_rss:
                try:
                    text = trafilatura.extract(body, include_comments=False) or ""
                except Exception:  # noqa: BLE001
                    text = ""
            paywall = "订阅" in body and "畅读全文" in body
            print(f"  {combo_name:22} [{r.status_code}] {len(r.content):>7}B "
                  f"rss_items={items:<3} text={len(text):>5}字 paywall={paywall}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
