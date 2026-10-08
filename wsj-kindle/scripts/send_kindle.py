#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 EPUB 通过 Amazon「发送至 Kindle」邮箱推到 Kindle。

用标准库 smtplib，不依赖任何第三方服务。要点（易踩）：
  * 发件邮箱必须已加入「已批准的个人文档电子邮箱列表」，否则邮件被静默丢弃。
  * 主题写 convert，Amazon 会按需转换格式。
  * 2022 年底起 Amazon 不再接受 MOBI/AZW，只能发 EPUB/PDF/DOCX 等。
  * 单个附件 ≤ 50MB 比较稳妥（邮件上限），一封最多 25 个附件。

凭据可以放 config.json，也可以用环境变量覆盖（CI 里必须用后者，别把密码提交进仓库）。
支持两种命名：
  * 本项目风格：WSJ_KINDLE_ENABLED / WSJ_KINDLE_TO / WSJ_SMTP_HOST / WSJ_SMTP_PORT /
    WSJ_SMTP_SSL / WSJ_SMTP_USER / WSJ_SMTP_PASSWORD / WSJ_SMTP_FROM / WSJ_SMTP_FROM_NAME
  * 书伴 Calibre-News-Delivery 风格：TO / FROM / SMTP / PORT / ENCRYPT / SECRET

用法：
    python send_kindle.py --epub out/xxx.epub --dry-run     # 只校验配置
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
from email.message import EmailMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def main() -> int:
    ap = argparse.ArgumentParser(description="推送 EPUB 到 Kindle 邮箱")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--epub", required=True)
    ap.add_argument("--to", default=None, help="覆盖收件地址，逗号分隔")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    overrides, applied = apply_env_overrides(config)
    kindle = overrides["kindle"]
    smtp = kindle.get("smtp", {})

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

    host, port = smtp["host"], int(smtp.get("port", 465))
    try:
        if smtp.get("ssl", True):
            with smtplib.SMTP_SSL(host, port, timeout=120,
                                  context=ssl.create_default_context()) as s:
                s.login(smtp["username"], smtp["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=120) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(smtp["username"], smtp["password"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        print(json.dumps({"ok": False, "error": f"SMTP 认证失败: {exc}", **base_result,
                          "hint": "多数邮箱要用「授权码」而不是登录密码"},
                         ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", **base_result},
                         ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, "sent_to": recipients, "epub": epub.name,
                      "size_mb": round(epub.stat().st_size / 1e6, 2), **base_result},
                     ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
