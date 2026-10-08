#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看看 cn.wsj.com 的 Google News sitemap 里到底有什么（英文站那套发现能否照搬）。

robots.txt 实测声明了 https://cn.wsj.com/wsj_cn_google_news.xml（200，61 个 <loc>）。

用法：python probe_cn_sitemap.py
"""

from __future__ import annotations

import collections
import re
import sys
from xml.etree import ElementTree as ET

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
# 注意：裸请求（无 Referer）就能拿到，说明 sitemap 不在 DataDome 后面
H = {"User-Agent": UA}

TARGETS = [
    ("Google News sitemap", "https://cn.wsj.com/wsj_cn_google_news.xml"),
    ("主 sitemap", "https://cn.wsj.com/sitemap.xml"),
    ("zh-cn 索引", "https://cn.wsj.com/sitemaps/web/wsj-cn/zh-cn/sitemap_wsj-cn_zh-cn_index.xml"),
]


def parse_rows(xml_text: str) -> list[dict]:
    rows: list[dict] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return rows
    for url_el in root.iter():
        if not url_el.tag.endswith("url"):
            continue
        rec = {"loc": "", "lastmod": "", "title": "", "pub": ""}
        for el in url_el.iter():
            tag = el.tag.split("}")[-1]
            text = (el.text or "").strip()
            if not text:
                continue
            if tag == "loc" and not rec["loc"]:
                rec["loc"] = text
            elif tag == "lastmod" and not rec["lastmod"]:
                rec["lastmod"] = text
            elif tag == "title" and not rec["title"]:
                rec["title"] = text
            elif tag == "publication_date" and not rec["pub"]:
                rec["pub"] = text
        if rec["loc"]:
            rows.append(rec)
    return rows


def main() -> int:
    for label, url in TARGETS:
        try:
            r = httpx.get(url, headers=H, follow_redirects=True, timeout=90.0)
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== {label}: ERR {type(exc).__name__}: {exc}")
            continue
        rows = parse_rows(r.text)
        locs = re.findall(r"<loc>\s*([^<\s]+)", r.text)
        print(f"\n=== {label}  [{r.status_code}] {len(r.content)}B  <loc>={len(locs)}  <url>块={len(rows)}")
        if not rows:
            for u in locs[:8]:
                print(f"    {u}")
            continue

        hist: collections.Counter[str] = collections.Counter()
        for rec in rows:
            m = re.match(r"https?://[^/]+/(.*)", rec["loc"])
            segs = [s for s in (m.group(1) if m else "").split("/") if s]
            hist["/".join(segs[:2])] += 1
        print("  路径前两段分布：")
        for pat, n in hist.most_common(10):
            print(f"    {n:>4}  {pat}")

        arts = [x for x in rows if "/articles/" in x["loc"]]
        print(f"  /articles/ 共 {len(arts)} 条；示例（含标题与时间）：")
        for x in arts[:5]:
            print(f"    · {x['title'][:44] or '(无标题)'}")
            print(f"      {x['loc'][:100]}")
            print(f"      pub={x['pub'] or '—'}  lastmod={x['lastmod'] or '—'}")
        dated = [x for x in rows if x["pub"] or x["lastmod"]]
        print(f"  带时间戳的: {len(dated)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
