#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真浏览器（Edge / Chromium）试过 cn.wsj.com 的 DataDome 401 挑战。

用法：
    python probe_stealth.py --edge --headed      # 真 Edge、有窗口（最可能过挑战）
    python probe_stealth.py --edge               # 真 Edge、无窗口
    python probe_stealth.py                      # Playwright 自带 Chromium

profile 目录会持久化 datadome cookie，第二次运行通常直接是 200。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".browser-profile"
TARGETS = ["https://cn.wsj.com/zh-hans", "https://cn.wsj.com/zh-hans/rss"]
ARTICLE_RE = re.compile(r"https?://cn\.wsj\.com/zh-hans/articles/[^\s\"'<>\\]+")


def main() -> int:
    headed = "--headed" in sys.argv
    channel = "msedge" if "--edge" in sys.argv else None
    profile = ROOT / (".browser-profile-edge" if channel else ".browser-profile")

    report = {"channel": channel or "chromium", "headed": headed, "targets": []}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel=channel,
            headless=not headed,
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for url in TARGETS:
            entry = {"url": url}
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(6000)  # 给 DataDome 的 JS 挑战留时间
                body = page.content()
                links = sorted(set(ARTICLE_RE.findall(body)))
                entry |= {
                    "status": resp.status if resp else None,
                    "title": page.title(),
                    "bytes": len(body),
                    "article_links": len(links),
                    "sample": links[:6],
                    "items": body.count("<item"),
                    "looks_like_challenge": "cmsg" in body[:2000],
                }
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["targets"].append(entry)
            print(f"[{entry.get('status')}] {url} title={entry.get('title')!r} "
                  f"bytes={entry.get('bytes')} links={entry.get('article_links')} "
                  f"challenge={entry.get('looks_like_challenge')} {entry.get('error', '')}")
            for s in entry.get("sample", []):
                print(f"        {s}")
        ctx.close()

    (ROOT / "logs" / "stealth_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
