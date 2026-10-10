#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源正文抓取：按配置的顺序尝试 archive.today / Wayback / 线上（可用订阅 cookie），
取正文最长的那一份，写成和 bpc-fetch 一样的 Markdown 布局。

为什么英文站不能直接用 bpc-fetch：
  * www.wsj.com 对新式板块 URL（politics/...）一律 401，Google Referer 也无效（中文站才有效）；
  * bpc-fetch 的 archive 兜底只查 web.archive.org，而这些新 URL 在 Wayback 里没有快照（404）；
  * 实测 archive.today 有全文（341 / 584 / 2378 / 636 words，结尾带
    "Appeared in the ... print edition" 或 "Copyright ©2026 Dow Jones"），
    而线上和 Wayback 只有 68~116 words 的付费墙预览。

产物与 wsj_fetch.py 一致（articles/<标题>/<标题>.md + images/、data/seen.json、
data/run_slugs.json），所以 build_feed.py / build_epub.py 不用改。

用法：
    python wsj_fetch_multi.py --project ../wsj-politics
    python wsj_fetch_multi.py --limit 3 --force
    python wsj_fetch_multi.py --sources archive_today        # 只用一个源
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time

import httpx
import trafilatura

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bpc_fetch.cli import _slugify  # noqa: E402
from bpc_fetch.extract import _extract_image_urls, _image_filename, download_images  # noqa: E402
from wsj_fetch import clean_markdown, enrich_frontmatter  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

ALL_SOURCES = ["archive_today", "wayback", "live", "bpc_fetch"]

PAYWALL_MARKERS = [
    # 英文
    "continue reading your article with a wsj subscription",
    "already a subscriber? sign in",
    "to read the full story",
    "subscribe to continue reading",
    "unlock this article",
    # 中文（简繁）
    "畅读全文", "暢讀全文",
    "订阅《华尔街日报》", "訂閱《華爾街日報》",
    "我们注意到您已经是订阅用户", "我們注意到您已經是訂閱用戶",
]
# 文章自然结尾的标志：出现这些基本可以认定是全文
COMPLETE_MARKERS = [
    "print edition as",
    "copyright ©",
    "dow jones & company",
    "write to ",
    "appeared in the",
]
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def length_units(text: str) -> int:
    """中英通用的长度度量：汉字数 + 拉丁词数。"""
    return len(CJK.findall(text)) + len(re.findall(r"[A-Za-z']+", text))

_COOKIE = ""
_REFERER = "https://www.google.com/"


def headers() -> dict:
    h = {"User-Agent": UA, "Referer": _REFERER,
         "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9"}
    if _COOKIE:
        h["Cookie"] = _COOKIE
    return h


def http_get(url: str, timeout: float = 120.0, attempts: int = 2):
    last = None
    for n in range(attempts):
        try:
            r = httpx.get(url, headers=headers(), follow_redirects=True, timeout=timeout)
            if r.status_code in (429, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (n + 1))
                continue
            return r
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (n + 1))
    return RuntimeError(last)


def extract_text(html: str, url: str = "") -> str:
    try:
        text = trafilatura.extract(html, url=url or None, include_comments=False,
                                   include_tables=True, favor_recall=True) or ""
    except Exception:  # noqa: BLE001
        return ""
    return tidy(text)


def tidy(text: str) -> str:
    """清掉 trafilatura 在 WSJ 页面上带出来的零碎（ET 时区标记、多余空行、断掉的署名）。"""
    raw = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(raw):
        s = line.strip()
        if s in ("ET", "ET.", "Updated", "Listen", "SHARE", "Share"):
            continue
        if re.fullmatch(r"\d+\s*min(ute)?s?\s*(read)?", s, re.I):
            continue
        # 署名常被拆成三行: "By" / "Ken Thomas" / 正文
        if s == "By" and i + 1 < len(raw):
            nxt = raw[i + 1].strip()
            if nxt and not nxt.startswith(("By ", "http")):
                out.append(f"By {nxt}")
                raw[i + 1] = ""
                continue
        s = re.sub(r"^By\s+(.+?)\s+ET\s+", r"By \1 — ", s)
        s = re.sub(r"\s{2,}", " ", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# --------------------------------------------------------------------- 各来源
#: archive.today 的多个镜像。它是同一家服务，但不同域名的边缘节点不一样，
#: 某些机房 IP 会被其中一个域名挡（返回验证页），换一个往往就能过。
ARCHIVE_MIRRORS = ["archive.ph", "archive.is", "archive.li", "archive.md", "archive.today"]

#: 被反爬/风控挡住时页面里会出现的特征词
ARCHIVE_BLOCK_MARKERS = [
    "captcha", "cf-chl", "challenge-platform", "attention required",
    "ddos protection", "checking your browser", "verify you are human",
]

#: 提取正文短于这个字数，就认为这个镜像没给出可用快照
ARCHIVE_MIN_UNITS = 200

#: archive.today 连续这么多篇都失败后，本轮不再尝试它（避免一次运行被拖成几小时）
ARCHIVE_BREAKER_LIMIT = 3


def src_archive_today(url: str, timeout: float = 60.0, attempts: int = 2) -> dict | None:
    """依次尝试 archive.today 的各个镜像，返回第一个能拿到正文的。

    失败原因会汇总在 error 里，例如：
        archive.ph:HTTP 429; archive.is:ConnectTimeout: ...
    这样云端日志能直接区分「限流 / IP 被挡 / 域名解析不了 / 确实没存档」。

    注意：http_get 重试耗尽后**返回**（而不是抛出）一个 RuntimeError，真正的原因在
    str() 里。所以这里必须记 `str(r)`，只记 `type(r).__name__` 会全变成无信息量的
    "RuntimeError"（真实踩过：五个镜像全失败却看不出为什么）。
    """
    problems: list[str] = []
    for host in ARCHIVE_MIRRORS:
        r = http_get(f"https://{host}/newest/{url}", timeout=timeout, attempts=attempts)
        if isinstance(r, Exception):
            detail = str(r).strip().replace("\n", " ")
            problems.append(f"{host}:{detail[:110] or type(r).__name__}")
            continue
        if r.status_code != 200:
            problems.append(f"{host}:HTTP {r.status_code}")
            continue
        low = r.text[:4000].lower()
        if any(m in low for m in ARCHIVE_BLOCK_MARKERS):
            problems.append(f"{host}:blocked（返回了验证/风控页）")
            continue
        if len(r.content) < 20000:  # 未存档时只给一个小提示页
            problems.append(f"{host}:no_snapshot")
            continue
        text = extract_text(r.text, url)
        if length_units(text) < ARCHIVE_MIN_UNITS:
            problems.append(f"{host}:thin({length_units(text)})")
            continue
        # 图片用"实际响应地址"做 base，archive.today 的图片是它自己的相对路径
        return {"source": f"archive_today/{host}", "text": text,
                "images": _extract_image_urls(r.text, str(r.url))}
    return {"error": "; ".join(problems) or "no_mirror_tried"}


def src_wayback(url: str) -> dict | None:
    r = http_get(f"https://web.archive.org/web/2/{url}")
    if isinstance(r, Exception):
        return {"error": str(r)}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    if "has not archived that URL" in r.text[:60000]:
        return {"error": "no_snapshot"}
    return {"source": "wayback", "text": extract_text(r.text, url),
            "images": _extract_image_urls(r.text, url)}


def src_live(url: str) -> dict | None:
    r = http_get(url)
    if isinstance(r, Exception):
        return {"error": str(r)}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return {"source": "live" + ("+cookie" if _COOKIE else ""),
            "text": extract_text(r.text, url), "images": _extract_image_urls(r.text, url)}


def src_bpc_fetch(url: str) -> dict | None:
    """保留 bpc-fetch 自身的取文路径（对英文站通常 401，作为兜底存在）。"""
    try:
        import asyncio

        from bpc_fetch import extract as bpf_extract
        from bpc_fetch import strategy as bpf_strategy
        from bpc_fetch.sites import SITES_JS_DEFAULT, domain_from_url, get_sites_map

        sites = get_sites_map(SITES_JS_DEFAULT)
        html, status, dom = asyncio.run(
            bpf_strategy.fetch_with_retries(url, sites.get(domain_from_url(url))))
        if status != 200:
            return {"error": f"HTTP {status}"}
        art = bpf_extract.extract_article(html, url, dom_result=dom)
        return {"source": "bpc_fetch", "text": tidy(art.get("text", "")),
                "images": art.get("images", [])}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


SOURCES = {"archive_today": src_archive_today, "wayback": src_wayback,
           "live": src_live, "bpc_fetch": src_bpc_fetch}


def looks_paywalled(text: str) -> bool:
    """判断拿到的是全文还是付费墙预览（中英通用）。

    只看"提取出来的正文"，不要看原始 HTML——存档页的 HTML 里往往仍带着
    「畅读全文」的样板，但正文其实是完整的。

    顺序很重要：**先查付费墙提示语，再查"完整结尾"标记**。
    预览页的正文往往也带着 `Copyright © … Dow Jones & Company` 那行（它排在
    「订阅…畅读全文」之前），所以先看 COMPLETE_MARKERS 会把预览误判成全文。
    真实踩过：11 篇预览只加上了提示语、却没打上 paywall 标记，导致它们
    既没被推迟、也没被如实标注。
    """
    if any(m in text for m in PAYWALL_MARKERS):
        return True
    tail = text[-1500:].lower()
    if any(m in tail for m in COMPLETE_MARKERS):
        return False
    return length_units(text) < 250


def build_markdown(title: str, url: str, text: str, pub_date: str, order: int,
                   images: list[str], paywalled: bool, out_dir: pathlib.Path,
                   slug: str, no_images: bool) -> tuple[pathlib.Path, int]:
    saved = 0
    images_dir = out_dir / slug / "images"
    if images and not no_images:
        saved = len(__import__("asyncio").run(download_images(images, images_dir, max_images=3)))

    lines = ["---", f'title: "{title.replace(chr(34), chr(39))}"']
    if pub_date:
        lines.append(f"date: {pub_date[:10]}")
    lines.append(f"source: {url}")
    lines.append(f"order: {order}")
    if paywalled:
        lines.append("paywall: true")
    lines += ["---", "", f"# {title}", ""]

    if saved and images:
        lines += [f"![image](images/{_image_filename(images[0], 0)})", ""]
    lines += [text, ""]
    if saved > 1:
        for i, img in enumerate(images[1:saved], 1):
            lines += [f"![image](images/{_image_filename(img, i)})", ""]

    path = out_dir / slug / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, saved


def main() -> int:
    ap = argparse.ArgumentParser(description="多源抓取（archive.today / Wayback / 线上）")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--urls-file", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sources", default=None, help="逗号分隔，覆盖 config.json")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    fcfg = config.get("fetch", {})
    global _COOKIE, _REFERER
    _COOKIE = fcfg.get("cookie", "") or ""
    _REFERER = fcfg.get("referer") or "https://www.google.com/"
    sources = [s.strip() for s in (args.sources.split(",") if args.sources
                                   else fcfg.get("sources") or ALL_SOURCES) if s.strip()]
    delay = float(fcfg.get("delay_seconds", 3))
    good_enough = int(fcfg.get("good_enough_words", 400))

    urls_file = pathlib.Path(args.urls_file) if args.urls_file else project / "urls.txt"
    if not urls_file.exists():
        print(json.dumps({"ok": False, "error": f"找不到 {urls_file}（先跑 wsj_discover_sitemap.py）"},
                         ensure_ascii=False))
        return 1
    urls = [u.strip() for u in urls_file.read_text(encoding="utf-8").splitlines()
            if u.strip() and not u.startswith("#")]

    meta: dict[str, dict] = {}
    disc = project / "data" / "discovered.json"
    if disc.exists():
        try:
            for it in json.loads(disc.read_text(encoding="utf-8")).get("articles", []):
                meta[it["url"]] = it
        except (json.JSONDecodeError, KeyError):
            pass

    seen_path = project / "data" / "seen.json"
    seen = {"urls": {}}
    if seen_path.exists():
        try:
            seen = json.loads(seen_path.read_text(encoding="utf-8"))
            seen.setdefault("urls", {})
        except json.JSONDecodeError:
            pass
    if not args.force:
        urls = [u for u in urls if u not in seen["urls"]]
    if args.limit:
        urls = urls[:args.limit]
    if not urls:
        print(json.dumps({"ok": True, "new": 0,
                          "note": "没有新 URL（都已抓过，或用 --force）"}, ensure_ascii=False))
        return 0

    out_dir = pathlib.Path(args.out_dir) if args.out_dir else project / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 「预览推迟」计数：data/preview_pending.json
    # 值为 -1 表示不推迟、直接把预览发出去（关掉这个机制）
    max_preview_tries = int(fcfg.get("max_preview_attempts", 1))
    pending_path = project / "data" / "preview_pending.json"
    pending: dict[str, dict] = {}
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            pending = {}
    deferred: list[dict] = []

    results: list[dict] = []
    slugs: list[str] = []
    archive_fail_streak = 0
    for idx, url in enumerate(urls):
        info = meta.get(url, {})
        title = info.get("title") or url.rstrip("/").split("/")[-1]
        pub_date = (info.get("date") or "")[:10]
        order = int(info.get("order", idx))
        attempts: list[str] = []
        best: dict | None = None

        for name in sources:
            fn = SOURCES.get(name)
            if fn is None:
                attempts.append(f"{name}:unknown")
                continue
            # 熔断：archive.today 若连续多篇都失败（机房 IP 被挡/限流），就别再逐篇试了，
            # 否则 30 篇 × 5 镜像 × 重试会把一次运行拖成几小时
            if name == "archive_today" and archive_fail_streak >= ARCHIVE_BREAKER_LIMIT:
                attempts.append("archive_today:skipped(连续失败已熔断)")
                continue
            got = fn(url) or {}
            if name == "archive_today":
                archive_fail_streak = archive_fail_streak + 1 if got.get("error") else 0
                if archive_fail_streak == ARCHIVE_BREAKER_LIMIT:
                    print(f"  [熔断] archive.today 连续 {ARCHIVE_BREAKER_LIMIT} 篇失败，"
                          f"本轮不再尝试它。最近的原因：{str(got.get('error'))[:150]}",
                          file=sys.stderr)
            if got.get("error"):
                attempts.append(f"{name}:{got['error']}")
                continue
            words = length_units(got.get("text", ""))
            attempts.append(f"{name}:{words}u")
            if words > length_units((best or {}).get("text", "")):
                best = got
            if words >= good_enough:
                break
            time.sleep(delay if name == "archive_today" else 1)

        if not best or not best.get("text"):
            results.append({"ok": False, "url": url, "error": "; ".join(attempts) or "all_failed"})
            continue

        text = best["text"]
        if "archive_today" in sources:
            time.sleep(delay)  # 对 archive.today 客气一点
        paywalled = looks_paywalled(text)
        words = length_units(text)

        # 拿到的是付费预览时先"推迟"，不要立刻发出去。
        # 实测（2026-10-09）：同一批文章当晚抓到 19/30 是预览，几小时后同样的 URL
        # 10/10 都是 archive.today 全文 —— 也就是存档站只是慢了几小时。
        # 推迟不会写 seen.json，所以下次运行会重新发现并重抓。
        # max_preview_attempts 的语义：
        #   -1 = 永不接受预览（一直重试；默认，宁可这天不发也不发概要）
        #    0 = 不推迟，立刻接受预览
        #    N = 推迟 N 次后接受
        if paywalled:
            tries = int(pending.get(url, {}).get("tries", 0))
            if max_preview_tries < 0 or tries < max_preview_tries:
                pending[url] = {"tries": tries + 1,
                                "last": dt.datetime.now().isoformat(timespec="seconds"),
                                "title": title}
                deferred.append({"url": url, "title": title, "words": words, "tries": tries + 1})
                results.append({"ok": True, "url": url, "title": title, "deferred": True,
                                "words": words, "source": best.get("source"),
                                "paywall_preview": True, "attempts": attempts})
                continue
            print(f"  [接受预览] {title[:40]} —— 已推迟 {tries} 次仍无全文", file=sys.stderr)
        pending.pop(url, None)

        slug = _slugify(title)
        md, n_img = build_markdown(title, url, text, pub_date, order, best.get("images") or [],
                                   paywalled, out_dir, slug, args.no_images)
        # 手工加付费墙提示（复用中文脚本的清理器，只为统一收尾）
        body = md.read_text(encoding="utf-8")
        cleaned, _ = clean_markdown(body, note=fcfg.get("paywall_note", ""))
        if paywalled and fcfg.get("paywall_note"):
            cleaned = cleaned.rstrip() + "\n\n" + fcfg["paywall_note"] + "\n"
        md.write_text(cleaned, encoding="utf-8")

        seen["urls"][url] = {"title": title, "path": str(md), "paywall_preview": paywalled,
                            "words": words, "source": best.get("source"),
                            "fetched": dt.datetime.now().isoformat(timespec="seconds")}
        slugs.append(slug)
        results.append({"ok": True, "url": url, "title": title, "slug": slug,
                        "words": words, "source": best.get("source"),
                        "paywall_preview": paywalled, "images": n_img,
                        "attempts": attempts})

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    # counters will be reset as each deferred URL is fetched again
    # 剪掉太老的推迟记录：那些 URL 早已出了 sitemap 窗口，不会再被重抓
    cutoff = (dt.datetime.now() - dt.timedelta(days=7)).isoformat()
    pending = {u: v for u, v in pending.items() if str(v.get("last", "")) >= cutoff}
    pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    # 只有真抓到东西才覆盖 run_slugs.json：
    # 否则"本轮全军覆没"会把上次成功的选集清空，之后手工跑 build_epub.py 就没文章可合了
    if slugs:
        (project / "data" / "run_slugs.json").write_text(
            json.dumps(slugs, ensure_ascii=False, indent=1), encoding="utf-8")
    slugs_file = project / "data" / "run_slugs.json"

    accepted = [r for r in results if r.get("ok") and not r.get("deferred")]
    result = {
        "ok": True,
        "cookie_used": bool(_COOKIE),
        "sources": sources,
        "total": len(urls),
        "success": len(accepted),
        "failed": sum(1 for r in results if not r.get("ok")),
        "deferred_preview": len(deferred),
        "deferred_titles": [d["title"][:34] for d in deferred[:8]],
        "paywall_preview": sum(1 for r in accepted if r.get("paywall_preview")),
        "full_text": sum(1 for r in accepted if not r.get("paywall_preview")),
        "avg_words": round(sum(r["words"] for r in accepted) / len(accepted)) if accepted else 0,
        "slugs_file": str(slugs_file),
        "articles": [{k: r[k] for k in ("title", "words", "source", "paywall_preview")}
                     for r in accepted],
        "errors": [{"url": r["url"], "error": r["error"]} for r in results if not r.get("ok")][:5],
    }
    if deferred:
        print(f"注：{len(deferred)} 篇目前只有付费预览，已推迟到下次运行"
              f"（archive.today 通常几小时内就有全文，届时会正常发出）", file=sys.stderr)
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
