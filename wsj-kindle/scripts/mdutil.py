#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 小工具（抓取与渲染共用，避免同一逻辑写两遍）。

目前只有一个函数：fix_paragraphs()。
"""

from __future__ import annotations

import re

#: 列表项：- / * / + 或 1. / 1)
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _split_frontmatter(md: str) -> tuple[str, str]:
    """拆出 YAML frontmatter 与正文。

    frontmatter 里是 `key: value` 逐行排列，**绝不能**被补空行（会破坏 YAML），
    所以修复段落时只处理正文部分。
    """
    if md.startswith("---\n") or md.startswith("---\r\n"):
        end = re.search(r"\n---[ \t]*\r?\n", md)
        if end:
            cut = end.end()
            return md[:cut], md[cut:]
    return "", md


def _is_structural(line: str) -> bool:
    """这些行属于"结构"，不适合在它前后强行补空行。"""
    s = line.lstrip()
    return (s.startswith("#") or s.startswith(">") or s.startswith("|")
            or s.startswith("<") or s.startswith("![") or s.startswith("---"))


def fix_paragraphs(md: str) -> str:
    """把「单换行分隔的段落」补成空行分隔。

    背景：抓回来的正文里，段落之间常常只有一个 `\\n`；而 Markdown 的规则是
    "连续行属于同一段"，于是原文十几个自然段会被并成一大段（EPUB 里特别明显，
    用户反馈过）。这里只在**两边都是普通正文行**的地方补一个空行，
    列表、表格、引用、标题、图片、HTML 块、以及行尾两个空格（显式换行）都不动。
    """
    head, body = _split_frontmatter(md)
    lines = body.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        if i + 1 >= len(lines):
            break
        nxt = lines[i + 1]
        if not line.strip() or not nxt.strip():
            continue                                    # 已经是空行分隔
        if line.endswith("  "):
            continue                                    # 显式软换行，保持同段
        if _is_structural(line) or _is_structural(nxt):
            continue
        if _LIST_RE.match(line) and _LIST_RE.match(nxt):
            continue                                    # 同一列表的连续项
        out.append("")                                  # 补空行 -> 另起一段
    if not out:
        return md
    fixed = "\n".join(out)
    if body.endswith("\n"):
        fixed += "\n"
    return head + fixed
