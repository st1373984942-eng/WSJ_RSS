#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 bpc-fetch 抓取 WSJ 中文文章，并补上它缺失的两块：

1. **Referer 修补**：cn.wsj.com 挂的是 DataDome，裸请求 401；带
   `Referer: https://www.google.com/` 才放行。bpc-fetch 对 wsj.com 恰好会加这个
   referer（看到 `referer_custom: drudgereport` 没被解析，于是走了"无 UA 时补 Google referer"
   的分支），但为了确定性这里显式加固。
2. **订阅 Cookie 注入**：bpc-fetch 不支持自定义 cookie，这里 patch 掉它的
   `build_headers`，于是 只要你在 config.json 里填了 cookie，就能直接拿到全文。

同时 patch 它的付费墙检测（只认英文标记），让它认得中文的「畅读全文」提示。

用法：
    python wsj_fetch.py                    # 读 urls.txt
    python wsj_fetch.py --force            # 忽略 seen.json
    python wsj_fetch.py --urls-file x.txt --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# 在导入 bpc_fetch 之后立刻打补丁（必须在调用前完成）
# --------------------------------------------------------------------------
import bpc_fetch.strategy as strategy  # noqa: E402
import bpc_fetch.extract as extract  # noqa: E402
from bpc_fetch.cli import _slugify  # noqa: E402
from bpc_fetch.sites import get_sites_map, domain_from_url, SITES_JS_DEFAULT  # noqa: E402

_orig_build_headers = strategy.build_headers
_ORIG_PAYWALL = strategy._is_paywalled
_COOKIE = ""
_REFERER = "https://www.google.com/"

# 中文付费墙标记：bpc-fetch 只认英文的，所以它会把「预览」误判成抓取成功。
# 简繁两版文案都要覆盖，否则繁体版本会漏检、样板文字留在正文里。
ZH_PAYWALL_MARKERS = [
    "畅读全文", "暢讀全文",
    "订阅《华尔街日报》", "訂閱《華爾街日報》",
    "我们注意到您已经是订阅用户", "我們注意到您已經是訂閱用戶",
    "This copy is for your personal, non-commercial use only",
    "Please enable JS and disable any ad blocker",
]


def _patched_build_headers(site_strategy):
    headers = _orig_build_headers(site_strategy)
    headers.setdefault("Referer", _REFERER)
    headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    if _COOKIE:
        headers["Cookie"] = _COOKIE
    return headers


def _patched_is_paywalled(html: str) -> bool:
    if any(m in html for m in ZH_PAYWALL_MARKERS):
        return True
    return _ORIG_PAYWALL(html)


strategy.build_headers = _patched_build_headers


def set_archive_fallback(enabled: bool) -> None:
    """是否要「认出中文付费墙 → 再试 googlebot / archive.org」的兜底链。

    实测（2026-10）：Wayback 快照与线上页面正文长度完全一致，都是付费墙预览，
    所以默认关闭，省掉每篇两次无用请求；只有当你确认某站 archive 有全文时才开。
    """
    strategy._is_paywalled = _patched_is_paywalled if enabled else _ORIG_PAYWALL

# 抓下来之后要清掉的付费墙/版权样板行（简繁都覆盖）
BOILERPLATE = [
    r"(订阅《华尔街日报》|訂閱《華爾街日報》)，?",
    r"(畅读全文|暢讀全文)",
    r"\[(订阅|訂閱)\]\(https?://subscribe\.wsj\.com[^)]*\)",
    r"(我们注意到您已经是订阅用户|我們注意到您已經是訂閱用戶)，?\[(请登录|請登錄)\]\([^)]*\)",
    r"Copyright ©\d{4} Dow Jones & Company.*",
    r"Please enable JS and disable any ad blocker",
    r"This copy is for your personal, non-commercial use only\..*",
    r"Distribution and use of this material are governed by our Subscriber Agreement.*",
]
BOILERPLATE_RE = [re.compile(rf"^\s*{p}\s*$") for p in BOILERPLATE]

PAYWALL_NOTE = "> 注：此为付费内容，以上仅为免费预览段落（点链接可在 WSJ 客户端续读全文）。"
_PAYWALL_NOTE = PAYWALL_NOTE


def clean_markdown(md: str, note: str | None = None) -> tuple[str, bool]:
    """剥掉付费墙样板，返回 (干净正文, 是否付费墙预览)。"""
    paywalled = any(m in md for m in ZH_PAYWALL_MARKERS)
    kept: list[str] = []
    for line in md.splitlines():
        if any(rx.match(line) for rx in BOILERPLATE_RE):
            continue
        kept.append(line)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n"
    note = _PAYWALL_NOTE if note is None else note
    if paywalled and note:
        out = out.rstrip() + "\n\n" + note + "\n"
    return out, paywalled


def enrich_frontmatter(md: str, extra: dict[str, str], override: tuple[str, ...] = ()) -> str:
    """往 bpc-fetch 写的 frontmatter 里补/改字段（date / paywall）。"""
    if not md.startswith("---\n"):
        return md
    end = md.find("\n---", 4)
    if end < 0:
        return md
    head, rest = md[4:end], md[end:]
    lines: list[str] = []
    present: set[str] = set()
    for line in head.splitlines():
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key:
            present.add(key)
        if key in override and extra.get(key):
            lines.append(f"{key}: {extra[key]}")
        else:
            lines.append(line)
    lines += [f"{k}: {v}" for k, v in extra.items() if k not in present and v]
    return "---\n" + "\n".join(lines).rstrip() + rest


def load_seen(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"urls": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"urls": {}}
    data.setdefault("urls", {})
    return data


async def fetch_one(url: str, sites: dict, out_dir: pathlib.Path, no_images: bool,
                    pub_date: str = "", order: int = 0) -> dict:
    from bpc_fetch.extract import extract_article, article_to_markdown, download_images

    domain = domain_from_url(url)
    site_strategy = sites.get(domain)
    try:
        html, status, dom_result = await strategy.fetch_with_retries(url, site_strategy)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    if status != 200:
        return {"ok": False, "url": url, "error": f"HTTP {status}"}

    article = extract.extract_article(html, url, dom_result=dom_result)
    if not article.get("text"):
        return {"ok": False, "url": url, "error": "extraction_failed"}

    slug = _slugify(article["title"] or domain)
    images_dir = out_dir / slug / "images"
    saved = []
    if not no_images and article.get("images"):
        saved = await extract.download_images(article["images"], images_dir)

    md = extract.article_to_markdown(article, images_dir="images")
    md, paywalled = clean_markdown(md)
    # 用 RSS 的发布时间覆盖 trafilatura 猜的日期，保证排序正确
    md = enrich_frontmatter(md,
                            {"date": pub_date[:10] if pub_date else article.get("date", ""),
                             "order": str(order),
                             "paywall": "true" if paywalled else ""},
                            override=("date", "order"))

    md_path = out_dir / slug / f"{slug}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")

    body_chars = len(re.sub(r"[#>*\-\s]", "", article["text"]))
    return {
        "ok": True,
        "url": url,
        "title": article["title"],
        "slug": slug,
        "path": str(md_path),
        "images": len(saved),
        "text_chars": body_chars,
        "paywall_preview": paywalled,
    }


async def run(args) -> dict:
    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    global _COOKIE, _REFERER, _PAYWALL_NOTE
    _COOKIE = (args.cookie if args.cookie is not None else config.get("fetch", {}).get("cookie", "")) or ""
    _REFERER = config.get("fetch", {}).get("referer") or "https://www.google.com/"
    _PAYWALL_NOTE = config.get("fetch", {}).get("paywall_note", PAYWALL_NOTE)
    set_archive_fallback(bool(config.get("fetch", {}).get("try_archive", False)))

    urls_file = pathlib.Path(args.urls_file) if args.urls_file else project / "urls.txt"
    if not urls_file.exists():
        return {"ok": False, "error": f"找不到 URL 列表: {urls_file}（先跑 wsj_discover.py）"}
    urls = [u.strip() for u in urls_file.read_text(encoding="utf-8").splitlines()
            if u.strip() and not u.startswith("#")]

    seen_path = project / "data" / "seen.json"
    seen = load_seen(seen_path)
    if not args.force:
        urls = [u for u in urls if u not in seen["urls"]]
    if args.limit:
        urls = urls[:args.limit]
    if not urls:
        return {"ok": True, "new": 0, "note": "没有新 URL（都已抓过，或用 --force 强制重抓）"}

    sites = get_sites_map(SITES_JS_DEFAULT)
    out_dir = pathlib.Path(args.out_dir) if args.out_dir else project / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 从发现结果里取发布时间和排序，写进 frontmatter
    pub_dates: dict[str, str] = {}
    pub_order: dict[str, int] = {}
    discovered = project / "data" / "discovered.json"
    if discovered.exists():
        try:
            for it in json.loads(discovered.read_text(encoding="utf-8")).get("articles", []):
                pub_dates[it["url"]] = it.get("date", "")
                pub_order[it["url"]] = int(it.get("order", 0))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    sem = asyncio.Semaphore(int(config.get("fetch", {}).get("concurrency", 4)))

    async def guarded(u: str) -> dict:
        async with sem:
            res = await fetch_one(u, sites, out_dir, args.no_images,
                                  pub_dates.get(u, ""), pub_order.get(u, 0))
            if res.get("ok"):
                seen["urls"][u] = {
                    "title": res["title"], "path": res["path"],
                    "paywall_preview": res["paywall_preview"],
                    "fetched": dt.datetime.now().isoformat(timespec="seconds"),
                }
            return res

    results = await asyncio.gather(*(guarded(u) for u in urls))

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = [r for r in results if r.get("ok")]
    previews = [r for r in ok if r.get("paywall_preview")]
    # 只有真抓到东西才覆盖 run_slugs.json（否则空跑会清掉上次成功的选集）
    if ok:
        (project / "data" / "run_slugs.json").write_text(
            json.dumps([r["slug"] for r in ok], ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "ok": True,
        "cookie_used": bool(_COOKIE),
        "total": len(urls),
        "success": len(ok),
        "failed": len(results) - len(ok),
        "paywall_preview": len(previews),
        "full_text": len(ok) - len(previews),
        "slugs_file": str(project / "data" / "run_slugs.json"),
        "articles": [{k: r[k] for k in ("title", "text_chars", "paywall_preview") if k in r} for r in ok],
        "errors": [{"url": r["url"], "error": r["error"]} for r in results if not r.get("ok")][:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="用 bpc-fetch 抓取 WSJ 中文文章（含 Referer/Cookie 修补）")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--urls-file", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="忽略 seen.json")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--cookie", default=None, help="覆盖 config.json 里的订阅 cookie")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if result.get("ok") else 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
