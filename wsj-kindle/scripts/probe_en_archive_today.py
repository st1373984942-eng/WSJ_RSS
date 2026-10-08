#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文政治板块：验证 archive.today 能否作为正文来源，并试"不走代理"的直连。

用法：python probe_en_archive_today.py
"""

from __future__ import annotations

import re
import sys
import time

import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
NEWS_SITEMAP = "https://www.wsj.com/wsjsitemaps/wsj_google_news.xml"

URLS = [
    "https://www.wsj.com/politics/policy/new-cdc-director-is-eager-to-change-its-culture-7f2319be",
    "https://www.wsj.com/politics/elections/democrats-expect-to-win-the-house-heres-their-to-do-list-4e5976d8",
    "https://www.wsj.com/politics/policy/guilfoyle-amex-donor-100-000-0e02cdc3",
    "https://www.wsj.com/politics/elections/michigan-gop-senate-candidate-breaks-with-trump-again-a8c7461c",
]


def words(t: str) -> int:
    return len(re.findall(r"[A-Za-z']+", t))


def try_get(url, headers=None, trust_env=True, timeout=120.0):
    try:
        with httpx.Client(trust_env=trust_env, follow_redirects=True, timeout=timeout) as c:
            return c.get(url, headers=headers or H)
    except Exception as exc:  # noqa: BLE001
        return exc


def main() -> int:
    print("=== A. archive.today 逐篇测试（间隔 4 秒，看限流）===")
    ok = 0
    for i, url in enumerate(URLS):
        r = try_get(f"https://archive.ph/newest/{url}")
        if isinstance(r, Exception):
            print(f"  [{i+1}] ERR {type(r).__name__}: {r}")
            time.sleep(4)
            continue
        text = trafilatura.extract(r.text, include_comments=False) or ""
        w = words(text)
        ratelimited = r.status_code in (429, 503) or "too many requests" in r.text.lower()[:2000]
        if w > 200:
            ok += 1
        print(f"  [{i+1}] [{r.status_code}] {len(r.content):>8}B words={w:<5} "
              f"ratelimited={ratelimited}  {url[-58:]}")
        if text:
            print(f"        head: {text[:130]}".replace("\n", " "))
            print(f"        tail: {text[-120:]}".replace("\n", " "))
        time.sleep(4)
    print(f"\n  有效正文（>200 words）: {ok}/{len(URLS)}")

    print("\n=== B. 不走代理直连 www.wsj.com/politics ===")
    for label, trust in [("直连(无代理)", False), ("走系统代理", True)]:
        r = try_get("https://www.wsj.com/politics", trust_env=trust, timeout=60.0)
        if isinstance(r, Exception):
            print(f"  {label}: ERR {type(r).__name__}: {r}")
            continue
        print(f"  {label}: [{r.status_code}] {len(r.content)}B "
              f"links={len(set(re.findall(r'/politics/[a-z0-9-]+-[0-9a-f]{8}', r.text)))}")

    print("\n=== C. 直连 wsj.com 的 sitemap（确认发现源不依赖代理）===")
    r = try_get(NEWS_SITEMAP, trust_env=False, timeout=90.0)
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}: {r}")
    else:
        pol = len(re.findall(r"<loc>https://www\.wsj\.com/politics/", r.text))
        print(f"  [{r.status_code}] {len(r.content)}B  politics loc={pol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
