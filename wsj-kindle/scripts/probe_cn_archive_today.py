#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能不能把"英文那套方法"（sitemap 发现 + archive.today 取文）用到中文站？

两个独立的假设，分别验证：
  A. cn.wsj.com 有没有可访问的 robots.txt / sitemap（英文站的 sitemap 不受 DataDome 保护）
  B. archive.today 上有没有 WSJ 中文文章的全文（英文站靠它才拿到全文）

对照基线：中文站线上带 Google Referer 只能拿到付费墙预览（实测 406~1529 字，
结尾是"订阅《华尔街日报》，畅读全文"）。

用法：python probe_cn_archive_today.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time

import httpx
import trafilatura

WS = pathlib.Path(r"D:\Documents\deepseek-harness\default-workspace")
CN_PROJECT = WS / "wsj-kindle"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
GRE = {"User-Agent": UA, "Referer": "https://www.google.com/",
       "Accept-Language": "zh-CN,zh;q=0.9"}
PLAIN = {"User-Agent": UA}

DISCOVERY_TARGETS = [
    "https://cn.wsj.com/robots.txt",
    "https://cn.wsj.com/sitemap.xml",
    "https://cn.wsj.com/wsj_cn_google_news.xml",
    "https://cn.wsj.com/sitemaps/web/wsj-cn/zh-cn/sitemap_wsj-cn_zh-cn_index.xml",
    "https://cn.wsj.com/sitemaps/web/wsj-cn/zh-hant/sitemap_wsj-cn_zh-hant_index.xml",
]

PAYWALL_CN = ["畅读全文", "訂閱《華爾街日報》", "订阅《华尔街日报》", "請登錄", "请登录"]
DONE_CN = ["Copyright ©", "Dow Jones & Company"]


def get(url, headers=None, timeout=120.0):
    try:
        return httpx.get(url, headers=headers or GRE, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return exc


def text_of(html: str) -> str:
    try:
        return trafilatura.extract(html, include_comments=False) or ""
    except Exception:  # noqa: BLE001
        return ""


def cn_urls(limit: int) -> list[dict]:
    """从中文项目里取真实文章 URL（优先 discovered.json，退回已抓文章的 source 字段）。"""
    disc = CN_PROJECT / "data" / "discovered.json"
    out: list[dict] = []
    if disc.exists():
        try:
            for it in json.loads(disc.read_text(encoding="utf-8")).get("articles", []):
                out.append({"url": it["url"], "title": (it.get("title") or "")[:40]})
        except json.JSONDecodeError:
            pass
    if not out:
        for md in sorted((CN_PROJECT / "articles").glob("*/*.md")):
            m = re.search(r"^source:\s*(\S+)", md.read_text(encoding="utf-8"), re.M)
            if m:
                out.append({"url": m.group(1), "title": md.parent.name[:40]})
    return out[:limit]


def main() -> int:
    print("=== A. cn.wsj.com 的 robots / sitemap 能不能访问 ===")
    for url in DISCOVERY_TARGETS:
        for label, headers in [("带Referer", GRE), ("裸请求 ", PLAIN)]:
            r = get(url, headers=headers)
            if isinstance(r, Exception):
                print(f"  [{label}] ERR {type(r).__name__}  {url}")
                continue
            locs = len(re.findall(r"<loc>", r.text))
            smaps = re.findall(r"Sitemap:\s*(\S+)", r.text) if "robots" in url else []
            note = f"<loc>={locs}" if locs else (f"sitemaps={smaps}" if smaps else "")
            print(f"  [{label}] [{r.status_code}] {len(r.content):>8}B {note:<14} {url}")
        print()

    print("=== B. archive.today 上的 WSJ 中文文章 ===")
    urls = cn_urls(4)
    if not urls:
        print("  中文项目里没有 discovered.json，跳过")
        return 1
    full = 0
    for i, item in enumerate(urls, 1):
        url = item["url"]
        r = get(f"https://archive.ph/newest/{url}", timeout=120.0)
        if isinstance(r, Exception):
            print(f"  [{i}] ERR {type(r).__name__}: {r}")
            time.sleep(4)
            continue
        text = text_of(r.text)
        paywall = any(m in r.text for m in PAYWALL_CN)
        done = any(m in text for m in DONE_CN)
        if len(text) > 600 and not paywall:
            full += 1
        print(f"  [{i}] [{r.status_code}] {len(r.content):>8}B  正文={len(text):>5}字  "
              f"付费墙标记={paywall}  结尾像全文={done}")
        print(f"      {item['title']}")
        if text:
            print(f"      head: {text[:90]}".replace("\n", " "))
            print(f"      tail: {text[-90:]}".replace("\n", " "))
        time.sleep(4)

    print(f"\n  看起来拿到全文的: {full}/{len(urls)}")
    print("\n=== C. 对照：同一批 URL 的 Wayback 快照 ===")
    for item in urls[:2]:
        r = get(f"https://web.archive.org/web/2/{item['url']}", headers=PLAIN)
        if isinstance(r, Exception):
            print(f"  ERR {type(r).__name__}")
            continue
        t = text_of(r.text)
        print(f"  [{r.status_code}] {len(t):>5}字  {item['url'][-46:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
