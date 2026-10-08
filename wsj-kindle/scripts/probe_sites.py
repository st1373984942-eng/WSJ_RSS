#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""穷尽列出 bpc-fetch 站点库里与 WSJ 相关的条目，并实测一批 URL 会匹配到什么策略。

bpc-fetch 的关键行为：`domain_from_url()` 会把主机名归约成"可注册域"，
所以 cn.wsj.com / blogs.wsj.com 这类子域全部命中同一条 wsj.com 记录。

用法：python probe_sites.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bpc_fetch.sites import (  # noqa: E402
    SITES_JS_DEFAULT, domain_from_url, get_sites_map,
)

KEYWORD = re.compile(r"(?i)wsj|wall street|dow jones|barrons|marketwatch|cxense")

CANDIDATE_URLS = [
    "https://wsj.com",
    "https://www.wsj.com/",
    "https://www.wsj.com/articles/some-slug-11614828611",
    "https://www.wsj.com/news/business",
    "https://wsj.com/podcasts/the-journal",
    "https://blogs.wsj.com/",
    "https://www.wsj.com/xml/rss/3_7031.xml",
    "https://cn.wsj.com",
    "https://cn.wsj.com/zh-hans",
    "https://cn.wsj.com/zh-hans/rss",
    "https://cn.wsj.com/articles/canadian-poet-anne-carson-awarded-nobel-prize-in-literature-de70b1b8",
    "https://cn.wsj.com/zh-hant/news/china",
    "https://www.barrons.com/articles/some-slug-51614828611",
    "https://barrons.com",
    "https://www.marketwatch.com/story/some-story",
    "https://marketwatch.com",
    "https://www.dowjones.com/",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://www.wsj.com/zh-hans",  # 不存在，但看它归约到哪
]


def main() -> int:
    sites = get_sites_map(SITES_JS_DEFAULT)
    print(f"sites.js: {SITES_JS_DEFAULT}")
    print(f"站点总数: {len(sites)}\n")

    # 1) 站点库里所有相关条目（按解析后的字段列，这才是 bpc-fetch 真正用的）
    print("=== 1. 站点库中与 WSJ/Dow Jones 相关的条目 ===")
    hits = {d: s for d, s in sites.items() if KEYWORD.search(d) or KEYWORD.search(s.name or "")}
    for domain, s in sorted(hits.items()):
        raw = {k: v for k, v in s.to_dict().items() if v not in ("", False, [], None)}
        print(f"\n  domain : {domain}")
        print(f"  name   : {s.name}")
        print(f"  策略   : {s.bypass_type()}")
        print(f"  解析出的字段: {json.dumps(raw, ensure_ascii=False)}")
    print(f"\n  合计 {len(hits)} 条")

    # 2) 实测 URL 归约与匹配
    print("\n=== 2. 实测：这些 URL 会匹配到什么 ===")
    print(f"  {'URL':<62} {'归约域':<18} {'匹配条目':<26} 策略")
    for url in CANDIDATE_URLS:
        domain = domain_from_url(url)
        s = sites.get(domain)
        name = s.name if s else "—（不支持）"
        strategy = s.bypass_type() if s else "—"
        print(f"  {url[:60]:<62} {domain:<18} {name[:24]:<26} {strategy}")

    # 3) search --urls 用的 is-supported 判断
    print("\n=== 3. bpc-fetch search --urls 会认为哪些是 supported ===")
    from bpc_fetch.search import filter_urls
    res = filter_urls(CANDIDATE_URLS, set(sites.keys()))
    for r in res:
        print(f"  supported  {r['domain']:<12} {r['url'][:70]}")
    print(f"  共 {len(res)}/{len(CANDIDATE_URLS)} 条被判为支持")

    # 4) 站点库里有 "##" 分组条目吗（可能藏着别的 WSJ 域）
    groups = [d for d, s in sites.items() if d.startswith("#")]
    print(f"\n=== 4. 分组/特殊条目: {groups or '无'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
