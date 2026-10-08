#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比「近期 Wayback 快照」与「线上页面」的正文长度：快照里到底是全文还是预览？

读取 coverage_probe.json（probe_coverage.py 产出）里最新的几个文章 URL。

用法：python probe_recent_wayback.py [--n 4]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import httpx
import trafilatura

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
GRE = {"User-Agent": UA, "Referer": "https://www.google.com/",
       "Accept-Language": "zh-CN,zh;q=0.9"}


def text_of(html: str) -> str:
    try:
        return trafilatura.extract(html, include_comments=False) or ""
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    n = 4
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    candidates = [p / "coverage_probe.json" for p in (ROOT, ROOT.parent)]
    probe_file = next((p for p in candidates if p.exists() and p.stat().st_size > 500), None)
    if probe_file is None:
        print("找不到 coverage_probe.json，先跑 probe_coverage.py")
        return 1
    print(f"读取 {probe_file}")
    data = json.loads(probe_file.read_text(encoding="utf-8"))
    entry = data.get("cn.wsj.com/articles*", {})
    urls = entry.get("urls", [])[:n]
    if not urls:
        print("coverage_probe.json 里没有文章 URL，先跑 probe_coverage.py")
        return 1

    for url in urls:
        decoded = url
        print(f"\n=== {decoded[:100]}")
        for label, target, headers in [
            ("wayback", f"https://web.archive.org/web/2/{url}", {"User-Agent": UA}),
            ("live   ", url, GRE),
        ]:
            try:
                r = httpx.get(target, headers=headers, follow_redirects=True, timeout=90.0)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label} [ERR] {type(exc).__name__}: {exc}")
                continue
            txt = text_of(r.text)
            paywall = ("订阅" in r.text and "畅读全文" in r.text) or "subscribe" in r.text.lower()
            print(f"  {label} [{r.status_code}] {len(txt):>5} 字  付费墙标记={paywall}")
            if txt:
                print(f"          {txt[:90]}".replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
