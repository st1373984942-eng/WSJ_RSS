#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对给定文章 URL，比较「Wayback 快照」与「线上页面」能拿到多少正文。

这是判断全文能否自动化的关键实验：wsj.com 在 BPC 里的策略就是 archive 兜底。

用法：python probe_wayback_articles.py [--file urls.txt]
"""
from __future__ import annotations

import sys
import pathlib

import httpx
import trafilatura

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def extract(html: str) -> str:
    try:
        return trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    except Exception:  # noqa: BLE001
        return ""


def measure(url: str, label: str, timeout: float = 90.0) -> dict:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "error": f"{type(exc).__name__}: {exc}"}
    text = extract(r.text)
    return {"label": label, "status": r.status_code, "bytes": len(r.content),
            "text_chars": len(text), "paras": text.count("\n"),
            "preview": text[:80].replace("\n", " ")}


def variants(url: str) -> list[tuple[str, str]]:
    amp = url.replace("/articles/", "/amp/articles/")
    out = [
        (f"{url} [live]", url),
        (f"{url} [wayback /web/2/]", f"https://web.archive.org/web/2/{url}"),
        (f"{url} [wayback 2026]", f"https://web.archive.org/web/2026/{url}"),
    ]
    if amp != url:
        out.append((f"{url} [wayback amp]", f"https://web.archive.org/web/2/{amp}"))
    return out


def main() -> int:
    path = ROOT / "test-urls.txt"
    if "--file" in sys.argv:
        path = pathlib.Path(sys.argv[sys.argv.index("--file") + 1])
    urls = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for url in urls:
        print(f"\n=== {url}")
        for label, probe_url in variants(url):
            res = measure(probe_url, label)
            if "error" in res:
                print(f"    [ERR] {label}: {res['error']}")
            else:
                print(f"    [{res['status']}] {res['text_chars']:>5} 字  {label}")
                if res["text_chars"]:
                    print(f"          {res['preview']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
