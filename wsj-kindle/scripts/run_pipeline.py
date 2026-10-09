#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令跑完：发现 → 抓取 → 生成 RSS → 生成 EPUB →（可选）推送 Kindle。

用法：
    python run_pipeline.py                 # 全流程（kindle.enabled=false 时不发邮件）
    python run_pipeline.py --no-send
    python run_pipeline.py --skip-discover --force
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# report() 要用到，由 main() 赋值
_KINDLE_ENABLED = False
_APPLIED: list[str] = []
_ROLLED_BACK = 0


def rollback_seen(project: pathlib.Path, slugs: list[str]) -> int:
    """把这一轮文章的 URL 从 data/seen.json 里撤掉，返回撤销条数。

    用途：投递失败时**不能**算"已完成"。抓取阶段会把 URL 写进 seen.json（还会提交回仓库），
    如果之后发信失败却不撤回，下次运行就会认为这些文章已经处理过，永远不会重发。
    """
    seen_path = project / "data" / "seen.json"
    slugs_path = project / "data" / "run_slugs.json"
    if not (seen_path.exists() and slugs_path.exists()):
        return 0
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8-sig"))
        wanted = {s for s in json.loads(slugs_path.read_text(encoding="utf-8-sig")) if s}
    except (json.JSONDecodeError, OSError):
        return 0
    urls = seen.get("urls", {})
    drop = [u for u, meta in urls.items()
            if any(s in str(meta.get("path", "") or "") for s in wanted)]
    for u in drop:
        urls.pop(u, None)
    if drop:
        seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(drop)


def run_step(name: str, argv: list[str], required: bool = True) -> dict:
    print(f"\n=== [{name}] {' '.join(argv[1:])}", flush=True)
    proc = subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = (proc.stdout or "").strip()
    parsed: dict | None = None
    if out.startswith("{"):
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = None
    print(out if out else (proc.stderr or "").strip(), flush=True)
    if proc.returncode != 0 and (proc.stderr or "").strip():
        print(proc.stderr.strip(), file=sys.stderr, flush=True)
    return {"step": name, "returncode": proc.returncode, "result": parsed,
            "stderr_tail": (proc.stderr or "")[-300:], "required": required}


def main() -> int:
    ap = argparse.ArgumentParser(description="wsj-kindle 每日流水线")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--since", default=None)
    ap.add_argument("--force", action="store_true", help="忽略 seen.json，重抓")
    ap.add_argument("--skip-discover", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--no-epub", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--epub-all", action="store_true", help="EPUB 用全部文章而不是仅本轮")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    # 是否推送要看"环境变量覆盖后"的配置：CI 里凭据和 enabled 都走 secrets，
    # 不能只读 config.json（那里 enabled 是 false）
    sys.path.insert(0, str(SCRIPTS))
    from send_kindle import apply_env_overrides  # noqa: E402

    effective, applied = apply_env_overrides(config)
    kindle_enabled = bool(effective["kindle"].get("enabled"))
    global _KINDLE_ENABLED, _APPLIED
    _KINDLE_ENABLED, _APPLIED = kindle_enabled, applied
    # 不同站点用不同的发现/抓取脚本：中文站走 RSS + bpc-fetch，
    # 英文政治板块走 sitemap + archive.today（见各自 README）
    disc_script = config.get("pipeline", {}).get("discover_script") or "wsj_discover.py"
    fetch_script = config.get("pipeline", {}).get("fetch_script") or "wsj_fetch.py"
    steps: list[dict] = []

    if not args.skip_discover:
        argv = [str(SCRIPTS / disc_script), "--project", str(project)]
        if args.since:
            argv += ["--since", args.since]
        if args.force:
            argv.append("--all")  # 强制重抓时，发现环节也要忽略 seen.json
        steps.append(run_step("discover", argv))
        if steps[-1]["returncode"] != 0:
            return report(steps, "发现阶段失败：先看 wsj_discover.py 的 notes")
        if (steps[-1]["result"] or {}).get("new", 0) == 0:
            # 没有新文章是正常结果（比如一天跑两次），不算失败
            return report(steps, "no new articles", benign=True)

    no_new_content = False
    if not args.skip_fetch:
        argv = [str(SCRIPTS / fetch_script), "--project", str(project)]
        if args.force:
            argv.append("--force")
        steps.append(run_step("fetch", argv))
        if steps[-1]["returncode"] != 0:
            return report(steps, "抓取阶段失败")
        # 抓不到 ≠ 失败：刚发布的文章 archive.today 上可能还没存档，
        # 而抓失败的 URL 不会写进 seen.json，下次运行会自动重试。
        fetch_result = steps[-1]["result"] or {}
        no_new_content = fetch_result.get("success", 0) == 0
        if no_new_content:
            print("本轮没有抓到任何文章，跳过 RSS / EPUB / 推送（这些 URL 未写入 seen.json，下次自动重试）")
            if fetch_result.get("errors"):
                print("抓取失败原因（每个来源的尝试结果）：")
                print(json.dumps(fetch_result["errors"], ensure_ascii=False, indent=2))

    # 关键：一篇都没抓到时**不能**去跑 build_feed ——
    # runner 是干净检出，articles/ 是空的，build_feed 会以 "没有找到 Markdown" 退出 1，
    # 把一次「本轮无新内容」的正常情况误报成失败。
    if no_new_content:
        steps.append({"step": "feed", "returncode": 0, "result": {
            "ok": True, "skipped": True, "reason": "本轮无新文章，无需重建 RSS"}})
    else:
        steps.append(run_step("feed", [str(SCRIPTS / "build_feed.py"), "--project", str(project)]))
        if steps[-1]["returncode"] != 0:
            return report(steps, "RSS 生成失败")

    epub_path = None
    if not args.no_epub and not no_new_content:
        argv = [str(SCRIPTS / "build_epub.py"), "--project", str(project)]
        if args.epub_all:
            argv.append("--all")
        steps.append(run_step("epub", argv))
        if steps[-1]["returncode"] == 0:
            epub_path = (steps[-1]["result"] or {}).get("epub")
    elif no_new_content:
        steps.append({"step": "epub", "returncode": 0, "result": {
            "ok": True, "skipped": True, "reason": "本轮没有新文章可合成"}})

    if epub_path and not args.no_send and kindle_enabled:
        send_step = run_step("send", [str(SCRIPTS / "send_kindle.py"),
                                      "--project", str(project), "--epub", epub_path])
        steps.append(send_step)
        if send_step["returncode"] != 0:
            # 投递失败 → 撤回状态，让下一次运行自动重抓并重发（否则这批文章永远不会再出现）
            try:
                slugs = json.loads((project / "data" / "run_slugs.json")
                                   .read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError):
                slugs = []
            global _ROLLED_BACK
            _ROLLED_BACK = rollback_seen(project, slugs)
            if _ROLLED_BACK:
                print(f"\n投递失败，已把本轮 {_ROLLED_BACK} 篇的 URL 从 seen.json 撤回 —— "
                      "下次运行会自动重抓并重发")
    elif not args.no_send and not kindle_enabled:
        steps.append({"step": "send", "returncode": 0, "result": {
            "ok": True, "skipped": True,
            "reason": ("未启用推送：config.json 的 kindle.enabled=false，"
                       "环境变量 WSJ_KINDLE_ENABLED / TO 等也没提供")}})

    return report(steps, "本轮没有抓到新文章，已跳过 EPUB" if no_new_content else None,
                  benign=no_new_content)


def report(steps: list[dict], stop_reason: str | None, benign: bool = False) -> int:
    feed = next((s["result"] for s in steps if s["step"] == "feed" and s["result"]), None)
    epub = next((s["result"] for s in steps if s["step"] == "epub" and s["result"]), None)
    failed = [s["step"] for s in steps if s["returncode"] != 0 and s.get("required", True)]
    # 把"哪一步失败、失败原因是什么"直接放进汇总 JSON：
    # 在 CI 里这段会被写进运行摘要，不用再去翻几十行日志
    failing = next((s for s in steps if s["step"] in failed), None)
    fres = failing.get("result") if failing and isinstance(failing.get("result"), dict) else {}
    summary = {
        "ok": not failed and (benign or not stop_reason),
        "stopped": stop_reason,
        "failed_steps": failed,
        "state_rolled_back": _ROLLED_BACK,
        "failing_step": failing["step"] if failing else None,
        "failing_reason": (fres or {}).get("error") or (fres or {}).get("hint")
                          or (failing or {}).get("stderr_tail") or None,
        "kindle_enabled": _KINDLE_ENABLED,
        "env_overrides": sorted(set(_APPLIED)),
        "feed_url": (feed or {}).get("feed_url"),
        "feed_items": (feed or {}).get("in_feed"),
        "epub": (epub or {}).get("epub"),
        "epub_articles": (epub or {}).get("articles"),
        "steps": [{"step": s["step"], "exit": s["returncode"]} for s in steps],
    }
    print("\n=== 汇总 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
