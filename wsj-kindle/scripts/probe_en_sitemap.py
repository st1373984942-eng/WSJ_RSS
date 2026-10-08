#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WSJ 的 sitemap 能给出去重后的近期文章列表吗？

robots.txt / sitemap.xml 没有被 DataDome 保护（200），而 Google News sitemap
按规范只含最近 48 小时的文章——如果它按板块组织或带 section 信息，发现就彻底解决了。

用法：python probe_en_sitemap.py
"""

from __future__ import annotations

import re
import sys
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.google.com/"}


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


def main() -> int:
    print("=== 1. robots.txt 声明的 sitemap ===")
    r = get("https://www.wsj.com/robots.txt")
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}")
        return 1
    declared = [ln.split(":", 1)[1].strip() for ln in r.text.splitlines()
                if ln.lower().startswith("sitemap:")]
    for d in declared:
        print(f"  {d}")

    print("\n=== 2. 主 sitemap.xml（索引？）===")
    r = get("https://www.wsj.com/sitemap.xml")
    if isinstance(r, Exception):
        print(f"  ERR {type(r).__name__}")
        return 1
    children = locs(r.text)
    print(f"  {r.status_code}  {len(children)} 个 <loc>")
    kids = [c for c in children if c.endswith(".xml")]
    for c in kids[:40]:
        print(f"    {c}")
    section_like = [c for c in kids if re.search(r"politic|news|us|world|opinion", c, re.I)]
    print(f"\n  看起来分板块/分频道的子 sitemap：{len(section_like)} 个")
    for c in section_like[:20]:
        print(f"    {c}")

    print("\n=== 3. Google News sitemap（规范上只含 48 小时内文章）===")
    for url in ["https://www.wsj.com/wsjsitemaps/wsj_google_news.xml"] + kids[:2]:
        r = get(url)
        if isinstance(r, Exception):
            print(f"  [ERR] {url}: {type(r).__name__}")
            continue
        items = locs(r.text)
        art = [u for u in items if "/articles/" in u]
        print(f"\n  [{r.status_code}] {url}")
        print(f"    {len(r.content)}B  <loc>={len(items)}  其中文章={len(art)}")
        if art:
            for u in art[:5]:
                print(f"      · {u}")
        # news: 命名空间里的时间与关键词
        for tag in ("publication_date", "keywords", "title", "genres"):
            found = re.findall(rf"<news:{tag}>([^<]+)</news:{tag}>", r.text)[:3]
            if found:
                print(f"    news:{tag} 示例: {found}")
        for m in re.findall(r"<lastmod>([^<]+)</lastmod>", r.text)[:3]:
            print(f"    lastmod 示例: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
