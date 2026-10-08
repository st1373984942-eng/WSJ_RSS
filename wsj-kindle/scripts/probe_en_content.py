#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英文政治板块：正文还能从哪儿拿？

已知：新式 politics/... URL 线上 401、Wayback 无快照；老式 /articles/... 有快照但很短。
这里试：
  A. archive.today（BPC 对 wsj.com 的 archive 兜底用的就是它这一类）
  B. 老式 /articles/ 政治文章的快照到底多长、结尾像不像全文
  C. Wayback "Save Page Now" 现抓一篇，看 IA 的爬虫能拿到什么

用法：python probe_en_content.py
"""

from __future__ import annotations

import re
import sys
import time

import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.google.com/"}
NEW_URL = ("https://www.wsj.com/politics/policy/"
           "new-cdc-director-is-eager-to-change-its-culture-7f2319be")
OLD_URLS = [
    "https://www.wsj.com/articles/democrats-midterm-election-congress-data-4a845ccf",
    "https://www.wsj.com/articles/even-chinas-property-stalwart-isnt-immune-from-the-crisis-19799863",
]


def get(url, headers=None, timeout=120.0, follow=True):
    try:
        return httpx.get(url, headers=headers or H, follow_redirects=follow, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def words(t: str) -> int:
    return len(re.findall(r"[A-Za-z']+", t))


def main() -> int:
    print("=== A. archive.today 对新式政治 URL ===")
    for url in [f"https://archive.ph/newest/{NEW_URL}", f"https://archive.ph/{NEW_URL}"]:
        r = get(url, timeout=90.0)
        if isinstance(r, Exception):
            print(f"  [ERR] {type(r).__name__}: {r}  <- {url[:60]}")
            continue
        text = trafilatura.extract(r.text, include_comments=False) or ""
        cap = "captcha" in r.text.lower() or "verify" in r.text.lower()[:4000]
        print(f"  [{r.status_code}] {len(r.content):>8}B words={words(text):<5} captcha={cap}")
        print(f"      {url[:100]}")
        if text:
            print(f"      {text[:110]}".replace("\n", " "))
        time.sleep(2)

    print("\n=== B. 老式 /articles/ 政治文章快照长度 ===")
    for url in OLD_URLS:
        r = get(f"https://web.archive.org/web/2/{url}")
        if isinstance(r, Exception):
            print(f"  [ERR] {url[:70]}")
            continue
        text = trafilatura.extract(r.text, include_comments=False) or ""
        tail = text[-90:].replace("\n", " ") if text else ""
        head = text[:90].replace("\n", " ") if text else ""
        print(f"  [{r.status_code}] words={words(text):<5} {url[-60:]}")
        print(f"      head: {head}")
        print(f"      tail: {tail}")
        time.sleep(1)

    print("\n=== C. Save Page Now 现抓一篇（看 IA 爬虫拿到什么）===")
    target = NEW_URL
    r = get(f"https://web.archive.org/save/{target}", timeout=180.0)
    if isinstance(r, Exception):
        print(f"  [ERR] {type(r).__name__}: {r}")
    else:
        text = trafilatura.extract(r.text, include_comments=False) or ""
        print(f"  [{r.status_code}] 最终 URL={str(r.url)[:110]}")
        print(f"    words={words(text)}")
        if text:
            print(f"    {text[:140]}".replace("\n", " "))
        time.sleep(3)
        rr = get(f"https://web.archive.org/web/2/{target}")
        if not isinstance(rr, Exception):
            t2 = trafilatura.extract(rr.text, include_comments=False) or ""
            print(f"  回读快照: [{rr.status_code}] words={words(t2)}")
            if t2:
                print(f"    {t2[:140]}".replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
