#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 bpc-fetch 抓下来的 Markdown 合成带全文的 RSS 2.0。

bpc-fetch 输出的是 Markdown + 图片，本身不产 RSS；这个脚本就是那个缺失的适配层。

产出（默认都在 wsj-kindle/public 下）：
    feed.xml        RSS 2.0，正文放 <content:encoded>，图片改成绝对 URL
    manifest.json   文章元数据，供 build_epub.py 生成 EPUB
    images/<slug>/  从 articles/ 拷过来的图片，供 feed 里的绝对 URL 引用

用法：
    python build_feed.py                     # 用 config.json
    python build_feed.py --max-items 15
    python build_feed.py --base-url http://192.168.1.10:8080
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import shutil
import sys

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
RFC822 = "%a, %d %b %Y %H:%M:%S +0000"
DEFAULT_AUTHOR = "华尔街日报中文版"

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
SRC_ATTR = re.compile(r"""src=(["'])([^"']+)\1""")
EXTERNAL = re.compile(r"^(?:https?:)?//|^data:|^mailto:")


# ---------------------------------------------------------------- frontmatter
def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 YAML frontmatter。不引入 PyYAML，只处理 key: value 这一层。"""
    m = FRONTMATTER.match(raw.lstrip("\ufeff"))
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t", "-")):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta, m.group(2)


def first(meta: dict, *keys, default=""):
    for k in keys:
        v = meta.get(k)
        if v:
            return v
    return default


def parse_date(value: str, fallback: pathlib.Path) -> dt.datetime:
    value = (value or "").strip()
    if value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    return dt.datetime.fromtimestamp(fallback.stat().st_mtime, dt.timezone.utc)


def rel_image(url: str) -> str:
    """把 markdown 里的图片引用规范化成相对于文章目录的路径。

    bpc-fetch 写的是 `images/img_000_x.jpg`，所以要把开头的 images/ 去掉，
    否则会拼成 images/<slug>/images/xxx 这种重复前缀，图片全裂。
    """
    clean = url.split("?")[0].split("#")[0].strip().lstrip("./")
    clean = re.sub(r"^images/", "", clean)
    return clean.strip("/")


# ---------------------------------------------------------------- 单篇处理
def load_article(md_path: pathlib.Path, base_url: str) -> dict:
    slug = md_path.parent.name
    meta, body = parse_frontmatter(md_path.read_text(encoding="utf-8", errors="replace"))

    src_dir = md_path.parent / "images"
    dest_dir = IMAGES_OUT / slug
    web_images: list[str] = []

    # 先把文章目录下的图片整体搬过去（保留子目录结构）
    if src_dir.is_dir():
        for img in src_dir.rglob("*"):
            if img.is_file():
                target = dest_dir / img.relative_to(src_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img, target)

    # markdown -> <img src="images/xxx.jpg">，再统一改成绝对 URL
    body_html = markdown.markdown(body, extensions=["extra", "sane_lists", "md_in_html"])

    def fix_src(m: re.Match) -> str:
        quote, url = m.group(1), m.group(2)
        if EXTERNAL.match(url):
            return m.group(0)
        clean = rel_image(url)
        web = f"images/{slug}/{clean}"
        if clean and (dest_dir / clean).exists():
            web_images.append(web)
        return f"src={quote}{base_url}/{web}{quote}"

    body_html = SRC_ATTR.sub(fix_src, body_html)

    title = first(meta, "title", "headline", default=md_path.stem)
    url = first(meta, "url", "link", "source", default=f"{base_url}/images/{slug}/")
    try:
        order = int(first(meta, "order", default="0") or 0)
    except ValueError:
        order = 0
    return {
        "slug": slug,
        "title": title,
        "url": url,
        "author": first(meta, "author", "byline", "site", default=DEFAULT_AUTHOR),
        "date": parse_date(first(meta, "date", "published", "pubdate", "updated"), md_path),
        "order": order,
        "paywall": first(meta, "paywall").lower() in ("true", "1", "yes"),
        "html": body_html,
        "images": sorted(set(web_images)),
        "md": str(md_path),
    }


# ---------------------------------------------------------------- 输出
def write_feed(items: list[dict], config: dict, base_url: str, feed_path: pathlib.Path) -> None:
    feed = config["feed"]
    now = dt.datetime.now(dt.timezone.utc).strftime(RFC822)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"'
        ' xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{html.escape(feed['title'])}</title>",
        f"<link>{html.escape(base_url)}/</link>",
        f"<description>{html.escape(feed['description'])}</description>",
        f"<language>{html.escape(feed.get('language', 'zh-cn'))}</language>",
        f'<atom:link href="{html.escape(base_url)}/feed.xml" rel="self" type="application/rss+xml"/>',
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<generator>bpc-fetch + build_feed.py</generator>",
    ]
    for it in items:
        pub = it["date"].strftime(RFC822)
        excerpt = re.sub(r"<[^>]+>", "", it["html"])[:300].strip()
        out += [
            "<item>",
            f"<title>{html.escape(it['title'])}</title>",
            f"<link>{html.escape(it['url'])}</link>",
            # guid 必须稳定：用文章 URL，绝不带时间戳，否则每次推送都算新文章
            f'<guid isPermaLink="true">{html.escape(it["url"])}</guid>',
            f"<pubDate>{pub}</pubDate>",
            f"<author>{html.escape(it['author'])}</author>",
            f"<description>{html.escape(excerpt)}</description>",
            f"<content:encoded><![CDATA[{it['html']}]]></content:encoded>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    feed_path.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="bpc-fetch Markdown -> RSS 2.0")
    ap.add_argument("--project", default=str(ROOT), help="项目根目录（含 config.json）")
    ap.add_argument("--articles", default=None, help="bpc-fetch 输出目录")
    ap.add_argument("--public", default=None, help="RSS/图片输出目录")
    ap.add_argument("--base-url", default=None, help="feed 对外地址，覆盖 config.json")
    ap.add_argument("--max-items", type=int, default=None)
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    global IMAGES_OUT, DEFAULT_AUTHOR
    articles_dir = pathlib.Path(args.articles).resolve() if args.articles else project / "articles"
    public_dir = pathlib.Path(args.public).resolve() if args.public else project / "public"
    IMAGES_OUT = public_dir / "images"

    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    DEFAULT_AUTHOR = config["feed"].get("author", DEFAULT_AUTHOR)
    base_url = (args.base_url or config["feed"]["base_url"]).rstrip("/")
    max_items = args.max_items or int(config["feed"].get("max_items", 25))

    if IMAGES_OUT.exists():
        shutil.rmtree(IMAGES_OUT)
    public_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted(articles_dir.glob("*/*.md"))
    if not md_files:
        print(json.dumps({"ok": False, "error": f"没有找到 Markdown: {articles_dir}/*/*.md"},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    items = [load_article(p, base_url) for p in md_files]
    # 同一 URL 重复抓取时保留最新的一份
    dedup: dict[str, dict] = {}
    for it in sorted(items, key=lambda i: i["date"]):
        dedup[it["url"]] = it
    # 时间倒序；同一天内按 WSJ 自己的 RSS 顺序（order 越小越靠前）
    items = sorted(dedup.values(), key=lambda i: (-i["date"].timestamp(), i["order"]))

    write_feed(items[:max_items], config, base_url, public_dir / "feed.xml")

    manifest = public_dir / "manifest.json"
    manifest.write_text(json.dumps(
        [{**it, "date": it["date"].isoformat()} for it in items],
        ensure_ascii=False, indent=1), encoding="utf-8")

    result = {
        "ok": True,
        "articles": len(items),
        "in_feed": min(len(items), max_items),
        "feed": str(public_dir / "feed.xml"),
        "feed_url": f"{base_url}/feed.xml",
        "images": sum(len(i["images"]) for i in items),
        "latest": items[0]["title"] if items else None,
    }
    print(json.dumps(result, ensure_ascii=args.compact, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
