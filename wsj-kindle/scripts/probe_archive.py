#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查 Wayback Machine 里有多少 cn.wsj.com 的文章快照。

bpc-fetch 对 wsj.com 的策略就是 archive（archive.org 兜底），这个脚本用来判断
中文站的文章是否也能从快照拿到正文。

用法：python probe_archive.py [--limit 30]
"""
from __future__ import annotations

import json
import sys

import httpx

CDX = "http://web.archive.org/cdx/search/cdx"
PATTERNS = [
    "cn.wsj.com/articles*",
    "cn.wsj.com/amp/articles*",
    "cn.wsj.com/zh-hans/news*",
]


def query(pattern: str, limit: int) -> list[dict]:
    params = {
        "url": pattern,
        "output": "json",
        "limit": str(limit),
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "from": "2024",
    }
    try:
        r = httpx.get(CDX, params=params, timeout=90.0)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    if r.status_code != 200:
        return [{"error": f"HTTP {r.status_code}", "body": r.text[:200]}]
    rows = r.json()
    if not rows:
        return []
    header, *data = rows
    out = []
    for row in data:
        rec = dict(zip(header, row))
        rec["snapshot"] = f"https://web.archive.org/web/{rec.get('timestamp')}/{rec.get('original')}"
        out.append(rec)
    return out


def extract_test(snapshot_url: str) -> dict:
    """真的取一份快照并抽正文，验证 archive 兜底是否可用。"""
    try:
        import trafilatura

        r = httpx.get(snapshot_url, timeout=90.0, follow_redirects=True)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        text = trafilatura.extract(r.text, include_comments=False) or ""
        return {"status": 200, "bytes": len(r.content), "text_chars": len(text),
                "preview": text[:120].replace("\n", " ")}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    limit = 30
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    result = {}
    for pattern in PATTERNS:
        rows = query(pattern, limit)
        result[pattern] = {"count": len(rows), "rows": rows[:limit]}
        print(f"### {pattern}: {len(rows)} 条")
        for rec in rows[:8]:
            if "error" in rec:
                print("   ", rec)
            else:
                print(f"    {rec.get('timestamp')}  {rec.get('original')}")
        if rows and "error" not in rows[0] and "articles" in pattern:
            probe = extract_test(rows[0]["snapshot"])
            result[pattern]["extract_test"] = probe
            print(f"    -> 快照正文抽取测试: {probe}")
    with open("archive_probe.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
