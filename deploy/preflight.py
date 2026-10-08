#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器预检：这台机器到底能不能跑这条流水线。

在服务器上先跑这个，比配好定时任务再排错省事得多。检查四件事：
  1. Python 依赖是否齐（httpx / trafilatura / markdown / bpc-fetch）
  2. Calibre 的 ebook-convert 是否可用（生成 EPUB 要用）
  3. sitemap 能不能访问（中文站、英文站各一个）
  4. archive.today 能不能访问，且能不能从它那儿抽出正文（全文的唯一来源）

用法：
    python3 preflight.py                    # 检查
    python3 preflight.py --json             # 机器可读
    HTTPS_PROXY=http://127.0.0.1:7890 python3 preflight.py   # 走代理检查
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# 只作为"探测样本"的固定文章 URL（都是已发布较久的，archive.today 上稳定有存档）
SAMPLE_CN = ("https://cn.wsj.com/articles/"
             "canadian-poet-anne-carson-awarded-nobel-prize-in-literature-de70b1b8")
SAMPLE_EN = ("https://www.wsj.com/politics/policy/"
             "new-cdc-director-is-eager-to-change-its-culture-7f2319be")

SITEMAPS = [
    ("中文站 sitemap", "https://cn.wsj.com/wsj_cn_google_news.xml", "articles/"),
    ("英文站 sitemap", "https://www.wsj.com/wsjsitemaps/wsj_google_news.xml", "politics/"),
]

results: list[dict] = []


def add(name: str, ok: bool, detail: str, fix: str = "", level: str = "") -> None:
    results.append({"check": name, "ok": ok, "level": level or ("fail" if not ok else "ok"),
                    "detail": detail, "fix": fix})
    mark = "OK  " if ok else ("WARN" if level == "warn" else "FAIL")
    print(f"  [{mark}] {name}: {detail}")
    if not ok and fix:
        print(f"         → {fix}")


def check_imports() -> None:
    print("\n=== 1. Python 依赖 ===")
    print(f"  python {sys.version.split()[0]}  ({sys.executable})")
    for mod in ("httpx", "trafilatura", "markdown", "bs4", "markdownify", "bpc_fetch"):
        try:
            __import__(mod)
            add(f"import {mod}", True, "已安装")
        except ImportError as exc:
            add(f"import {mod}", False, str(exc),
                "pip install -r deploy/requirements.txt")
    # 只需 pip 包，不需要浏览器二进制（只有 bpc-fetch 的兜底来源会用到）
    try:
        import playwright  # noqa: F401
        browsers = ""
        try:
            out = subprocess.run([sys.executable, "-m", "playwright", "install", "--dry-run",
                                  "chromium"], capture_output=True, text=True, timeout=20)
            browsers = "chromium 已装" if "already installed" in (out.stdout or "") else "chromium 未装"
        except Exception:  # noqa: BLE001
            browsers = "无法检测 chromium"
        add("playwright 浏览器", True, f"可选依赖，{browsers}（仅在 bpc-fetch 兜底时才需要）",
            level="warn")
    except ImportError:
        add("playwright 浏览器", True, "未安装（可选；只要 archive.today 能用就不需要）",
            level="warn")


def check_calibre() -> None:
    print("\n=== 2. Calibre（生成 EPUB）===")
    exe = shutil.which("ebook-convert")
    if not exe:
        add("ebook-convert", False, "PATH 里找不到",
            "sudo -v && wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sudo sh /dev/stdin")
        return
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        ver = (out.stdout or out.stderr or "").strip().splitlines()[0]
        add("ebook-convert", True, f"{ver}  ({exe})")
    except Exception as exc:  # noqa: BLE001
        add("ebook-convert", False, f"执行失败: {exc}", "重装 Calibre")


def check_network() -> list[str]:
    print("\n=== 3. sitemap（发现环节）===")
    import httpx

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
    headers = {"User-Agent": ua, "Referer": "https://www.google.com/"}
    candidates: list[str] = []
    for label, url, prefix in SITEMAPS:
        try:
            r = httpx.get(url, headers=headers, follow_redirects=True, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            add(label, False, f"{type(exc).__name__}: {exc}",
                "这台机器到 wsj 不通：换机房，或在 systemd 里配 HTTPS_PROXY 指向可用代理")
            continue
        if r.status_code != 200:
            add(label, False, f"HTTP {r.status_code}",
                "被挡了：换机房 IP，或配代理")
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)", r.text)
        hits = [u for u in locs if f"/{prefix}" in u]
        add(label, True, f"200，{len(locs)} 条 URL，其中匹配 {prefix} 的 {len(hits)} 条")
        if hits:
            candidates.append(hits[0])
    return candidates


def check_archive_today(sitemap_candidates: list[str]) -> None:
    print("\n=== 4. archive.today（正文的唯一来源）===")
    import httpx
    import trafilatura

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
    targets = [("固定样本(中文)", SAMPLE_CN), ("固定样本(英文)", SAMPLE_EN)]
    targets += [(f"最新文章{i+1}", u) for i, u in enumerate(sitemap_candidates[:1])]

    ok_any = False
    for label, url in targets:
        try:
            r = httpx.get(f"https://archive.ph/newest/{url}",
                          headers={"User-Agent": ua}, follow_redirects=True, timeout=90.0)
        except Exception as exc:  # noqa: BLE001
            add(f"archive.today {label}", False, f"{type(exc).__name__}: {exc}",
                "archive.today 不可达；它拿不到全文的话，流水线只能出标题/预览")
            continue
        text = ""
        try:
            text = trafilatura.extract(r.text, include_comments=False) or ""
        except Exception:  # noqa: BLE001
            pass
        units = len(re.findall(r"[\u3400-\u9fff]", text)) + len(re.findall(r"[A-Za-z']+", text))
        if r.status_code == 429:
            add(f"archive.today {label}", False, "HTTP 429（限流）",
                "把 config.json 的 fetch.delay_seconds 调大到 6~8 秒")
            continue
        if r.status_code == 200 and units > 200:
            ok_any = True
            add(f"archive.today {label}", True, f"200，抽出正文 {units} 字/词 ✅")
        else:
            add(f"archive.today {label}", False,
                f"HTTP {r.status_code}，只抽出 {units} 字/词",
                "这个机房 IP 可能被 archive.today 挡了；换机房或用住宅 IP（家里的 NAS/树莓派）")
    if not ok_any:
        print("\n  ⚠ 四类检查里 archive.today 全失败——这条流水线就没有全文可推。"
              "详见 deploy/README-server.md 的\"机房选择\"一节。")


def main() -> int:
    ap = argparse.ArgumentParser(description="服务器预检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    print("wsj 每日推送 —— 服务器预检")
    print(f"代理: {proxy or '（未设置，直连）'}")

    check_imports()
    check_calibre()
    candidates = check_network()
    check_archive_today(candidates)

    fails = [r for r in results if not r["ok"] and r["level"] != "warn"]
    warns = [r for r in results if not r["ok"] and r["level"] == "warn"]
    verdict = ("可以跑" if not fails else "还不能跑，先按上面的 → 提示处理")
    print(f"\n=== 结论：{verdict}（失败 {len(fails)} 项，提醒 {len(warns)} 项）===")
    if args.json:
        print(json.dumps({"verdict": verdict, "fails": len(fails), "results": results},
                         ensure_ascii=False, indent=2))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
