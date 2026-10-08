#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Playwright 真浏览器探测 cn.wsj.com：能否过 401 挑战、拿到文章链接与全文。

用法：python probe_browser.py [--json] [--article URL]
"""
from __future__ import annotations

import json
import re
import sys

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

PAGES = [
    "https://cn.wsj.com/zh-hans",
    "https://cn.wsj.com/zh-hans/news/business",
]
RSS = "https://cn.wsj.com/zh-hans/rss"
ARTICLE_RE = re.compile(r"https?://cn\.wsj\.com/zh-hans/articles/[A-Za-z0-9\-]+")


def main() -> int:
    argv = sys.argv[1:]
    article = None
    if "--article" in argv:
        article = argv[argv.index("--article") + 1]

    report: dict = {"pages": [], "rss": None, "article": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="zh-CN", user_agent=UA,
                                  viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        for url in PAGES:
            entry = {"url": url}
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                html = page.content()
                links = sorted(set(ARTICLE_RE.findall(html)))
                entry |= {
                    "status": resp.status if resp else None,
                    "title": page.title(),
                    "html_bytes": len(html),
                    "article_links": len(links),
                    "sample": links[:5],
                    "has_paywall_kw": bool(re.search(r"订阅|会员|登录|Subscribe", html)),
                }
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["pages"].append(entry)

        # 浏览器已过挑战、拿到 cookie，用同一 context 取官方 RSS
        try:
            resp = ctx.request.get(RSS, timeout=30000)
            body = resp.text()
            report["rss"] = {
                "status": resp.status,
                "bytes": len(body),
                "items": body.count("<item"),
                "head": body[:200].replace("\n", " "),
            }
        except Exception as exc:  # noqa: BLE001
            report["rss"] = {"error": f"{type(exc).__name__}: {exc}"}

        if article:
            try:
                resp = page.goto(article, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                html = page.content()
                text = page.inner_text("body")
                report["article"] = {
                    "url": article,
                    "status": resp.status if resp else None,
                    "title": page.title(),
                    "text_chars": len(text),
                    "has_paywall_kw": bool(re.search(r"订阅|会员|登录以阅读", text)),
                }
                with open("probe_article.html", "w", encoding="utf-8") as fh:
                    fh.write(html)
            except Exception as exc:  # noqa: BLE001
                report["article"] = {"error": f"{type(exc).__name__}: {exc}"}

        browser.close()

    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for e in report["pages"]:
            print(f"[{e.get('status')}] {e['url']}  title={e.get('title')!r} "
                  f"html={e.get('html_bytes')} links={e.get('article_links')} "
                  f"paywall_kw={e.get('has_paywall_kw')} {e.get('error', '')}")
            for s in e.get("sample", [])[:3]:
                print(f"        {s}")
        print("RSS:", report["rss"])
        if report["article"]:
            print("ARTICLE:", report["article"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
