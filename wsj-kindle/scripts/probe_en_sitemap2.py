#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深挖 WSJ 的 web sitemap 索引：有没有分板块 / 分日的子 sitemap。

用法：python probe_en_sitemap2.py
"""

from __future__ import annotations

import collections
import re
import sys
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.google.com/"}
FROM_WSJ = re.compile(r"https?://(?:www\.)?wsj\.com/[^\s\"'<>\\]+")


def get(url, timeout=90.0):
    try:
        return httpx.get(url, headers=H, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def locs(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text)
    return [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]


def path_hist(urls: list[str], depth: int = 2) -> list[tuple[str, int]]:
    c: collections.Counter[str] = collections.Counter()
    for u in urls:
        m = re.match(r"https?://[^/]+/(.*)", u)
        if not m:
            continue
        segs = [s for s in m.group(1).split("/") if s]
        c["/".join(segs[:depth]) if segs else "(root)"] += 1
    return c.most_common(12)


def main() -> int:
    print("=== 1. web sitemap 索引 ===")
    idx = "https://www.wsj.com/sitemaps/web/wsj/en/sitemap_wsj_en_index.xml"
    r = get(idx)
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}: {r}")
        return 1
    print(f"  [{r.status_code}] {len(r.content)}B")
    kids = locs(r.text)
    print(f"  子 sitemap {len(kids)} 个")
    for k in kids[:25]:
        print(f"    {k}")
    if len(kids) > 25:
        print(f"    ...（还有 {len(kids)-25} 个）")
    # 找分板块/分日的
    sec = [k for k in kids if re.search(r"politic", k, re.I)]
    day = [k for k in kids if re.search(r"\d{4}[-_]\d{2}[-_]\d{2}|latest|recent|news", k, re.I)]
    print(f"\n  含 politic 的: {sec or '无'}")
    print(f"  像按日/最新切分的: {len(day)} 个 -> {day[:8]}")

    print("\n=== 2. Google News sitemap 的 URL 构成 ===")
    r = get("https://www.wsj.com/wsjsitemaps/wsj_google_news.xml")
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}")
        return 1
    all_locs = locs(r.text)
    print(f"  [{r.status_code}] {len(all_locs)} 条 loc，按路径前两段统计：")
    for pat, n in path_hist(all_locs):
        print(f"    {n:>5}  {pat}")
    art = [u for u in all_locs if "/articles/" in u]
    print(f"\n  /articles/ 共 {len(art)} 条，示例：")
    for u in art[:10]:
        print(f"    {u}")

    print("\n=== 3. live_news_sitemap.xml ===")
    r = get("https://www.wsj.com/live_news_sitemap.xml")
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}")
    else:
        ls = locs(r.text)
        print(f"  [{r.status_code}] {len(ls)} 条 loc")
        for u in ls[:6]:
            print(f"    {u}")

    print("\n=== 4. 取最'新鲜'的子 sitemap 看内容形态 ===")
    candidate = None
    for k in kids + ["https://www.wsj.com/wsjsitemaps/wsj_google_news.xml"]:
        if re.search(r"latest|recent|news|\d{4}", k, re.I):
            candidate = k
    for k in (kids[:1] + [candidate] if candidate else kids[:1]):
        r = get(k)
        if isinstance(r, Exception):
            print(f"  [ERR] {k}")
            continue
        ls = locs(r.text)
        arts = [u for u in ls if "/articles/" in u]
        print(f"\n  [{r.status_code}] {k}")
        print(f"    loc={len(ls)}  articles={len(arts)}  lastmod={re.findall(r'<lastmod>([^<]+)', r.text)[:2]}")
        for u in arts[:6]:
            print(f"      · {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
