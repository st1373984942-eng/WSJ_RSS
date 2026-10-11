#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 manifest.json 里的文章合成一本带图带目录的 EPUB。

两套引擎：
  * calibre —— 调用 ebook-convert（排版最好，本机默认）
  * python  —— 纯标准库自己写 EPUB（zip + XHTML + OPF + NCX），
               用于 GitHub Actions / 极简服务器这类没装 Calibre 的环境

中间产物 public/digest.html 同时也是可以直接用浏览器看的"今日合辑"。

用法：
    python build_epub.py                       # 自动选引擎（有 Calibre 就用 Calibre）
    python build_epub.py --engine python       # 强制纯 Python
    python build_epub.py --all                 # 用 feed 里的全部文章
    python build_epub.py --slugs a,b,c
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import shutil
import subprocess
import sys
import uuid
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

CALIBRE_CANDIDATES = [
    pathlib.Path(r"F:\Program Files\Calibre2\ebook-convert.exe"),
    pathlib.Path(r"C:\Program Files\Calibre2\ebook-convert.exe"),
    pathlib.Path(r"C:\Program Files (x86)\Calibre2\ebook-convert.exe"),
]

CSS = """
body { font-family: "Noto Serif CJK SC", "Songti SC", serif; line-height: 1.7; }
h1 { font-size: 1.5em; border-bottom: 2px solid #333; padding-bottom: .3em; }
h2 { font-size: 1.3em; margin-top: 2em; }
img { max-width: 100%; height: auto; }
.meta { color: #666; font-size: .85em; margin-bottom: 1.2em; }
.preview { background: #f4f4f4; border-left: 4px solid #bbb; padding: .6em .9em;
           font-size: .85em; color: #555; margin-top: 1.5em; }
.masthead { text-align: center; margin-bottom: 2em; }
.masthead h1 { border: none; font-size: 2em; }
hr.sep { border: none; border-top: 1px solid #ddd; margin: 2.5em 0; }
.toc { margin: 1.5em 0 2em; }
.toc-title { font-size: 1.3em; font-weight: bold; border-bottom: 1px solid #333;
             padding-bottom: .2em; }
.toc-list { padding-left: 1.4em; }
.toc-list li { margin: .35em 0; line-height: 1.5; }
"""


def find_calibre() -> pathlib.Path | None:
    found = shutil.which("ebook-convert")
    if found:
        return pathlib.Path(found)
    return next((p for p in CALIBRE_CANDIDATES if p.exists()), None)


# --------------------------------------------------------------- 公共：HTML
def item_body(it: dict, base_url: str, for_epub: bool) -> str:
    """把一篇文章渲染成 HTML 片段。for_epub=True 时图片走 EPUB 内部相对路径。"""
    prefix = f"{base_url.rstrip('/')}/images/" if base_url else None
    body = it["html"]
    if prefix:
        body = body.replace(prefix, "../images/" if for_epub else "images/")
    if it.get("paywall"):
        note = ("<p class='preview'>本文为付费内容，此处仅为免费预览段落。</p>"
                if not for_epub else
                "<p class='preview'>Note: paywalled by WSJ — the text above is the free preview.</p>")
        body += note
    return body


def build_html(items: list[dict], title: str, generated: str, base_url: str = "") -> str:
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>",
        "<div class='masthead'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class='meta'>生成于 {html.escape(generated)} · 共 {len(items)} 篇</p>",
        "</div>",
    ]
    # 可见的目录页（新闻标题列表）。
    # 光有 NCX 是不够的：很多阅读器——尤其是 Send to Kindle 转换之后的书——
    # 不会主动弹出 NCX 菜单，但一定会把这一页当正文第一页显示出来。
    # 标题行故意用 <p> 而不是 <h2>：下面 --level1-toc=//h:h2 会把 h2 收进 NCX，
    # 用 h2 的话「目录」自己也会变成一条目录项。
    if items:
        parts.append("<div id='toc' class='toc'>")
        parts.append("<p class='toc-title'>目录</p>")
        parts.append("<ol class='toc-list'>")
        for i, it in enumerate(items, 1):
            parts.append(f"<li><a href='#art{i}'>{html.escape(it['title'])}</a></li>")
        parts.append("</ol></div>")
    for i, it in enumerate(items, 1):
        if i > 1:
            parts.append("<hr class='sep'/>")
        parts.append(f"<h2 id='art{i}'>{html.escape(it['title'])}</h2>")
        meta_bits = [html.escape(str(it.get("date", ""))[:10])]
        if it.get("author"):
            meta_bits.append(html.escape(str(it["author"])))
        parts.append(f"<p class='meta'>{' · '.join(b for b in meta_bits if b)}</p>")
        parts.append(item_body(it, base_url, for_epub=False))
    parts.append("</body></html>")
    return "\n".join(parts)


def patch_epub_guide(epub_path: pathlib.Path, marker: str = "id='toc'") -> str:
    """给 Calibre 产出的 EPUB 补一条 guide 的「目录」指向。

    Calibre 的 HTML 输入只会写 cover，不写 toc；而 Kindle 和不少阅读器据此决定
    菜单里要不要给出「目录」入口。做法：找出含目录标记的那个正文文件，把它写成
    <reference type="toc">，原有条目保留。失败也不影响书本身，返回原因字符串即可。
    """
    import posixpath
    import re
    import tempfile

    if not epub_path.exists():
        return "no_epub"
    tmp = epub_path.with_name(epub_path.name + ".tmp")
    try:
        with zipfile.ZipFile(epub_path) as zin:
            names = zin.namelist()
            opf_name = next((n for n in names if n.endswith(".opf")), None)
            if not opf_name:
                return "no_opf"
            opf = zin.read(opf_name).decode("utf-8", "replace")

            # 找出含目录标记的正文文件（兼容两种引号写法）
            variants = {marker, 'id="toc"', "id=toc"}
            toc_href = ""
            for n in names:
                if not n.lower().endswith((".xhtml", ".html", ".htm")):
                    continue
                body = zin.read(n).decode("utf-8", "replace")
                if any(v in body for v in variants):
                    toc_href = n
                    break
            if not toc_href:
                return "no_toc_page"

            base = posixpath.dirname(opf_name)
            rel = posixpath.relpath(toc_href, base) if base else toc_href
            entry = f'<reference type="toc" title="目录" href="{rel}"/>'

            if re.search(r'<reference[^>]*type="toc"[^>]*/>', opf):
                opf = re.sub(r'<reference[^>]*type="toc"[^>]*/>', entry, opf, count=1)
            elif re.search(r"<guide\s*/>", opf):
                opf = re.sub(r"<guide\s*/>", f"<guide>{entry}</guide>", opf, count=1)
            elif "<guide>" in opf:
                opf = opf.replace("<guide>", f"<guide>\n    {entry}", 1)
            elif "<guide " in opf:  # 带属性（少见）
                opf = re.sub(r"(<guide[^>]*>)", r"\1" + entry, opf, count=1)
            else:
                opf = opf.replace("</package>", f"  <guide>\n    {entry}\n  </guide>\n</package>")

            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for n in names:
                    # 注意：OPF 要写**改过的那一份**，其余按原样复制。
                    # （第一版这里对所有条目都用 zin.read()，等于把刚改好的 OPF 丢了，
                    #   函数还返回 ok —— 静默无效，真实踩过。）
                    data = opf.encode("utf-8") if n == opf_name else zin.read(n)
                    if n == "mimetype":  # 必须第一个、且不压缩
                        zout.writestr(zipfile.ZipInfo("mimetype"), data, zipfile.ZIP_STORED)
                    else:
                        zout.writestr(n, data)
        tmp.replace(epub_path)

        # 自校验：写完再读回来确认 guide 真的有了 toc，避免"返回 ok 其实没改"
        with zipfile.ZipFile(epub_path) as zchk:
            check = zchk.read(opf_name).decode("utf-8", "replace")
        if not re.search(r'<reference[^>]*type="toc"', check):
            return "verify_failed"
        return f"ok:{rel}"
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return f"failed:{type(exc).__name__}"


# --------------------------------------------------------------- 引擎一：Calibre
def epub_via_calibre(calibre: pathlib.Path, html_path: pathlib.Path, epub_path: pathlib.Path,
                     cfg: dict) -> tuple[bool, str]:
    cmd = [
        str(calibre), str(html_path), str(epub_path),
        "--title", cfg["title"],
        "--authors", cfg.get("authors", "WSJ"),
        "--language", cfg.get("language", "zh"),
        "--output-profile", cfg.get("output_profile", "kindle_pw3"),
        "--extra-css", CSS,
        # 目录用每篇文章的标题（h2）。不显式指定的话，Calibre 的自动检测只会给出
        # 一条名为「开始」的目录，标题全都不进去。
        "--level1-toc=//h:h2",
        "--max-levels=1",
        # 每篇作为一章，并且另起一页（阅读器里的章节导航/进度与翻页体验都靠它）
        "--chapter=//h:h2",
        "--page-breaks-before=//h:h2",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = proc.returncode == 0 and epub_path.exists()
    if not ok:
        return False, (proc.stderr or proc.stdout or "")[-500:]
    # Calibre 只写 cover 不写 toc，补上 guide 的目录指向（失败不影响书本身）
    guide = patch_epub_guide(epub_path)
    if not guide.startswith("ok:"):
        print(f"  [warn] guide 目录指向补写结果: {guide}", file=sys.stderr)
    return True, ""


# --------------------------------------------------------------- 引擎二：纯 Python
def _xhtml(body: str) -> str:
    """用 lxml 把片段规整成 XHTML（自闭合空元素、转义裸 &），否则阅读器可能拒收。"""
    try:
        from lxml import html as LH

        frag = LH.fragment_fromstring(body, create_parent="div")
        return LH.tostring(frag, encoding="unicode", method="xml")
    except Exception:  # noqa: BLE001
        fixed = body.replace("<br>", "<br/>").replace("<hr>", "<hr/>")
        return f"<div>{fixed}</div>"


def epub_via_python(items: list[dict], epub_path: pathlib.Path, cfg: dict,
                    public: pathlib.Path, base_url: str = "") -> tuple[bool, str]:
    title = cfg["title"]
    author = cfg.get("authors", "WSJ")
    lang = "zh" if cfg.get("language", "zh").startswith("zh") else cfg.get("language", "en")
    book_id = f"urn:uuid:{uuid.uuid4()}"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 图片：只打包"被选中的这些文章真正引用到"的图片，并且在 EPUB 内部改成纯 ASCII 名。
    # 原因：public/images 里可能还有其它文章的图（整包搬进去会让体积从 15MB 涨到 33MB）；
    # 而中文目录名在 XML 属性里会被转义成 &#x...;，不如直接换成 img0001.jpg 干净。
    img_map: dict[str, str] = {}     # 原相对路径(images/<slug>/<file>) -> EPUB 内路径
    for it in items:
        for rel in it.get("images", []):
            rel = rel.replace("\\", "/")
            if rel and rel not in img_map and (public / rel).is_file():
                ext = pathlib.Path(rel).suffix.lower() or ".jpg"
                img_map[rel] = f"images/img{len(img_map) + 1:04d}{ext}"

    chapters: list[tuple[str, str, str]] = []   # (id, 文件名, 章节标题)
    docs: dict[str, str] = {}
    for i, it in enumerate(items, 1):
        cid = f"c{i:04d}"
        fname = f"text/{cid}.xhtml"
        meta_bits = [html.escape(str(it.get("date", ""))[:10])]
        if it.get("author"):
            meta_bits.append(html.escape(str(it["author"])))
        # RSS 里的正文用的是绝对图片地址，这里换成 EPUB 内部的相对路径
        body = it["html"]
        for rel in it.get("images", []):
            rel = rel.replace("\\", "/")
            if rel in img_map:
                body = body.replace(f"{base_url.rstrip('/')}/{rel}", f"../{img_map[rel]}")
        if it.get("paywall"):
            body += "<p class='preview'>Note: paywalled by WSJ — the text above is the free preview.</p>"
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
            f'<head><meta charset="utf-8"/><title>{html.escape(it["title"])}</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
            '<body>\n'
            f'<h2>{html.escape(it["title"])}</h2>\n'
            f'<p class="meta">{" · ".join(b for b in meta_bits if b)}</p>\n'
            f'{_xhtml(body)}\n'
            "</body></html>\n"
        )
        docs[fname] = doc
        chapters.append((cid, fname, it["title"]))

    # 目录
    nav_items = "\n".join(
        f'<li><a href="{f}">{html.escape(t)}</a></li>' for _, f, t in chapters)
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
        f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body><h1>{html.escape(title)}</h1>'
        f'<p class="toc-title">目录</p><ul>{nav_items}</ul></body></html>\n')
    docs["nav.xhtml"] = nav

    ncx_points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel>'
        f'<content src="{f}"/></navPoint>'
        for i, (_, f, t) in enumerate(chapters, 1))
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_id}"/>'
        '<meta name="dtb:depth" content="1"/></head>\n'
        f'<docTitle><text>{html.escape(title)}</text></docTitle>\n'
        f'<navMap>{ncx_points}</navMap></ncx>\n')
    docs["toc.ncx"] = ncx
    docs["style.css"] = CSS

    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    for cid, fname, _ in chapters:
        manifest.append(f'<item id="{cid}" href="{fname}" '
                        'media-type="application/xhtml+xml"/>')
    for j, mapped in enumerate(img_map.values(), 1):
        ext = pathlib.Path(mapped).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(ext, "image/jpeg")
        manifest.append(f'<item id="img{j:04d}" href="{mapped}" media-type="{mime}"/>')
    # 目录页放在正文最前面：这样阅读器里既能用「目录」菜单跳转（NCX），
    # 也能像普通书页一样翻到目录（spine 里必须收进去，否则只是一个孤立页面）
    spine = '<itemref idref="nav"/>' + "".join(f'<itemref idref="{cid}"/>'
                                               for cid, _, _ in chapters)

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f'<dc:title>{html.escape(title)}</dc:title>\n'
        f'<dc:language>{lang}</dc:language>\n'
        f'<dc:creator opf:role="aut">{html.escape(author)}</dc:creator>\n'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n'
        f'<dc:date>{now}</dc:date>\n'
        '<dc:publisher>wsj-kindle pipeline</dc:publisher>\n'
        '</metadata>\n'
        f'<manifest>\n{"".join(manifest)}\n</manifest>\n'
        f'<spine toc="ncx">\n{spine}\n</spine>\n'
        '<guide><reference type="toc" title="目录" href="nav.xhtml"/></guide>\n'
        '</package>\n')
    docs["content.opf"] = opf

    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>\n')

    try:
        epub_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(epub_path, "w") as z:
            # mimetype 必须是第一个条目且不压缩
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
            for name, content in docs.items():
                z.writestr(f"OEBPS/{name}", content, zipfile.ZIP_DEFLATED)
            for rel, mapped in img_map.items():
                z.write(public / rel, f"OEBPS/{mapped}", zipfile.ZIP_DEFLATED)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


# --------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="manifest.json -> EPUB")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--all", action="store_true", help="用 feed 里的全部文章")
    ap.add_argument("--slugs", default=None, help="逗号分隔的 slug 列表")
    ap.add_argument("--out", default=None, help="输出 epub 路径")
    ap.add_argument("--engine", choices=["auto", "calibre", "python"], default="auto")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    public = project / "public"
    manifest_path = public / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"ok": False, "error": "缺少 public/manifest.json，先跑 build_feed.py"},
                         ensure_ascii=False))
        return 1
    items = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 选取范围：默认只用本轮抓到的（避免每天推送里混进旧文）
    slugs: list[str] | None = None
    if args.slugs:
        slugs = [s for s in args.slugs.split(",") if s]
    elif not args.all:
        run_file = project / "data" / "run_slugs.json"
        if run_file.exists():
            slugs = json.loads(run_file.read_text(encoding="utf-8"))
    if slugs is not None:
        wanted = set(slugs)
        items = [it for it in items if it["slug"] in wanted]
    items = items[:int(config["epub"].get("max_items", 25))]

    if not items:
        print(json.dumps({"ok": False, "error": "没有可合成的文章（本轮抓取为空或都失败了）",
                          "hint": "用 --all 可以拿 feed 里的全部文章；或先跑抓取脚本"},
                         ensure_ascii=False))
        return 1

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = public / "digest.html"
    html_path.write_text(
        build_html(items, config["epub"]["title"], generated,
                   config.get("feed", {}).get("base_url", "")),
        encoding="utf-8")

    out_dir = project / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    epub_path = pathlib.Path(args.out) if args.out else out_dir / \
        f"{config['epub'].get('filename_prefix', 'wsj-cn')}-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.epub"

    calibre = find_calibre()
    engine = args.engine
    if engine == "auto":
        engine = "calibre" if calibre else "python"

    if engine == "calibre" and calibre is None:
        print(json.dumps({"ok": False, "error": "找不到 ebook-convert；用 --engine python 走纯 Python"},
                         ensure_ascii=False))
        return 1

    if engine == "calibre":
        ok, err = epub_via_calibre(calibre, html_path, epub_path, config["epub"])
    else:
        cfg = dict(config["epub"])
        # 纯 Python 引擎下，语言名用 EPUB 认的写法
        lang = config.get("feed", {}).get("language", "zh-cn")
        cfg["language"] = "zh" if lang.lower().startswith("zh") else lang.split("-")[0]
        ok, err = epub_via_python(items, epub_path, cfg, public,
                                  config.get("feed", {}).get("base_url", ""))

    result = {
        "ok": ok,
        "engine": engine,
        "epub": str(epub_path),
        "size_mb": round(epub_path.stat().st_size / 1e6, 2) if epub_path.exists() else 0,
        "articles": len(items),
        "html": str(html_path),
    }
    if not ok:
        result["error"] = err
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if ok else 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
