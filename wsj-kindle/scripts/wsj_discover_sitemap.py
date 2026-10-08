#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 WSJ 的 sitemap 发现文章，可按板块路径过滤（英文站用）。

为什么不用 RSS：WSJ 的 feeds.a.dj.com 全部冻结在 2025-01-27（实测），
不存在的 feed 名还会返回 S3 的 AccessDenied。而 robots.txt / sitemap.xml
**不在 DataDome 后面**（200），且 Google News sitemap 按规范只含最近 48 小时的文章。

关键发现：WSJ 新版文章 URL 是按板块路径组织的，例如
    https://www.wsj.com/politics/policy/new-cdc-director-...-7f2319be
    https://www.wsj.com/world/middle-east/...
所以"政治板块"= 路径以 politics/ 开头的 URL，过滤是确定性的。

产物与 wsj_discover.py 完全一致（data/discovered.json + urls.txt），下游不用改。

用法：
    python wsj_discover_sitemap.py --project ../wsj-politics
    python wsj_discover_sitemap.py --since 3d --all
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from xml.etree import ElementTree as ET

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

DEFAULT_SITEMAPS = ["https://www.wsj.com/wsjsitemaps/wsj_google_news.xml"]
# 当月 sitemap 作兜底：文件名形如 sitemap_wsj_en_m10_2026.xml
MONTH_SITEMAP = "https://www.wsj.com/sitemaps/web/wsj/en/sitemap_wsj_en_m{M}_{Y}.xml"
HEX_TAIL = re.compile(r"-[0-9a-f]{6,10}$")


def build_headers(referer: str) -> dict:
    return {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def parse_since(value: str) -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    value = (value or "").strip()
    m = re.fullmatch(r"(\d+)([dhm])", value)
    if m:
        n = int(m.group(1))
        unit = {"d": "days", "h": "hours", "m": "minutes"}[m.group(2)]
        return now - dt.timedelta(**{unit: n})
    if value == "today":
        return now - dt.timedelta(days=1)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return now - dt.timedelta(days=2)


def parse_iso(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def parse_sitemap(xml_text: str) -> list[dict]:
    """按 <url> 块解析，注意 news:title / news:publication_date 嵌在 <news:news> 里。"""
    rows: list[dict] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return rows
    for url_el in root.iter():
        if not url_el.tag.endswith("url"):
            continue
        loc = lastmod = title = pub = ""
        for el in url_el.iter():
            tag = el.tag.split("}")[-1]
            text = (el.text or "").strip()
            if not text:
                continue
            if tag == "loc" and not loc:
                loc = text
            elif tag == "lastmod" and not lastmod:
                lastmod = text
            elif tag == "title" and not title:
                title = text
            elif tag == "publication_date" and not pub:
                pub = text
        if loc:
            rows.append({"loc": loc, "lastmod": lastmod, "title": title, "pub": pub})
    return rows


def title_from_slug(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = HEX_TAIL.sub("", slug)
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug[:1].upper() + slug[1:] if slug else url


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


def fetch_sitemap(url: str, headers: dict, timeout: float = 120.0) -> tuple[list[dict], str]:
    try:
        r = httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return [], f"{url}: {type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return [], f"{url}: HTTP {r.status_code}"
    rows = parse_sitemap(r.text)
    return rows, f"{url}: OK, {len(rows)} 个 <url>"


def main() -> int:
    ap = argparse.ArgumentParser(description="sitemap 发现（可按板块路径过滤）")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--since", default=None)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="忽略 seen.json")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    disc = config.get("discover", {})
    since_raw = args.since or disc.get("since", "2d")
    since_dt = parse_since(since_raw)
    referer = disc.get("referer") or "https://www.google.com/"
    prefixes = disc.get("path_prefixes") or ["politics/"]
    headers = build_headers(referer)

    now = dt.datetime.now(dt.timezone.utc)
    sitemaps = list(disc.get("sitemaps") or DEFAULT_SITEMAPS)
    if disc.get("use_month_fallback", True):
        sitemaps.append(MONTH_SITEMAP.format(M=now.month, Y=now.year))

    seen = set() if args.all else load_seen(project / "data" / "seen.json")

    notes: list[str] = []
    rows: list[dict] = []
    for sm in sitemaps:
        got, note = fetch_sitemap(sm, headers)
        notes.append(note)
        rows.extend(got)
        if got and sm in (disc.get("sitemaps") or DEFAULT_SITEMAPS):
            break  # 主 sitemap 有货就不必再拉当月兜底

    # 板块过滤 + 时间过滤 + 去重
    def path_of(u: str) -> str:
        m = re.match(r"https?://[^/]+/(.*)", u)
        return m.group(1) if m else ""

    picked: dict[str, dict] = {}
    for row in rows:
        url = row["loc"]
        if prefixes and not any(path_of(url).startswith(p) for p in prefixes):
            continue
        when = parse_iso(row.get("pub")) or parse_iso(row.get("lastmod"))
        if when and when < since_dt:
            continue
        if url in seen or url in picked:
            continue
        picked[url] = {
            "title": row.get("title") or title_from_slug(url),
            "url": url,
            "date": (when or now).isoformat(),
            "timestamp": (when or now).timestamp(),
            "section": path_of(url).split("/")[0],
            "sitemap": "sitemap",
        }

    fresh = sorted(picked.values(), key=lambda i: i["timestamp"], reverse=True)[:args.limit]
    for idx, it in enumerate(fresh):
        it["order"] = idx

    (project / "data").mkdir(parents=True, exist_ok=True)
    (project / "data" / "discovered.json").write_text(
        json.dumps({"generated": dt.datetime.now().isoformat(), "since": since_raw,
                    "count": len(fresh), "articles": fresh}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (project / "urls.txt").write_text(
        "\n".join(it["url"] for it in fresh) + ("\n" if fresh else ""), encoding="utf-8")

    result = {
        "ok": bool(rows),
        "mode": "sitemap",
        "since": since_raw,
        "path_prefixes": prefixes,
        "sitemap_rows": len(rows),
        "new": len(fresh),
        "urls_file": str(project / "urls.txt"),
        "notes": notes,
        "titles": [f"{it['title'][:56]}" for it in fresh[:6]],
    }
    if not rows:
        result["hint"] = ("sitemap 一个都没拿到。检查：1) 本机系统代理(127.0.0.1:7892)是否在跑，"
                          "直连 www.wsj.com 是不通的；2) Referer 是否仍是 https://www.google.com/")
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
