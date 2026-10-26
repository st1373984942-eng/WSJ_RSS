#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机网络环境的小工具。

存在的唯一理由：**不要把代理端口写死在提示信息里**。
Clash 的默认端口换过（127.0.0.1:7892 → 127.0.0.1:7890），而脚本里写死端口的
提示会把「代理换了端口」误报成「代理没开」，害得整轮发现阶段失败
（真实踩过：2026-10-24、2026-10-25 两天全灭，日志里是
 `ConnectError: [WinError 10061] 由于目标计算机积极拒绝`）。
"""

from __future__ import annotations

import os


def system_proxy() -> str:
    """返回 Windows 当前生效的系统代理，读不到就回落到环境变量。

    返回值给提示信息用，例如 `127.0.0.1:7890`；完全没配时返回「未检测到」。
    """
    try:
        import winreg  # noqa: PLC0415  —— 只有 Windows 有

        key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            server = winreg.QueryValueEx(handle, "ProxyServer")[0]
        if server:
            return str(server)
    except Exception:  # noqa: BLE001  —— 非 Windows / 注册表被锁都只是"读不到"
        pass
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return "未检测到（系统代理没开）"
