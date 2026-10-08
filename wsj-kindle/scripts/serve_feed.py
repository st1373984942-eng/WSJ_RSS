#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 public/ 目录用 HTTP 发出去，这样 feed.xml 和图片都能被 RSS 阅读器/KindleEar 访问。

地址形如 http://127.0.0.1:8080/feed.xml —— 要和 config.json 的 feed.base_url 一致。

用法：
    python serve_feed.py                # 前台运行，Ctrl+C 停止
    python serve_feed.py --host 0.0.0.0 # 让局域网里的设备也能访问
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import pathlib
import socketserver
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[feed] {self.address_string()} {fmt % args}\n")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(description="本地托管 RSS feed")
    ap.add_argument("--project", default=str(ROOT))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    project = pathlib.Path(args.project).resolve()
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    port = args.port or int(config.get("server", {}).get("port", 8080))
    public = project / "public"
    public.mkdir(parents=True, exist_ok=True)

    handler = functools.partial(QuietHandler, directory=str(public))
    with ReusableServer((args.host, port), handler) as httpd:
        print(f"feed 已托管: http://{args.host}:{port}/feed.xml")
        print(f"目录: {public}")
        print("Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
