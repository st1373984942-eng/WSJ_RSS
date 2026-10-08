#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物自检：feed.xml 是否合法、图片是否是绝对 URL、EPUB 是否是合法 zip。

用法：python selfcheck.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent


def check_feed(public: pathlib.Path) -> dict:
    feed = public / "feed.xml"
    if not feed.exists():
        return {"ok": False, "error": "feed.xml 不存在"}
    try:
        root = ET.fromstring(feed.read_text(encoding="utf-8"))
    except ET.ParseError as exc:
        return {"ok": False, "error": f"XML 非法: {exc}"}
    items = root.findall("./channel/item")
    empty_content = []
    relative_imgs = []
    for it in items:
        title = (it.findtext("title") or "")[:40]
        enc = it.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        if enc is None or not (enc.text or "").strip():
            empty_content.append(title)
        for m in re.finditer(r'src="([^"]+)"', enc.text or "" if enc is not None else ""):
            if not m.group(1).startswith("http"):
                relative_imgs.append(m.group(1))
    return {
        "ok": not empty_content and not relative_imgs,
        "items": len(items),
        "titles": [(it.findtext("title") or "")[:30] for it in items[:3]],
        "guids_unique": len({it.findtext("guid") for it in items}) == len(items),
        "pubdates": [it.findtext("pubDate") for it in items[:2]],
        "empty_content": empty_content,
        "relative_images": relative_imgs[:5],
    }


def check_epub(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"ok": False, "error": f"不存在: {path}"}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            bad = z.testzip()
            has_mimetype = "mimetype" in names
            mimetype = z.read("mimetype").decode() if has_mimetype else ""
            container = "META-INF/container.xml" in names
            opf = [n for n in names if n.endswith(".opf")]
            html = [n for n in names if n.endswith((".html", ".xhtml"))]
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": f"不是合法 zip: {exc}"}
    return {
        "ok": has_mimetype and container and bool(opf) and bad is None,
        "mimetype": mimetype.strip(),
        "entries": len(names),
        "content_docs": len(html),
        "images": len([n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))]),
        "crc_ok": bad is None,
        "size_kb": round(path.stat().st_size / 1024, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="产物自检")
    ap.add_argument("--project", default=str(ROOT))
    args = ap.parse_args()
    project = pathlib.Path(args.project).resolve()
    public = project / "public"
    result = {"feed": check_feed(public)}

    manifest = public / "manifest.json"
    if manifest.exists():
        items = json.loads(manifest.read_text(encoding="utf-8"))
        missing = [img for it in items for img in it.get("images", [])
                   if not (public / img).exists()]
        result["manifest"] = {
            "ok": not missing,
            "items": len(items),
            "latest": items[0]["title"] if items else None,
            "date_parsed": items[0]["date"] if items else None,
            "paywall_flags": sum(1 for it in items if it.get("paywall")),
            "missing_images": missing[:5],
        }
    else:
        result["manifest"] = {"ok": False, "error": "manifest.json 不存在"}

    epubs = sorted((project / "out").glob("*.epub"))
    result["epub"] = check_epub(epubs[-1]) | {"path": str(epubs[-1])} if epubs else \
        {"ok": False, "error": "out/ 下没有 epub"}
    result["ok"] = all(v.get("ok") for k, v in result.items() if isinstance(v, dict))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
