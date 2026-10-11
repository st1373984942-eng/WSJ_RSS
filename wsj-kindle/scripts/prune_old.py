#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除过期的旧新闻，避免素材无限堆积。

保留期写在 config.json 的 retention 段（命令行可以临时覆盖）：

    "retention": { "enabled": true, "article_days": 3, "epub_days": 3, "ledger_days": 30 }

清理三类文件，全部在 --project 目录内，且**逐个校验路径后**才删：

  1. articles/<slug>/       正文与配图；按 frontmatter 的 date: 判断（缺失时退回文件时间）
  2. public/images/<slug>/  配对文章已不在的孤儿图片目录（按目录名对应）
  3. out/*.epub             历史成品；按文件修改时间

另外把 seen.json 里超过 ledger_days 的条目清掉：它们早已出了 48 小时的 sitemap 窗口，
永远不会再被用到，留着只会让账本无限增长。

用法：
    python prune_old.py --project .              # 按 config.json 清理
    python prune_old.py --project . --dry-run    # 只报告会删什么，不动任何文件
    python prune_old.py --project . --days 7     # 临时改成保留 7 天
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: frontmatter 里的发布日期，例如 date: 2026-10-07（也可能是带引号的）
DATE_RE = re.compile(r'^date:\s*"?(\d{4}-\d{2}-\d{2})', re.M)


def dir_size(path: pathlib.Path) -> int:
    """目录内所有文件的字节数（拿不到的文件跳过，不为此中断）。"""
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def article_date(article_dir: pathlib.Path) -> dt.date:
    """这篇新闻的日期：优先 frontmatter 的 date，其次 md 的修改时间，最后目录时间。"""
    fallback: dt.date | None = None
    for md in sorted(article_dir.glob("*.md")):
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        m = DATE_RE.search(head)
        if m:
            try:
                return dt.date.fromisoformat(m.group(1))
            except ValueError:
                pass
        if fallback is None:
            try:
                fallback = dt.date.fromtimestamp(md.stat().st_mtime)
            except OSError:
                pass
    if fallback:
        return fallback
    return dt.date.fromtimestamp(article_dir.stat().st_mtime)


def inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    """确认要删的路径确实位于本项目目录内 —— 删除前必须过这一关。"""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def main() -> int:
    ap = argparse.ArgumentParser(description="清理过期新闻素材")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--days", type=int, default=None, help="文章保留天数（默认取 config）")
    ap.add_argument("--epub-days", type=int, default=None, help="EPUB 保留天数")
    ap.add_argument("--ledger-days", type=int, default=None, help="seen.json 条目保留天数")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不删除")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    try:
        config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"读不到 config.json: {exc}"}, ensure_ascii=False))
        return 1

    rcfg = config.get("retention", {}) or {}
    if rcfg.get("enabled", True) is False and args.days is None:
        print(json.dumps({"ok": True, "skipped": "retention.enabled = false（配置里关掉了清理）"},
                         ensure_ascii=False))
        return 0

    days = args.days if args.days is not None else int(rcfg.get("article_days", 3))
    epub_days = args.epub_days if args.epub_days is not None else int(rcfg.get("epub_days", 3))
    ledger_days = (args.ledger_days if args.ledger_days is not None
                   else int(rcfg.get("ledger_days", 30)))
    # 保底：绝不允许"保留 0 天"这种会把素材全清空的设置
    if days < 1:
        print(json.dumps({"ok": False, "error": f"--days 必须 >= 1（收到 {days}）"},
                         ensure_ascii=False))
        return 1
    epub_days = max(epub_days, 1)
    ledger_days = max(ledger_days, 7)   # 比 48 小时窗口宽裕得多，够安全

    today = dt.date.today()
    cutoff = today - dt.timedelta(days=days)
    epub_cutoff = today - dt.timedelta(days=epub_days)
    ledger_cutoff = (today - dt.timedelta(days=ledger_days)).isoformat()

    articles_dir = project / "articles"
    images_dir = project / "public" / "images"
    out_dir = project / "out"

    removed_articles: list[dict] = []
    kept_articles = 0
    freed = 0

    # ---- 1. 过期的文章目录 ----
    if articles_dir.is_dir():
        for d in sorted(articles_dir.iterdir()):
            if not d.is_dir() or not inside(d, articles_dir):
                continue
            when = article_date(d)
            if when >= cutoff:
                kept_articles += 1
                continue
            size = dir_size(d)
            if not args.dry_run:
                shutil.rmtree(d, ignore_errors=True)
            freed += size
            removed_articles.append({"slug": d.name, "date": when.isoformat(),
                                     "mb": round(size / 1e6, 2)})

    # ---- 2. 文章已不在的孤儿图片目录（顺带把历史遗留一并收掉）----
    removed_images: list[str] = []
    if images_dir.is_dir():
        for d in sorted(images_dir.iterdir()):
            if not d.is_dir() or not inside(d, images_dir):
                continue
            if (articles_dir / d.name).is_dir():
                continue
            size = dir_size(d)
            if not args.dry_run:
                shutil.rmtree(d, ignore_errors=True)
            freed += size
            removed_images.append(d.name)

    # ---- 3. 过期的 EPUB 成品 ----
    removed_epubs: list[str] = []
    if out_dir.is_dir():
        for f in sorted(out_dir.glob("*.epub")):
            if not inside(f, out_dir):
                continue
            try:
                when = dt.date.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
            if when >= epub_cutoff:
                continue
            size = f.stat().st_size
            if not args.dry_run:
                try:
                    f.unlink()
                except OSError:
                    continue
            freed += size
            removed_epubs.append(f.name)

    # ---- 4. 账本里的老条目（超出 sitemap 窗口，不会再被用到）----
    pruned_ledger = 0
    seen_path = project / "data" / "seen.json"
    if seen_path.exists():
        try:
            seen = json.loads(seen_path.read_text(encoding="utf-8-sig"))
            urls = seen.get("urls", {}) or {}
            keep: dict = {}
            for url, meta in urls.items():
                fetched = str((meta or {}).get("fetched") or "")[:10]
                # 日期缺失/异常的条目一律保留，宁可多留也不要误删导致重复推送
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fetched) and fetched < ledger_cutoff:
                    pruned_ledger += 1
                    continue
                keep[url] = meta
            if pruned_ledger and not args.dry_run:
                seen["urls"] = keep
                seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "keep_days": {"articles": days, "epubs": epub_days, "ledger": ledger_days},
        "cutoff": cutoff.isoformat(),
        "removed_articles": len(removed_articles),
        "kept_articles": kept_articles,
        "removed_orphan_image_dirs": len(removed_images),
        "removed_epubs": len(removed_epubs),
        "removed_epub_names": removed_epubs[-5:],
        "pruned_ledger_entries": pruned_ledger,
        "freed_mb": round(freed / 1e6, 2),
        "oldest_removed": sorted(r["date"] for r in removed_articles)[:3],
        "examples": [r["slug"][:34] for r in removed_articles[:5]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
