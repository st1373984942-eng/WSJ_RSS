#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估 Wayback 对 cn.wsj.com 的近期覆盖（带重试，CDX 经常 504）。

用法：python probe_coverage.py [--days 10]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time

import httpx

CDX = "http://web.archive.org/cdx/search/cdx"
PATTERNS = [
    "cn.wsj.com/articles*",
    "cn.wsj.com/amp/articles*",
    "cn.wsj.com/zh-hans/news/archive*",
]


def cdx(pattern: str, frm: str, to: str, limit: int = 150, attempts: int = 4) -> list[dict]:
    params = {
        "url": pattern,
        "output": "json",
        "from": frm,
        "to": to,
        "limit": str(limit),
        "collapse": "urlkey",
        "fl": "timestamp,original",
    }
    last = ""
    for n in range(attempts):
        try:
            r = httpx.get(CDX, params=params, timeout=180.0)
            if r.status_code == 200:
                rows = r.json()
                if not rows:
                    return []
                header, *data = rows
                return [dict(zip(header, row)) for row in data]
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(5 * (n + 1))
    return [{"error": last}]


def main() -> int:
    days = 10
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    today = dt.date.today()
    frm = (today - dt.timedelta(days=days)).strftime("%Y%m%d")
    to = today.strftime("%Y%m%d")
    print(f"查询 {frm} ~ {to}\n")

    summary = {}
    for pattern in PATTERNS:
        rows = cdx(pattern, frm, to)
        if rows and "error" in rows[0]:
            print(f"### {pattern}: 失败 {rows[0]['error']}\n")
            summary[pattern] = {"error": rows[0]["error"]}
            continue
        stamps = sorted(r["timestamp"] for r in rows)
        print(f"### {pattern}: {len(rows)} 条")
        if stamps:
            print(f"    最新快照 {stamps[-1]}（{stamps[-1][:4]}-{stamps[-1][4:6]}-{stamps[-1][6:8]}）")
        for r in sorted(rows, key=lambda r: r["timestamp"], reverse=True)[:8]:
            print(f"    {r['timestamp']}  {r['original'][:100]}")
        summary[pattern] = {"count": len(rows), "newest": stamps[-1] if stamps else None,
                            "urls": [r["original"] for r in rows]}
        print()

    with open("coverage_probe.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print("已写入 coverage_probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
