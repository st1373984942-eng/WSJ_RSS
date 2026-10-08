# 用 bpc-fetch 抓华尔街日报中文版 → 生成 RSS → 推送 Kindle

> **⚠️ 本文写于动手实现之前，其中两处已被实测推翻，请看实现后的权威文档：[wsj-kindle/README.md](wsj-kindle/README.md)**
>
> 1. `bpc-fetch discover cn.wsj.com` **实际上拿不到任何文章**——它拼的是 `https://www.{domain}`，
>    对中文站打不到正确主机。发现改用 `https://cn.wsj.com/wsj_cn_google_news.xml`
>    （Google News sitemap，61 篇/48 小时，裸请求即可）。
> 2. **WSJ 中文的正文可以拿到全文**，但必须走 archive.today（`archive.ph/newest/<url>`）。
>    线上页面、Wayback 快照、Googlebot 伪装都只给付费墙预览（前 2~3 段），新式 URL 在 Wayback
>    里甚至没有快照。详见 wsj-kindle/README.md 第 1.3 节。
>
> 下面保留原始描述，实现细节与真实结论以 README 为准。

> 目标链路：**发现文章 → 绕过付费墙取全文 → 合成 RSS → 转 EPUB → 邮件推到 `@kindle.com`**
> 本文里的命令都经过核对（bpc-fetch 0.1.1 / KindleEar 3.3），站点结构变化时以实测为准。

---

## 0. 先看全局：为什么中间必须有"造 RSS"这一步

```
cn.wsj.com/zh-hans/rss   ← 官方中文 feed（只用来做"目录/发现"）
        │ 或  bpc-fetch discover cn.wsj.com
        ▼
bpc-fetch fetch <文章URL> --out-dir ./articles      ← 绕过付费墙，产出全文 Markdown + 图片
        ▼
build_feed.py  →  feed.xml（RSS 2.0 + <content:encoded> 全文）   ← ★ bpc-fetch 不产 RSS
        ▼  静态托管（Caddy / Nginx / GitHub Pages）
KindleEar（或 Calibre recipe）→ 带图带目录的 EPUB
        ▼  SMTP
xxx@kindle.com  →  Amazon 云端转换  →  Kindle
```

三个必须知道的事实，决定了整个架构：

1. **bpc-fetch 只输出 Markdown + 图片目录，没有 RSS 输出能力**，所以"Markdown → RSS"的适配层要自己写（第 5 节给了 60 行脚本）。
2. **RSS 只是中间格式**。bpc-fetch 抓的是付费墙全文，Calibre/KindleEar 抓 RSS 也只会抓摘要——所以正文必须由 bpc-fetch 提供，RSS 里要放 `content:encoded` 全文，不能让下游再去抓一次网页。
3. **Kindle 侧的"转换器"必须能访问到你的 feed URL**。本地 `file://` 不行，所以要么放在有公网地址的 VPS/NAS 上，要么用 GitHub Pages / 内网穿透。

---

## 1. Kindle 侧准备（这一步最容易卡住，先做完）

在亚马逊「管理我的内容和设备 → 首选项 → 个人文档设置」里：

| 项目 | 说明 |
|---|---|
| 收件地址 | `你的设备名@kindle.com`（每台设备一个） |
| 已批准发件人 | **必须**把你用来发信的邮箱加进"已批准的个人文档电子邮箱列表"，否则邮件被静默丢弃 |
| 支持格式 | EPUB、PDF、DOC/DOCX、TXT、HTML、RTF、JPEG/GIF/PNG/BMP |
| 不支持 | **MOBI / AZW 从 2022 年底起不再支持**，所以流水线终点必须是 EPUB |
| 邮件限制 | 一封邮件 ≤ 25 个附件、最多发往 15 个 Kindle 邮箱，附件总量 ≤ 50 MB；主题写 `convert` 可让服务器做转换 |
| 中国区账号 | 中国区 Kindle 电子书店 2023-06-30 已停运，`@kindle.com` 推送请使用非中国区（如 amazon.com）账号 |

参考：[了解如何使用〖发送至Kindle〗电子邮箱](https://www.amazon.co.jp/gp/help/customer/display.html?nodeId=G7NECT4B4ZWHQ8WV)、[Send PDF, EPUB and Other Files to Your Kindle](https://www.amazon.com.au/gp/help/customer/display.html?nodeId=G5WYD9SAF7PGXRNA)

---

## 2. 装 bpc-fetch

bpc-fetch 是命令行工具（最新 0.1.1），复用了 Bypass Paywalls Clean 的绕过逻辑，依赖 `playwright`、`httpx`、`trafilatura`（正文抽取）、`markdownify`。

```bash
# 推荐用 pipx，避免污染全局环境
pipx install bpc-fetch
playwright install chromium

# 或普通 pip（国内可加清华源 -i https://pypi.tuna.tsinghua.edu.cn/simple）
pip install bpc-fetch && playwright install chromium
```

Windows 也可以到 Releases 下载 `bpc-fetch.exe`，然后：

```powershell
bpc-fetch.exe install-browser
bpc-fetch.exe doctor
```

自检与站点覆盖确认：

```bash
bpc-fetch doctor                       # 环境检测，第一步永远先跑这个
bpc-fetch sites --filter wsj           # 看 WSJ 相关域名覆盖了哪些
bpc-fetch discover cn.wsj.com --since today --compact   # 真正的覆盖性验证
```

> **注意**：`data/sites.js` 的站点库来自 Bypass Paywalls Clean，明确列出 WSJ 属于"财经商业"支持站点。但站点库里针对中国站点的专用策略条目需要你实测确认；即使某域名不在 936 列表里，`fetch` 仍能直抓（只是缺少该站定制策略、成功率下降）。用上面的 `discover` 一句话就能验完。

国内网络：`pip` 源和 Playwright 浏览器下载常需要镜像/代理；`block_js` 这类策略靠 Playwright 跑 headless Chromium，走代理会更稳。

---

## 3. 发现当天文章（两种发现源）

**A. 官方中文 RSS（推荐做首选发现源）**

```
https://cn.wsj.com/zh-hans/rss
```

浏览器直接打开正常，但 2024 年起 FreshRSS / RSSHub 等大量阅读器开始 403——华尔街日报强化了 CDN 反爬（见 [FreshRSS #6237](https://github.com/FreshRSS/FreshRSS/issues/6237)、[RSSHub #14924](https://github.com/DIYgod/RSSHub/issues/14924)、[V2EX 讨论](https://global.v2ex.co/t/1026893)）。社区结论是「源本身没停，是 reader 拿不到」。用 HTTP 指纹正常的工具（含 bpc-fetch 的 discover）即可。

**B. bpc-fetch 自带发现（RSS / sitemap / 首页解析 / 浏览器渲染）**

```bash
bpc-fetch discover cn.wsj.com --since today --compact
bpc-fetch discover cn.wsj.com --since 2d --compact > urls.json
```

**C. 栏目页/归档页兜底**（上面都不通时用它做发现，再用 `fetch` 抓正文）：

- 商业：`https://cn.wsj.com/zh-hans/news/business`
- 早间市场快报：`https://cn.wsj.com/zh-hans/news/types/cn-nlt`
- 按日归档：`https://cn.wsj.com/zh-hans/news/archive/2026/05/14`（日期改成你要的那天）

---

## 4. 抓全文

```bash
# 单篇
bpc-fetch fetch "https://cn.wsj.com/zh-hans/articles/xxxx" --out-dir ./articles

# 批量（urls.txt 每行一个 URL）
bpc-fetch batch --file urls.txt --out-dir ./articles

# 跨站关键词爬取（本例用不到，了解一下）
bpc-fetch crawl "AI regulation" --sites cn.wsj.com --since 7d --out-dir ./ai-articles
```

输出结构（每篇一个目录）：

```
article-title/
├── article-title.md      # YAML frontmatter + 正文 + 图片引用
└── images/
    ├── img_000_abc1.jpg
    └── img_001_def2.png
```

绕过策略按站点自动选、失败逐级降级：`ua:custom` → `ua:googlebot` → `referer:google` → `block_js`（Playwright 拦付费墙脚本，425 个站点用这招）→ `archive`（archive.org / archive.is 兜底，274 个站点）→ `cookies`。所有命令都输出 JSON，`--compact` 给精简版，并返回 `next_command`，很适合串进管道或交给 agent。

---

## 5. 把 Markdown 合成 RSS（核心适配层）

存成 `build_feed.py`，跑一次生成 `feed.xml`：

```python
#!/usr/bin/env python3
"""扫 ./articles 下的 bpc-fetch 产物，生成带全文的 RSS 2.0"""
import html, re, pathlib, datetime
import markdown  # pip install markdown

SRC = pathlib.Path("./articles")
OUT = pathlib.Path("./public/feed.xml")
BASE = "https://your.domain/feeds/wsj-cn"   # 公网地址，供图片与 guid 用
MAX_ITEMS = 20                              # 控制体积，别撞 50MB 邮件上限

def parse(md_path: pathlib.Path):
    raw = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    meta, body = ({}, raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"\'')
        body = m.group(2)
    # 图片相对路径 → 绝对 URL，否则 Kindle/EPUB 里全是裂图
    body = re.sub(r"\]\((?!https?://)([^)]+)\)",
                  lambda x: f"]({BASE}/{md_path.parent.name}/{x.group(1)})", body)
    title = meta.get("title") or md_path.stem
    link = meta.get("url") or meta.get("link") or f"{BASE}/{md_path.parent.name}/"
    date = meta.get("date") or meta.get("published") or ""
    try:
        dt = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.datetime.fromtimestamp(md_path.stat().st_mtime,
                                            datetime.timezone.utc)
    return {"title": title, "link": link,
            "html": markdown.markdown(body, extensions=["tables", "fenced_code"]),
            "dt": dt}

items = sorted((parse(p) for p in SRC.glob("*/*.md")),
               key=lambda i: i["dt"], reverse=True)[:MAX_ITEMS]

OUT.parent.mkdir(parents=True, exist_ok=True)
parts = ['<?xml version="1.0" encoding="utf-8"?>',
         '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
         '<channel>', '<title>华尔街日报中文版</title>',
         f'<link>{BASE}</link><description>WSJ 中文精选（全文）</description>',
         f'<lastBuildDate>{datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>']
for it in items:
    parts += ["<item>",
              f"<title>{html.escape(it['title'])}</title>",
              f"<link>{html.escape(it['link'])}</link>",
              f"<guid isPermaLink=\"true\">{html.escape(it['link'])}</guid>",  # guid 必须稳定，否则重复推送
              f"<pubDate>{it['dt'].strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>",
              f"<description><![CDATA[{it['html'][:400]}]]></description>",
              f"<content:encoded><![CDATA[{it['html']}]]></content:encoded>",
              "</item>"]
parts += ["</channel>", "</rss>"]
OUT.write_text("\n".join(parts), encoding="utf-8")
print(f"wrote {OUT} with {len(items)} items")
```

四个容易踩的点，脚本里都已经处理：

- **图片必须是绝对 URL**：Calibre 生成 EPUB 时按 RSS 里的地址取图，相对路径一律裂。
- **`guid` 用文章 URL 且保持稳定**：guid 一变，KindleEar/Calibre 会当成新文章，重复推送。
- **`pubDate` 用 RFC822 格式**（`Thu, 25 Sep 2026 07:00:00 +0000`），不少解析器对 ISO 格式不友好。
- **正文放 `content:encoded`**，`description` 只放个摘要。

把 `public/` 用静态服务器发出去（Caddy 一行配置即可）：

```
your.domain {
    root * /var/www/feeds
    file_server
}
```

→ 得到 `https://your.domain/feed.xml`，这就是给 Kindle 端的输入。不想自建：把 `feed.xml` + 图片推到 GitHub Pages 也行。

---

## 6. 送到 Kindle：三条路线

### 路线 A（推荐）：KindleEar 自托管

[KindleEar](https://github.com/cdhigh/KindleEar) 3.3，Python 3，把 Calibre 的 epub/mobi 生成模块抽出来独立用，原生支持 Calibre recipe 格式、内置 1000+ recipe，能定时推送、也能在线看。

Docker 部署（VPS）：

```bash
wget https://raw.githubusercontent.com/cdhigh/KindleEar/master/docker/ke-docker.sh
chmod +x ke-docker.sh
./ke-docker.sh http://your.domain          # 或改用 docker-compose + Caddy 自动签 SSL
```

群晖/NAS：套件中心装 Docker → 注册表搜 `kindleear/kindleear` → 端口把本机端口映射到容器 `8000`、目录映射到 `/data` 并给读写权限 → 浏览器开 `http://ip:8001`，默认账号 `admin/admin`。

接你的 feed：登录后在「自定义 RSS」里填 `https://your.domain/feed.xml`（**必须是 KindleEar 能访问的公网地址**），设定每天推送时间，配好 SMTP 发件账号，收件人填 `xxx@kindle.com`。之后每天自动出带图带目录的 EPUB 并邮件送达。它还有 AI 摘要（Gemini/Grok/Mistral/Groq 免费额度）、双语对照、TTS 语音、墨水屏在线阅读器（Docker 版）。

### 路线 B：Calibre 桌面端

Windows 机器常开即可，不需要公网：Calibre「抓取新闻」→ 自定义 recipe（或用 `bpc-fetch` 产物包一个本地 recipe），加上「首选项 → 通过网络分享/邮件发送」把结果发到 `@kindle.com`，再配「计划任务」每天跑。缺点是得有一台长期开机的机器。

### 路线 C：干脆不要 RSS

如果只想快点跑通：`bpc-fetch` 抓完 → `pandoc` 或 `ebooklib` 直接合成 EPUB → `smtplib` 带附件发到 `@kindle.com`。少一个 feed 托管环节，但丧失了 RSS 的通用性（后续想换阅读器/在线阅读就没了）。

---

## 7. 定时与运维

cron 一条龙（每天 7:00）：

```cron
0 7 * * * cd /opt/wsj && bpc-fetch discover cn.wsj.com --since 1d --compact > urls.json \
  && jq -r '.articles[].url' urls.json > urls.txt \
  && bpc-fetch batch --file urls.txt --out-dir ./articles \
  && python3 build_feed.py
```

- **去重**：维护 `seen.json` 记录已推送 URL，`build_feed.py` 里过滤，避免同一篇反复出现。
- **体积**：默认只保留最近 20 篇（`MAX_ITEMS`），并把图片压到合适尺寸，别撞 50 MB 邮件上限。
- **失败排查顺序**：`bpc-fetch doctor` → 看是否走 `archive` 兜底（说明主策略被拦）→ 换代理 → 查 KindleEar 的 `./data/gunicorn.error.log` → 核对亚马逊"已批准发件人"。

常见坑速查：

| 现象 | 原因 / 处理 |
|---|---|
| `discover` 拿到 0 篇 | cn.wsj.com 反爬；换代理、或改用栏目页/归档页发现 |
| 阅读器 403、但浏览器正常 | CDN 拦 HTTP 指纹；用 bpc-fetch/带正常 UA 的工具 |
| EPUB 里图片裂 | RSS 中图片是相对路径，改成绝对 URL |
| 邮件没到、也没报错 | 发件邮箱不在「已批准的个人文档电子邮箱列表」 |
| 每次推送重复文章 | `guid` 不稳定的问题，固定用文章 URL |
| 推送失败提示格式 | 还在发 MOBI/AZW，改发 EPUB |
| feed 更新了但 Kindle 没变 | KindleEar 拉不到你的公网 feed（本地路径/防火墙） |

---

## 8. 一句合规提醒

bpc-fetch 属于绕过付费墙的工具，上述流水线适合**个人订阅阅读**。生成的全文 feed 请勿公开托管或对外分发——公开的全文 RSS 等于再发布受版权保护的新闻内容，性质和自用完全不同。

---

## 附：最短可跑路径

```bash
# 1) 装
pipx install bpc-fetch && playwright install chromium && bpc-fetch doctor

# 2) 发现 + 抓全文
bpc-fetch discover cn.wsj.com --since today --compact > urls.json
jq -r '.articles[].url' urls.json > urls.txt
bpc-fetch batch --file urls.txt --out-dir ./articles

# 3) 造 RSS 并托管
python3 build_feed.py      # → ./public/feed.xml
caddy file-server --root ./public --listen :8080

# 4) KindleEar 里订阅 http://<你的地址>:8080/feed.xml，收件人填 xxx@kindle.com
```
