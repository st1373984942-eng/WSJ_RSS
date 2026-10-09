#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 EPUB 通过 Amazon「发送至 Kindle」邮箱推到 Kindle。

用标准库 smtplib，不依赖任何第三方服务。要点（易踩）：
  * 发件邮箱必须已加入「已批准的个人文档电子邮箱列表」，否则邮件被静默丢弃。
  * 主题写 convert，Amazon 会按需转换格式。
  * 2022 年底起 Amazon 不再接受 MOBI/AZW，只能发 EPUB/PDF/DOCX 等。
  * 单个附件 ≤ 50MB 比较稳妥（邮件上限），一封最多 25 个附件。
  * **端口与加密方式必须配套**：465 用隐式 TLS（SMTP_SSL），587 用 STARTTLS。
    配错（例如 465 + STARTTLS）服务器会立刻断连，报
    `SMTPServerDisconnected: Connection unexpectedly closed`。
    本脚本会先按配置试、失败再自动换另一种组合，所以一般不用手改。

凭据可以放 config.json，也可以用环境变量覆盖（CI 里必须用后者，别把密码提交进仓库）。
支持两种命名：
  * 本项目风格：WSJ_KINDLE_ENABLED / WSJ_KINDLE_TO / WSJ_SMTP_HOST / WSJ_SMTP_PORT /
    WSJ_SMTP_SSL / WSJ_SMTP_USER / WSJ_SMTP_PASSWORD / WSJ_SMTP_FROM / WSJ_SMTP_FROM_NAME
  * 书伴 Calibre-News-Delivery 风格：TO / FROM / SMTP / PORT / ENCRYPT / SECRET

用法：
    python send_kindle.py --probe --project .                # 不需要密码：看 465/587 哪个连得上
    python send_kindle.py --epub out/xxx.epub --dry-run      # 只校验配置
    python send_kindle.py --epub out/xxx.epub
    TO=me@kindle.com SECRET=xxxx python send_kindle.py --epub out/xxx.epub --project .
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent

CONN_HINT = (
    "连不上 SMTP。按顺序查："
    "① 端口与加密方式要配套——465 用 SSL（隐式 TLS）、587 用 STARTTLS，配错就是 "
    "'Connection unexpectedly closed'（脚本已自动试过两种组合，见 tried）；"
    "② 25 端口在 GitHub runner 上是被封的，别用；"
    "③ 有邮箱在**密码/授权码错误**时也直接断连而不返回 535 —— 看下面的 probe："
    "probe 里某个端口通、但发信断连 ⇒ 基本是凭据问题（确认用的是授权码，不是登录密码）；"
    "probe 两个端口都不通 ⇒ 是这台机器/这个机房 IP 被该 SMTP 挡了（换 Gmail App Password 或换机器）；"
    "④ 用 `python send_kindle.py --probe --project .` 可以随时单独测连通性。"
)


def apply_env_overrides(config: dict) -> tuple[dict, list[str]]:
    """用环境变量覆盖 kindle 段，返回 (新配置, 实际生效的变量名列表)。"""
    kindle = json.loads(json.dumps(config.get("kindle", {})))
    smtp = kindle.setdefault("smtp", {})
    applied: list[str] = []

    def take(field: str, *names: str) -> str | None:
        for n in names:
            v = os.environ.get(n)
            if v and v.strip():
                applied.append(n)
                return v.strip()
        return None

    enabled = take("enabled", "WSJ_KINDLE_ENABLED")
    if enabled is not None:
        kindle["enabled"] = enabled.lower() in ("1", "true", "yes", "on")

    to = take("addresses", "WSJ_KINDLE_TO", "TO")
    if to:
        kindle["addresses"] = [a.strip() for a in to.replace(";", ",").split(",") if a.strip()]

    for field, names in {
        "host": ("WSJ_SMTP_HOST", "SMTP"),
        "port": ("WSJ_SMTP_PORT", "PORT"),
        "username": ("WSJ_SMTP_USER", "SMTP_USER"),
        "password": ("WSJ_SMTP_PASSWORD", "SECRET"),
        "from": ("WSJ_SMTP_FROM", "FROM"),
        "from_name": ("WSJ_SMTP_FROM_NAME", "FROM_NAME"),
    }.items():
        val = take(field, *names)
        if val is None:
            continue
        smtp[field] = int(val) if field == "port" and val.isdigit() else val

    enc = take("encrypt", "WSJ_SMTP_ENCRYPT", "ENCRYPT")
    if enc:
        low = enc.lower()
        if low.startswith("ssl"):
            smtp["ssl"] = True
        elif low in ("starttls", "tls", "starttls/tls"):
            smtp["ssl"] = False
    if "ssl" not in smtp:
        smtp["ssl"] = str(smtp.get("port", 465)) != "587"
    if smtp.get("from") and not smtp.get("username"):
        smtp["username"] = smtp["from"]

    return {"kindle": kindle}, applied


def attempt_send(smtp: dict, msg: EmailMessage, use_ssl: bool, port: int,
                 debug: bool = False) -> None:
    """debug=True 时打印 SMTP 原始对话，用来判断到底断在哪一步：
    连不上（端口/加密不匹配）还是认证被拒（凭据问题）。"""
    host = smtp["host"]
    if debug:
        print(f"--- SMTP 对话开始：{'SSL(隐式TLS)' if use_ssl else 'STARTTLS'} port={port} ---",
              file=sys.stderr)
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=120,
                              context=ssl.create_default_context()) as s:
            if debug:
                s.set_debuglevel(1)
            s.login(smtp["username"], smtp["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=120) as s:
            if debug:
                s.set_debuglevel(1)
            s.starttls(context=ssl.create_default_context())
            s.login(smtp["username"], smtp["password"])
            s.send_message(msg)
    if debug:
        print("--- SMTP 对话结束（成功）---", file=sys.stderr)


def smtp_probe(host: str, timeout: float = 20.0) -> list[dict]:
    """不需要密码：分别试 465+隐式TLS 与 587+STARTTLS，看这台机器能连上哪个。"""
    out: list[dict] = []
    for use_ssl, port in ((True, 465), (False, 587)):
        t0 = time.time()
        entry: dict = {"mode": "SSL(隐式TLS)" if use_ssl else "STARTTLS", "port": port}
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=timeout,
                                      context=ssl.create_default_context()) as s:
                    s.ehlo()
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as s:
                    s.ehlo()
                    s.starttls(context=ssl.create_default_context())
            entry |= {"ok": True, "ms": int((time.time() - t0) * 1000)}
        except Exception as exc:  # noqa: BLE001
            entry |= {"ok": False, "ms": int((time.time() - t0) * 1000),
                      "error": f"{type(exc).__name__}: {exc}"}
        out.append(entry)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="推送 EPUB 到 Kindle 邮箱")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--epub", default=None, help="要推送的 EPUB（--probe 时不需要）")
    ap.add_argument("--to", default=None, help="覆盖收件地址，逗号分隔")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="只测 SMTP 连通性（465/587 哪个通），不需要密码")
    ap.add_argument("--debug", action="store_true",
                    help="打印 SMTP 原始对话（判断断在连接阶段还是认证阶段）")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8-sig"))
    overrides, applied = apply_env_overrides(config)
    kindle = overrides["kindle"]
    smtp = kindle.get("smtp", {})

    if args.probe:
        host = smtp.get("host")
        if not host:
            print(json.dumps({"ok": False,
                              "error": "没有 SMTP 主机（config.json kindle.smtp.host，或 SMTP / WSJ_SMTP_HOST）"},
                             ensure_ascii=False, indent=2))
            return 1
        probes = smtp_probe(host)
        working = [p for p in probes if p.get("ok")]
        if working:
            first = working[0]
            verdict = (f"可用：port={first['port']} / {first['mode']}；"
                       f"建议 PORT={first['port']}、ENCRYPT="
                       f"{'SSL' if first['mode'].startswith('SSL') else 'STARTTLS'}")
        else:
            verdict = f"{host} 的 465 和 587 都连不上 —— 这台机器/这个机房到该 SMTP 被挡了"
        print(json.dumps({"ok": bool(working), "host": host, "probe": probes,
                          "verdict": verdict, "hint": None if working else CONN_HINT},
                         ensure_ascii=False, indent=None if args.compact else 2))
        return 0 if working else 1

    if not args.epub:
        print(json.dumps({"ok": False, "error": "缺少 --epub"}, ensure_ascii=False))
        return 1

    recipients = [a.strip() for a in (args.to.split(",") if args.to else kindle.get("addresses", []))
                  if a.strip()]
    epub = pathlib.Path(args.epub)

    problems = []
    if not epub.exists():
        problems.append(f"EPUB 不存在: {epub}")
    if not recipients:
        problems.append("没有收件地址（config.json 的 kindle.addresses，或环境变量 TO / WSJ_KINDLE_TO）")
    if not smtp.get("host"):
        problems.append("没有 SMTP 主机（kindle.smtp.host，或 SMTP / WSJ_SMTP_HOST）")
    if not smtp.get("username") or not smtp.get("password"):
        problems.append("SMTP 用户名/密码为空（QQ/163 要填「授权码」；CI 里用 SECRET / WSJ_SMTP_PASSWORD）")
    if not smtp.get("from"):
        problems.append("缺少发件地址（kindle.smtp.from，或 FROM / WSJ_SMTP_FROM）")
    if epub.exists() and epub.stat().st_size > 50 * 1024 * 1024:
        problems.append(f"附件 {epub.stat().st_size/1e6:.1f}MB 超过 50MB 邮件上限")

    base_result = {
        "epub": str(epub),
        "recipients": recipients,
        "smtp": f"{smtp.get('host')}:{smtp.get('port')} ssl={smtp.get('ssl')}",
        "env_overrides": sorted(set(applied)),
    }

    if args.dry_run or problems:
        result = {"ok": not problems, "dry_run": args.dry_run, **base_result, "problems": problems}
        if problems:
            result["hint"] = ("先在亚马逊「管理我的内容和设备 → 首选项 → 个人文档设置」里把发件邮箱"
                              "加进已批准列表；QQ 邮箱用 smtp.qq.com:465 + 授权码。")
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0 if not problems else 1

    msg = EmailMessage()
    msg["Subject"] = "convert"
    msg["From"] = f"{smtp.get('from_name', 'WSJ')} <{smtp['from']}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content("Sent by the wsj-kindle pipeline.")
    msg.add_attachment(epub.read_bytes(), maintype="application", subtype="epub+zip",
                       filename=epub.name)

    # 先按配置试，再自动换另一种「端口+加密」组合（配错时就是这个原因报断连）
    configured = (bool(smtp.get("ssl", True)), int(smtp.get("port", 465)))
    order = [configured, (not configured[0], 587 if configured[0] else 465)]
    order = list(dict.fromkeys(order))

    tried: list[dict] = []
    for use_ssl, port in order:
        mode = f"{'SSL' if use_ssl else 'STARTTLS'}:{port}"
        try:
            attempt_send(smtp, msg, use_ssl, port, debug=args.debug)
        except smtplib.SMTPAuthenticationError as exc:
            print(json.dumps({"ok": False, "error": f"SMTP 认证失败: {exc}",
                              "smtp_mode": mode, "tried": tried, **base_result,
                              "hint": "多数邮箱要用「授权码」而不是登录密码（Gmail 用 App Password）"},
                             ensure_ascii=False, indent=None if args.compact else 2))
            return 1
        except Exception as exc:  # noqa: BLE001
            tried.append({"mode": mode, "error": f"{type(exc).__name__}: {exc}"})
            continue

        print(json.dumps({"ok": True, "sent_to": recipients, "epub": epub.name,
                          "size_mb": round(epub.stat().st_size / 1e6, 2),
                          "smtp_mode": mode, "tried_before_success": tried, **base_result},
                         ensure_ascii=False, indent=None if args.compact else 2))
        return 0

    # 两种组合都失败：再跑一次"不带密码"的探针，让日志自己区分
    # 「端口被挡」和「凭据错误导致断连」这两种完全不同的原因
    probe = smtp_probe(smtp["host"]) if smtp.get("host") else []
    reachable = [p for p in probe if p.get("ok")]
    if reachable and all("Disconnected" in t.get("error", "") or "SSLError" in t.get("error", "")
                         for t in tried):
        diagnosis = ("端口能连通但认证阶段被断开 ⇒ 大概率是凭据问题（授权码/发件地址），"
                     "或该 SMTP 拒绝这台机器的 IP")
    elif reachable:
        diagnosis = "端口可连通；再看 tried 里第一条失败的具体报错"
    else:
        diagnosis = "465/587 都不通 ⇒ 这台机器到该 SMTP 被挡（机房 IP 问题），换发信方式或换机器"

    print(json.dumps({"ok": False, "error": tried[-1]["error"] if tried else "unknown",
                      "tried": tried, "probe": probe, "diagnosis": diagnosis,
                      **base_result, "hint": CONN_HINT},
                     ensure_ascii=False, indent=None if args.compact else 2))
    return 1


# 任意 locale 下都要能打印中文：CI/容器里 stdout 可能是 ASCII，
# 那样 print 中文会 UnicodeEncodeError，脚本直接以 exit 1 结束（真实踩过的坑）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

if __name__ == "__main__":
    sys.exit(main())
