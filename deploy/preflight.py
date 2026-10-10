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
import pathlib
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


def check_egress_ip() -> None:
    """记下这台机器的出口 IP。

    换机器/换机房时，这一行能直接和 archive.today 的可用性对上号
    （例如 Azure 的 IP 段被挡、住宅 IP 正常）。取不到不影响运行。
    """
    print("\n=== 3.5 出口 IP（用于对比不同机房的 archive.today 可用性）===")
    import httpx

    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = httpx.get(url, timeout=20.0, follow_redirects=True)
            if r.status_code == 200 and r.text.strip():
                add("出口 IP", True, r.text.strip()[:64], level="warn")
                return
        except Exception:  # noqa: BLE001
            continue
    add("出口 IP", True, "取不到（不影响运行）", level="warn")


def check_archive_today(sitemap_candidates: list[str]) -> None:
    """用**流水线同一个函数**去试 archive.today，所以这里的结果等于实际抓取的结果。

    失败会列出每个镜像的原因（blocked / HTTP 403 / no_snapshot / thin），
    一眼能区分"IP 被挡"和"确实没存档"。
    """
    print("\n=== 4. archive.today（正文的唯一来源）===")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "wsj-kindle" / "scripts"))
    try:
        from wsj_fetch_multi import ARCHIVE_MIRRORS, length_units, src_archive_today
    except Exception as exc:  # noqa: BLE001
        add("加载抓取模块", False, f"{type(exc).__name__}: {exc}",
            "确认 wsj-kindle/scripts/wsj_fetch_multi.py 存在")
        return

    print(f"  镜像（依次尝试）: {', '.join(ARCHIVE_MIRRORS)}")
    # 优先用 sitemap 里的真实新文章 —— 那才是流水线要抓的东西
    targets = [(f"最新文章{i + 1}", u) for i, u in enumerate(sitemap_candidates[:2])]
    if not targets:
        targets = [("固定样本(中文)", SAMPLE_CN), ("固定样本(英文)", SAMPLE_EN)]

    ok_any = False
    for label, url in targets:
        got = src_archive_today(url) or {}
        if got.get("error"):
            add(f"archive.today {label}", False, f"各镜像均未取到正文：{got['error']}",
                "若原因全是 blocked/HTTP 403，就是这个机房的 IP 被挡了 —— 换机房或改用住宅 IP"
                "（家里的 NAS / 常开的 PC）；也可以用 HTTPS_PROXY 指向可用代理再跑一次")
            continue
        ok_any = True
        add(f"archive.today {label}", True,
            f"{got['source']}，抽出 {length_units(got['text'])} 字/词 ✅")
    if not ok_any:
        print("\n  ⚠ archive.today 全部失败 —— 这条流水线拿不到全文，"
              "当前配置（max_preview_attempts=-1）会**什么也不推**。")
        print("    详见 deploy/README-server.md 的\"机房选择\"一节，以及 wsj-kindle/README.md 第 6 节。")


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
    check_egress_ip()
    check_archive_today(candidates)

    fails = [r for r in results if not r["ok"] and r["level"] != "warn"]
    warns = [r for r in results if not r["ok"] and r["level"] == "warn"]
    verdict = ("可以跑" if not fails else "还不能跑，先按上面的 → 提示处理")
    print(f"\n=== 结论：{verdict}（失败 {len(fails)} 项，提醒 {len(warns)} 项）===")
    if args.json:
        print(json.dumps({"verdict": verdict, "fails": len(fails), "results": results},
                         ensure_ascii=False, indent=2))
    return 0 if not fails else 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
