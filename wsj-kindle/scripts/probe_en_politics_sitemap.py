#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确认 WSJ 政治文章是否走 politics/... 路径前缀，以及能否从 sitemap 稳定发现。

如果成立，发现环节就变成：拉 Google News sitemap（48 小时窗口）→ 过滤 politics/ 前缀。
这样既不需要 DataDome 绕行，也不需要 Google News 的加密链接。

用法：python probe_en_politics_sitemap.py
"""

from __future__ import annotations

import collections
import re
import sys
from xml.etree import ElementTree as ET

import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.google.com/"}
NEWS_SITEMAP = "https://www.wsj.com/wsjsitemaps/wsj_google_news.xml"
MONTH_SITEMAP = "https://www.wsj.com/sitemaps/web/wsj/en/sitemap_wsj_en_m10_2026.xml"
N = "{http://www.google.com/schemas/sitemap-news/0.9}"


def get(url, timeout=120.0):
    try:
        return httpx.get(url, headers=H, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def parse_urls(xml_text: str) -> list[dict]:
    """按 <url> 块解析，保留 loc / lastmod / news:title / news:publication_date。"""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return out
    for url_el in root.iter():
        if not url_el.tag.endswith("url"):
            continue
        loc = None
        lastmod = title = pub = ""
        for child in url_el:
            tag = child.tag.split("}")[-1]
            if tag == "loc" and child.text:
                loc = child.text.strip()
            elif tag == "lastmod" and child.text:
                lastmod = child.text.strip()
            elif tag == "title" and child.text:
                title = child.text.strip()
            elif tag == "publication_date" and child.text:
                pub = child.text.strip()
        if loc:
            out.append({"loc": loc, "lastmod": lastmod, "title": title, "pub": pub})
    return out


def seg_hist(urls: list[str], depth: int = 2) -> list[tuple[str, int]]:
    c: collections.Counter[str] = collections.Counter()
    for u in urls:
        m = re.match(r"https?://[^/]+/(.*)", u)
        if not m:
            continue
        segs = [s for s in m.group(1).split("/") if s]
        c["/".join(segs[:depth])] += 1
    return c.most_common(15)


def main() -> int:
    for label, url in [("Google News sitemap (48h)", NEWS_SITEMAP),
                       ("当月 web sitemap", MONTH_SITEMAP)]:
        print(f"\n===== {label}: {url}")
        r = get(url)
        if isinstance(r, Exception):
            print(f"  ERR {type(r).__name__}: {r}")
            continue
        rows = parse_urls(r.text)
        print(f"  [{r.status_code}] {len(r.content)}B  <url> 块 {len(rows)} 个")
        print("  路径前两段分布：")
        for pat, n in seg_hist([x["loc"] for x in rows]):
            mark = "  ← politics" if pat.startswith("politics") else ""
            print(f"    {n:>5}  {pat}{mark}")

        pol = [x for x in rows if re.match(r"https?://[^/]+/politics/", x["loc"])]
        print(f"\n  politics/ 前缀的文章: {len(pol)} 条")
        for x in pol[:8]:
            print(f"    · {x['title'][:62] if x['title'] else '(无标题)'}")
            print(f"      {x['loc']}")
            print(f"      pub={x['pub'] or '—'} lastmod={x['lastmod'] or '—'}")

        # 对前两条做正文可得性测试
        for x in pol[:2]:
            print(f"\n  --- 正文测试: {x['loc']}")
            live = get(x["loc"])
            if not isinstance(live, Exception):
                t = trafilatura.extract(live.text, include_comments=False) or ""
                print(f"      live    [{live.status_code}] {len(t.split()):>5} words")
            arch = get(f"https://web.archive.org/web/2/{x['loc']}", timeout=120.0)
            if isinstance(arch, Exception):
                print(f"      archive ERR {type(arch).__name__}")
                continue
            t = trafilatura.extract(arch.text, include_comments=False) or ""
            sec = re.search(r'"articleSection"\s*:\s*"([^"]+)"', arch.text)
            date = re.search(r'"datePublished"\s*:\s*"([^"]+)"', arch.text)
            print(f"      archive [{arch.status_code}] {len(t.split()):>5} words  "
                  f"section={sec.group(1) if sec else '?'}  datePublished={date.group(1) if date else '?'}")
            if t:
                print(f"          {t[:100]}".replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
