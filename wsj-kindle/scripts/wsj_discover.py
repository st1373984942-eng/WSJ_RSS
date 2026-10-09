#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发现 WSJ 中文版最新文章（走官方 RSS + Google Referer 绕过 DataDome）。

背景（实测结论，2026-10）：
  * cn.wsj.com 挂了 DataDome，裸请求一律 401（挑战页）。
  * 只要带 `Referer: https://www.google.com/` 就放行：官方 RSS 返回 200 + 36 条。
  * bpc-fetch 自带的 `discover` 拼的是 https://www.{domain}，对 cn.wsj.com 打不到正确主机，
    所以发现环节必须自己来（抓取环节仍然交给 bpc-fetch）。

产物：
  data/discovered.json   本次新发现的文章（含标题/链接/时间）
  urls.txt               给 bpc-fetch batch 用的 URL 列表

用法：
    python wsj_discover.py                     # 用 config.json 的 since，跳过已抓过的
    python wsj_discover.py --since 3d
    python wsj_discover.py --all               # 忽略 seen.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent

FEEDS = [
    "https://cn.wsj.com/zh-hans/rss",
    "https://cn.wsj.com/zh-hant/rss",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def build_headers(referer: str = "https://www.google.com/") -> dict:
    """Google Referer 是过 DataDome 的关键，别去掉。"""
    return {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "application/rss+xml,application/xml,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def parse_since(value: str) -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    value = (value or "").strip()
    if value in ("today", "1d"):
        return now - dt.timedelta(days=1)
    m = re.fullmatch(r"(\d+)d", value)
    if m:
        return now - dt.timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(\d+)h", value)
    if m:
        return now - dt.timedelta(hours=int(m.group(1)))
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return now - dt.timedelta(days=2)


def parse_feed(xml_text: str, source_feed: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"RSS 解析失败: {exc}") from exc

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        when = None
        if pub:
            try:
                when = parsedate_to_datetime(pub)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                when = None
        if not link:
            continue
        items.append({
            "title": title,
            "url": link,
            "date": when.isoformat() if when else "",
            "timestamp": when.timestamp() if when else 0.0,
            "summary": re.sub(r"<[^>]+>", "", desc)[:200],
            "feed": source_feed,
        })
    return items


def load_seen(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if isinstance(data, dict):
        return set(data.get("urls", {}).keys()) | set(data.get("seen", []))
    return set(data)


def main() -> int:
    ap = argparse.ArgumentParser(description="发现 WSJ 中文版最新文章")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--since", default=None, help="today / 2d / 12h / YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="忽略 seen.json，全部返回")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    since_raw = args.since or config.get("discover", {}).get("since", "2d")
    since_dt = parse_since(since_raw)

    seen = set() if args.all else load_seen(project / "data" / "seen.json")

    found: list[dict] = []
    errors: list[str] = []
    got_feed = False
    feeds = config.get("discover", {}).get("feeds") or FEEDS
    referer = config.get("discover", {}).get("referer") or "https://www.google.com/"
    for feed in feeds:
        try:
            r = httpx.get(feed, headers=build_headers(referer), follow_redirects=True, timeout=45.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{feed}: {type(exc).__name__}: {exc}")
            continue
        if r.status_code != 200 or "<item" not in r.text:
            errors.append(f"{feed}: HTTP {r.status_code}，未拿到 RSS"
                          f"（DataDome？确认 Referer=google 是否被改动）")
            continue
        got_feed = True
        items = parse_feed(r.text, feed)
        errors.append(f"{feed}: OK, {len(items)} 条")
        found.extend(items)

    # 去重 + 时间过滤 + 已抓过滤
    uniq: dict[str, dict] = {}
    for it in found:
        uniq.setdefault(it["url"], it)
    fresh = [it for it in uniq.values()
             if (not it["timestamp"] or it["timestamp"] >= since_dt.timestamp())
             and it["url"] not in seen]
    fresh.sort(key=lambda i: i["timestamp"], reverse=True)
    fresh = fresh[:args.limit]
    # order 保留 WSJ 自己的排序，同一天内按它的编辑顺序排版
    for idx, it in enumerate(fresh):
        it["order"] = idx

    (project / "data").mkdir(parents=True, exist_ok=True)
    (project / "data" / "discovered.json").write_text(
        json.dumps({"generated": dt.datetime.now().isoformat(),
                    "since": since_raw, "count": len(fresh), "articles": fresh},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (project / "urls.txt").write_text(
        "\n".join(it["url"] for it in fresh) + ("\n" if fresh else ""), encoding="utf-8")

    result = {
        "ok": got_feed,
        "since": since_raw,
        "feed_total": len(uniq),
        "skipped_seen": len(uniq) - len(fresh) - sum(
            1 for it in uniq.values() if it["timestamp"] and it["timestamp"] < since_dt.timestamp()),
        "new": len(fresh),
        "urls_file": str(project / "urls.txt"),
        "notes": errors,
        "titles": [it["title"] for it in fresh[:8]],
    }
    if not got_feed:
        result["hint"] = ("所有 feed 都拿不到。检查：1) 网络能否访问 cn.wsj.com；"
                          "2) build_headers 里的 Referer 是否仍是 https://www.google.com/")
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if got_feed else 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
