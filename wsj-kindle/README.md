# wsj-kindle — 华尔街日报中文版 → RSS → Kindle

抓华尔街日报中文版（cn.wsj.com），合成带全文的 RSS，转 EPUB 推到 Kindle。全部在本机跑。

> **当前只推送中文版**（本目录）。英文站政治板块 `../wsj-politics/` 的定时任务已摘除——
> 项目文件还在仓库里（同一套 sitemap + archive.today 方法，共用本目录的 `scripts/`，
> 靠 `--project` 指向各自配置），但没有任何 workflow / timer 会跑它，想恢复随时能加回来。

---

## 0. 结论（本机实测，2026-10-08）

> **⚠️ 2026-10-15 起站点变了：全文拿不到了。** WSJ 中文站对未登录读者改成硬付费墙，
> 每篇只剩 2 段（约 150~250 字），archive.today 的新快照也是这个空壳。
> 实测证据与三条出路见 [第 9 节](#9-2026-10-15-起wsj-中文站改成硬付费墙全文拿不到了)。
> 下表的"✅ 全文"记录的是当时（10-08）的状态。

| 环节 | 状态 | 说明 |
|---|---|---|
| 发现文章 | ✅ | `https://cn.wsj.com/wsj_cn_google_news.xml`，61 篇/48 小时，**裸请求即可，不需要 Referer** |
| 抓取正文 | ✅ **全文** | archive.today 拿全文（中位 1333 字，最长 4594 字），不是预览 |
| 生成 RSS | ✅ | `public/feed.xml`，全文在 `content:encoded`，图片转绝对 URL |
| 生成 EPUB | ✅ | Calibre `ebook-convert`，Kindle 输出配置 |
| 推送 Kindle | ✅ | SMTP 发到 `@kindle.com`，填好配置即可 |
| 定时运行 | ✅ | `run_daily.cmd` + Windows 任务计划程序 |

最近一次完整运行：

```
discover  中文 Google News sitemap 61 篇 → 取最新 30 篇（自带中文标题 + 发布时间）
fetch     30/30 成功，29 篇来自 archive.today、1 篇来自线上，付费墙预览 0 篇
          正文中位 1333 字、合计 44236 字（最长 4594 字）
feed      feed.xml 30 条，57 张图，guid 唯一，content:encoded 非空
epub      wsj-cn-20261009-0011.epub，14.2 MB，58 张图，mimetype/container/CRC 全合法
selfcheck 全部 ok: true
```

---

## 1. 四个关键事实

### 1.1 DataDome：`Referer: https://www.google.com/` 是中文站的钥匙

cn.wsj.com 挂了 DataDome，裸请求一律 401（挑战页）。实测对照：

| 请求 | 无 Referer | 带 Google Referer |
|---|---|---|
| `/zh-hans/rss` | 401 | **200，36 条** |
| `/zh-hans/news/business` | 401 | 200 |
| 文章页 | 401 | 200（付费墙预览） |
| `/robots.txt`、`/sitemap.xml`、`/wsj_cn_google_news.xml` | **200** | 200 |

最后一行很关键：**sitemap 完全不受 DataDome 保护**，所以发现环节根本不需要绕。
（Googlebot 伪装无效；headless Chromium 和真实 Edge 的无头模式也都被挡。）

### 1.2 发现走 sitemap，比官方 RSS 更好

`robots.txt` 里声明了中文站的四个 sitemap，其中 `wsj_cn_google_news.xml` 是 Google News sitemap
（按规范只含最近 48 小时）：

| 发现源 | 结果 |
|---|---|
| `/zh-hans/rss`（官方 RSS） | 36 条，需要 Google Referer |
| **`/wsj_cn_google_news.xml`** | **61 条，裸请求即可，每篇自带 `news:title`（中文标题）+ `news:publication_date`** |

所以现在用 sitemap 做发现（`discover_script = wsj_discover_sitemap.py`，
`path_prefixes: ["articles/"]`）——篇数更多、更稳、还省掉一个可能被反爬的环节。

### 1.3 全文走 archive.today ⚠️（这一条我一开始判断错了，已修正）

中文站的正文**能拿到全文**。我曾经只测了线上页面、Wayback、AMP 和第三方镜像，
就下了"没有订阅 cookie 就只能拿预览"的结论——**漏测了 archive.today**，这个结论是错的。

同一批文章的对照（trafilatura 抽取的正文字数）：

| 来源 | AI价格战 | AI数学难题 | GLP-1 | Manus |
|---|---|---|---|---|
| 线上（带 Google Referer） | 643 | 1529 | 485 | 530 |
| Wayback 今天的新鲜快照 | 与线上**逐字相同**，都是预览 | | | |
| Wayback 2024 年旧快照 | 282 | | | |
| AMP 快照 | 只有 2024-03 之前的（站点后来取消 AMP 了） | | | |
| **archive.today** | **2467** | 1850* | **3215** | 628 |

\* 表中 archive.today 那行为实测正文字数；带 `*` 的是抓取当时的数字。
关键判据不是字数而是**结尾**：全文会以 `Copyright ©2026 Dow Jones & Company` 或
`Appeared in the ... print edition as ...` 收尾，而预览断在"订阅《华尔街日报》，畅读全文"。

30 篇的最终结果：29 篇来自 archive.today、1 篇来自线上兜底、**0 篇仍是预览**。

之所以以前会误判，还有一个技术原因：**存档页的 HTML 里仍然带着「畅读全文」的样板文字**，
所以判断"是不是全文"必须看**提取出来的正文**，不能 grep 原始 HTML
（`wsj_fetch_multi.py` 里 `looks_paywalled()` 就是按这个原则写的）。

### 1.4 bpc-fetch 在本项目里的角色

抓取主力已经从 bpc-fetch 换成 `wsj_fetch_multi.py`（多源：archive.today → bpc-fetch → 线上），
bpc-fetch 现在是**兜底来源**之一，以及它提供的两样东西仍然在用：
`domain_from_url`/站点策略表，和 `trafilatura + markdownify + 图片下载` 的抽取/落盘逻辑。

站点库里 WSJ 相关条目（`bpc-fetch sites --filter wsj` 只能看到 1 条）：

| 库中域名 | 名称 | 策略 | 位置 |
|---|---|---|---|
| `wsj.com` | The Wall Street Journal | `archive` | sites.js:3350 |
| `barrons.com` | Barron's | `block_js` | sites.js:268 |
| `marketwatch.com` | MarketWatch | `block_js` | sites.js:2011 |
| `cxense.com` | Cxense | `cookies` | sites.js:3596（第三方脚本域，非新闻站） |

`domain_from_url()` 把主机名归约成"可注册域"，所以 `cn.wsj.com`、`blogs.wsj.com`、
`www.wsj.com` 全部命中同一条 `wsj.com` 记录，路径不参与匹配。
**中文站没有专用条目**——这也是当初 bpc-fetch 抓中文站只能拿到预览的原因之一
（它的 `_is_paywalled()` 只认英文标记，中文的"畅读全文"识别不了，于是不会触发兜底）。

另外两个坑（看源码发现的，别去"修"）：

1. 库里写的 `referer_custom: https://www.drudgereport.com/` **没有被解析**——
   `SiteStrategy` 只读 `referer`，实际生效的是 `build_headers()` 里"未指定 UA 时补
   `Referer: https://www.google.com/`"的兜底逻辑。**中文站能访问恰恰靠这个意外的 Google referer**，
   所以别给这条记录加 UA 或改策略，否则会把中文站一起打死。
2. Cxense 那条 `excluded_domains` 里虽然列了 `wsj.com`，但该字段不在数据类里，形同虚设。

---

## 2. 目录结构

```
wsj-kindle/
├── config.json                    # 唯一需要改的配置
├── run_daily.cmd                  # 任务计划程序入口
├── urls.txt                       # 每轮待抓 URL（自动生成）
├── articles/<标题>/<标题>.md       # 抓取产物（+ images/）
├── public/
│   ├── feed.xml                   # ★ RSS 成品（全文在 content:encoded）
│   ├── manifest.json
│   ├── digest.html                # 浏览器可直接看的今日合辑
│   └── images/<标题>/...
├── out/wsj-cn-<时间>.epub          # ★ 推给 Kindle 的成品
├── data/{discovered,seen,run_slugs}.json
├── logs/pipeline.log
└── scripts/                       # 流水线脚本（已停用的 ../wsj-politics 也复用这里）
    ├── wsj_discover_sitemap.py    # 【本项目在用】sitemap 发现
    ├── wsj_fetch_multi.py         # 【本项目在用】archive.today/Wayback/线上 多源取全文
    ├── wsj_discover.py            # RSS 发现（保留，Google Referer 那条路）
    ├── wsj_fetch.py               # 纯 bpc-fetch 抓取（保留，作对照/兜底）
    ├── build_feed.py / build_epub.py / send_kindle.py / serve_feed.py
    ├── prune_old.py               # 【本项目在用】清理过期素材（默认保留 3 天）
    ├── run_pipeline.py / selfcheck.py
    └── probe_*.py                 # 当初摸站点行为用的探针（留作复查）
```

---

## 3. 快速开始

本机解释器：`F:\Program Files\Python\python.exe`（Python 3.15、bpc-fetch 0.1.1、
Playwright 1.63、markdown）；Calibre 在 `F:\Program Files\Calibre2\`。

```powershell
cd D:\Documents\deepseek-harness\default-workspace\wsj-kindle

# 一条命令跑完（清理过期素材 → 发现 → 抓取 → RSS → EPUB，kindle.enabled=true 时再发邮件）
& 'F:\Program Files\Python\python.exe' scripts\run_pipeline.py --project .
.\run_daily.cmd                      # 同上，会写 logs\pipeline.log

# 托管 RSS（另开窗口常驻）
& 'F:\Program Files\Python\python.exe' scripts\serve_feed.py --project .
#   → http://127.0.0.1:8080/feed.xml

& 'F:\Program Files\Python\python.exe' scripts\selfcheck.py --project .   # 自检
```

单步调试（都要带 `--project .`，否则会用脚本所在项目）：

```powershell
python scripts\wsj_discover_sitemap.py --project .          # 只看发现
python scripts\wsj_fetch_multi.py --project . --limit 3 --force
python scripts\wsj_fetch_multi.py --project . --sources bpc_fetch   # 只用一个来源
python scripts\build_feed.py --project .                    # 只重建 RSS
python scripts\build_epub.py --project . --all              # 用全部文章出 EPUB
```

### 接到 Kindle

**A. 邮件推送（推荐）**

1. 亚马逊「管理我的内容和设备 → 首选项 → 个人文档设置」：拿到 `xxx@kindle.com`，
   并把发件邮箱加进**已批准的个人文档电子邮箱列表**（不加会被静默丢弃）。
   注意中国区 Kindle 书店 2023-06-30 已停运，请用非中国区账号。
2. 改 `config.json` 的 `kindle`（收件地址 + SMTP 授权码），`enabled` 改 `true`。
3. 先干跑校验，再真发：

```powershell
python scripts\send_kindle.py --project . --epub out\xxx.epub --dry-run
python scripts\run_pipeline.py --project .
```

> 邮件主题写的是 `convert`；Amazon 2022 年底起不再收 MOBI/AZW，所以生成的是 EPUB；
> 单封附件总量别超 50MB（`send_kindle.py` 会提前检查，当前 30 篇约 14MB）。

**B. 用 RSS 阅读器 / KindleEar**

`feed.xml` 是标准 RSS 2.0，含全文 + 绝对图片地址：本机订阅
`http://127.0.0.1:8080/feed.xml`；局域网设备用 `--host 0.0.0.0`；
[KindleEar](https://github.com/cdhigh/KindleEar)（Docker）在「自定义 RSS」里填这个地址，
它定时转成带图带目录的 EPUB 再推到 `@kindle.com`（那时 feed 需要在 KindleEar 能访问到的地址上）。

### 每天自动跑（本机方案，当前采用的就是它）

本机现在注册了带**唤醒**的计划任务 `WSJ-Kindle-Daily`：每天 07:00，机器睡着也会被叫醒跑一次，
跑完继续睡（机器彻底关机则叫不醒）。注册命令（一次即可，已执行过）：

```powershell
$ws = 'D:\Documents\deepseek-harness\default-workspace'
$action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$ws\wsj-kindle\run_daily.cmd`"" -WorkingDirectory "$ws\wsj-kindle"
$trigger  = New-ScheduledTaskTrigger -Daily -At 07:00
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'WSJ-Kindle-Daily' -Action $action -Trigger $trigger -Settings $settings `
  -Description 'WSJ 中文版每日推送到 Kindle（唤醒计算机执行）'
```

常用操作：

```powershell
Start-ScheduledTask  -TaskName 'WSJ-Kindle-Daily'     # 立刻试跑（不用等到 07:00）
Get-ScheduledTaskInfo -TaskName 'WSJ-Kindle-Daily'    # 看上次结果/下次运行时间
Disable-ScheduledTask -TaskName 'WSJ-Kindle-Daily'    # 临时停用
Get-Content ".\logs\pipeline.log" -Tail 30            # 看运行日志
```

**凭据放在 `secrets.local.cmd`**（本机专有，已在 `.gitignore` 里，**绝不入库**）。
从模板复制一份再填两行即可：

```powershell
copy .\secrets.local.cmd.example .\secrets.local.cmd
notepad .\secrets.local.cmd      # 填 FROM（生成授权码的那个邮箱）和 SECRET（授权码）
```

没填或还是占位符时，流水线会**明确报错并回滚状态**（不会静默成功、也不会白记一篇已投递）。

### 云端路线：为什么现在没在用

已实测：**archive.today 对 GitHub runner 的机房 IP 全部返回 HTTP 429**（5 个镜像无一例外），
而它是本流水线唯一的全文来源。所以云端只能拿到约 200 字的付费预览——
按现在的策略（`max_preview_attempts = -1`）它**什么也不会推**。
云端定时因此在 workflow 里被注释掉了，只保留手动触发；哪天真换了住宅网络的常开设备，
再启用 [deploy/README-server.md](../deploy/README-server.md) 那条 systemd 路线。

| 方案 | 全文 | 本机关机也发 | 说明 |
|---|---|---|---|
| **本机计划任务 + 唤醒**（当前） | ✅ | ❌（需要通电，睡眠可唤醒） | 本机出网是住宅 IP，archive.today 正常 |
| 家里常开设备（NAS/树莓派/旧电脑） | ✅ | ✅ | 唯一同时满足两条的路线 |
| GitHub Actions | ❌（429） | ✅ | runner 机房 IP 被 archive.today 限流 |

不管走哪条，**第一步都一样**：先跑 `python3 deploy/preflight.py`，它会逐项报告依赖、Calibre、
sitemap 和 archive.today 的可用性，并且会打印出口 IP——**能不能拿到全文，就看这台机器能不能访问 archive.today**。

---

## 4. 配置项（`config.json`）

| 键 | 说明 |
|---|---|
| `pipeline.discover_script` / `fetch_script` | 本项目用 `wsj_discover_sitemap.py` + `wsj_fetch_multi.py` |
| `discover.sitemaps` | `["https://cn.wsj.com/wsj_cn_google_news.xml"]` |
| `discover.path_prefixes` | `["articles/"]`（中文站文章都在 `/articles/` 下） |
| `discover.since` | 时间窗口，默认 `2d`（sitemap 本身只覆盖 48 小时） |
| `discover.use_month_fallback` | 中文站设为 `false`（没有对应的当月 sitemap） |
| `fetch.sources` | 取文顺序：`archive_today` → `bpc_fetch` → `live` |
| `fetch.delay_seconds` | archive.today 的请求间隔（默认 3 秒，别调太小） |
| `fetch.good_enough_words` | 拿到这么多"字/词"就不再试后面来源（默认 500） |
| `fetch.max_preview_attempts` | 要不要接受预览：**`-1`（默认）= 永不接受**，`0` = 立刻接受，`N` = 推迟 N 次后接受。见第 6 节 |
| `fetch.cookie` | 可选；填了订阅 cookie 后 `live` 也能直接拿全文 |
| `fetch.paywall_note` | 只拿到预览时附加的提示语 |
| `feed.base_url` / `server.port` | 本项目用 8080（英文项目用 8081） |
| `feed.author` | 默认"华尔街日报中文版" |
| `retention.article_days` | 新闻素材保留天数（**默认 3**）：更老的文章目录连同配图一起删除，见第 4.1 节 |
| `retention.epub_days` | 历史 EPUB 保留天数（默认 3） |
| `retention.ledger_days` | `seen.json` 条目保留天数（默认 30，远超 48 小时窗口，安全） |
| `retention.enabled` | `false` 则完全不清理 |

---

## 4.1 自动清理过期新闻（`scripts/prune_old.py`）

素材会一天天堆积（实测积累到 100 篇文章 + 217 张图 + 16 本旧 EPUB ≈ **120 MB**），
所以流水线的**第一步**就会清理过期素材，默认只留最近 **3 天**：

```
[prune] 删掉 articles/ 里超过 3 天的文章（按 frontmatter 的 date:）
        删掉 public/images/ 里已经对应不上文章的孤儿图片目录
        删掉 out/ 里超过 3 天的历史 EPUB
        把 seen.json 里超过 30 天的条目收掉（早已出了 48 小时 sitemap 窗口）
```

实测一次清理：**删 39 篇文章 + 37 个孤儿图片目录，释放 41.2 MB**，随后 RSS/EPUB 构建与自检全部正常。

手动查看/执行：

```powershell
python scripts\prune_old.py --project . --dry-run     # 只报告会删什么，不动文件
python scripts\prune_old.py --project .               # 按 config.json 执行
python scripts\prune_old.py --project . --days 7      # 临时改成保留 7 天
python scripts\run_pipeline.py --project . --skip-prune   # 本次流水线跳过清理
```

几个安全设计：

- **判断依据是新闻发布日期**（frontmatter 的 `date:`），缺失时退回文件修改时间 —— 不是"抓取时间"
- 边界按"超过 N 天"算：今天 10-11、保留 3 天时，cutoff 是 10-08，**10-08 当天的文章会保留**
- 删除前逐个校验路径确实位于项目目录内；`--days` 最小为 1，避免误设成 0 把素材全清空
- 日期缺失或异常的账本条目**一律保留**，宁可多留也不误删导致重复推送
- 清理放在发现/构建**之前**，所以 RSS 与 EPUB 只会包含保留期内的文章（也顺带杜绝了
  "历史遗留的旧预览文件被 `--all` 捞进成品"这类问题）
- 清理失败**不算流水线失败**（`required=False`），最多是这次没腾出空间

---

## 4.2 EPUB 的目录（按新闻标题生成）

EPUB 里其实有**三套**目录，缺一套就会出现"在某些阅读器里看不到目录"的情况——所以都做了：

| 组成 | 内容 | 作用 |
|---|---|---|
| **可见目录页** | 第一页就是「目录」+ 30 条可点击标题 | 翻书就能看到；不依赖阅读器的菜单 |
| **NCX 目录** | 每篇一个 navPoint，文本即标题 | 阅读器/Kindle 的「前往 → 目录」菜单 |
| **guide 指向** | `<reference type="toc" href="目录页">` | 告诉阅读器"目录"入口在哪 |

两个引擎都产出这三样：

- **Calibre 引擎**：`digest.html` 顶部插入目录块（标题用 `<p>` 而非 `<h2>`，否则
  `--level1-toc=//h:h2` 会把"目录"自己也收成一条），再配
  `--level1-toc=//h:h2`（NCX = 标题）、`--chapter=//h:h2`（按篇分章）、
  `--page-breaks-before=//h:h2`（每篇另起一页）；生成后由 `patch_epub_guide()`
  把 guide 的 toc 指向补进 OPF。
- **纯 Python 引擎**：自己写 `nav.xhtml`（放在 spine **第一位**，所以打开即目录页）、
  `toc.ncx` 与 guide。

验证方式（改完 EPUB 相关代码请重跑）：

```powershell
python scripts\build_epub.py --project . --all          # 出成品
python scripts\selfcheck.py  --project .                # 合法性/图片/zip
# 再转一次 Kindle 格式，确认目录没在转换中丢掉
& 'F:\Program Files\Calibre2\ebook-convert.exe' out\xxx.epub out\rt.azw3
& 'F:\Program Files\Calibre2\ebook-convert.exe' out\rt.azw3 out\rt.epub
```

实测（30 篇）：NCX 30 条、可见目录页 30 条链接**全部可达**（`30/30`）、guide 有 toc、
`EPUB → AZW3 → EPUB` 之后 NCX 仍是 30 条标题 —— 说明 Send to Kindle 之后目录还在。

---

## 5. 排错表

| 现象 | 原因 / 处理 |
|---|---|
| 发现 0 条，notes 报连接超时 / `WinError 10061 目标计算机积极拒绝` | **系统代理没开，或者换了端口**；直连 wsj 系是不通的。端口别猜，看 Windows 设置 → 网络 → 代理（Clash 曾从 `7892` 改成 `7890`，害死过两天的定时任务） |
| 某篇 `archive_today:no_snapshot` | archive.today 上还没人存过这篇，会自动退到 bpc-fetch/线上；都不行就跳过该篇 |
| 三源全失败：`archive_today:404; wayback:404; live:401` | **刚发布的文章还没被任何存档站收录**，属正常现象。流程会跳过 EPUB 并以**退出码 0** 结束，且该 URL 不写入 `seen.json`，下次运行自动重试（48 小时窗口内基本都会补上） |
| 大量 429 / 抓取变慢 | archive.today 限流；把 `fetch.delay_seconds` 调到 5~8 秒，或减少单轮篇数 |
| 文章很短且带 `paywall_note` | 三个来源都只给了预览；填 `fetch.cookie` 可解决 |
| **书里混进"概要/预览"** | 见下面「为什么书里会混进概要」一节；机制上现在会自动推迟这类文章 |
| RSS 阅读器订阅 `127.0.0.1` 报 502 | 本机系统代理被 Python 客户端读到但没生效例外表；给该程序设 `NO_PROXY=127.0.0.1,localhost`（浏览器走 WinINET，`127.*` 已在例外里，不受影响） |
| Kindle 收不到、也没报错 | 发件邮箱不在「已批准的个人文档电子邮箱列表」里 |
| 推送报认证失败 | QQ/163 要用**授权码**，不是登录密码 |
| 每次推送重复文章 | `guid` 必须稳定（现在用文章 URL），别改成带时间戳的值 |
| EPUB 里图片裂 | 图片 URL 必须是绝对路径（`build_feed.py` 已强制），且 `feed.base_url` 要和实际访问地址一致 |
| 单轮跑太久 | 抓取是串行的（对 archive.today 限速），30 篇约 3~5 分钟；要快就调小 `discover` 的篇数 |
| 日志 | `logs/pipeline.log`；任务计划程序里也能看上次结果 |

---

## 6. 为什么书里会混进"概要"，以及现在的处理

**成因**：archive.today 是靠别人访问时顺手存档的，**有滞后**。文章刚发布那几小时，快照往往还不存在，于是 `fetch` 退到线上预览（约 170~440 字），书里就出现"只有开头的概要"。

实测（2026-10-09）：

| 同一批 30 篇的抓取时间 | 结果 |
|---|---|
| 当晚 23:04（刚发布不久） | **19 篇是付费预览**，只有 11 篇全文 |
| 几小时后重抓 | **10/10 全部 archive.today 全文**（平均 1948 字） |

### 现在的策略：**宁可不发，也不发概要**

`fetch.max_preview_attempts` 控制"要不要接受预览"，默认 **`-1` = 永不接受**：

| 取值 | 行为 |
|---|---|
| **`-1`（默认）** | 只有预览就一直推迟：不写正文、不写 `seen.json`，下次运行继续重试；直到 archive.today 有快照才发出 |
| `0` | 不推迟，立刻接受预览（旧行为，书里会出现概要） |
| `N > 0` | 推迟 N 次后接受（`N=1` 就是"第二天还是没有就发概要"） |

配套的两道保险：

1. **全是预览就不推书**：即使把参数调成会接受预览，只要本轮 `full_text == 0`，`run_pipeline` 也会跳过
   RSS/EPUB/推送，并把状态回滚下次重抓（摘要里显示 `stopped: "本轮只有付费预览，已跳过推送"`）。
2. **archive.today 多镜像轮换**：`archive.ph / archive.is / archive.li / archive.md / archive.today`
   依次尝试（同一家服务但边缘节点不同），并把失败原因记进日志，例如
   `archive.ph:blocked; archive.is:HTTP 403` —— 一眼能看出是"IP 被挡"还是"确实没存档"。

### 代价：某天可能什么都不发

因为默认不接受预览，**如果当天的文章在 archive.today 上都还没存档，这一天就不会有书**。
摘要里会写 `deferred_preview: N` 和具体标题，说明是"在等全文"而不是出错。
那些文章还会在 48 小时的 sitemap 窗口内被重试；窗口一过就不再出现（宁可漏发，也不发概要）。

> ⚠️ **如果连续多天 `deferred_preview` 都等于当天总数**，说明这台机器根本拿不到 archive.today 的全文
> （典型是机房 IP 被挡，见下面第 5 节排错表）。那时"只发全文"就等于"永远不发"，
> 需要改策略：换一台能访问的机器（家里 NAS / 常开的 PC），或把 `max_preview_attempts` 改成 `0` 接受概要。

---

## 7. 每次改动的自检

```powershell
python scripts\selfcheck.py --project .     # feed 合法性 / 图片是否齐全 / EPUB 是否合法 zip
python scripts\probe_cn_sitemap.py          # sitemap 里有多少篇（换站点结构后复查）
python scripts\probe_cn_archive_today.py    # archive.today 对中文文章的覆盖情况
```

---

## 8. 合规提醒

archive.today 与 bpc-fetch 同属绕过付费墙的取文手段，这条流水线适合**个人订阅阅读**。
`public/feed.xml` 含新闻全文，**只在你自己的机器/局域网内托管**，不要公开分享或对外分发——
公开的全文 RSS 等于再发布受版权保护的内容。

---

## 9. 2026-10-15 起：WSJ 中文站改成硬付费墙，全文拿不到了

### 现象

| 日期 | fetch 结果 |
|---|---|
| 10-12 ~ 10-14 | `success 30/30`，30 篇全文，平均 1509~1823 字，全部来自 archive.today |
| **10-15** | `success 6/30`，平均 **203 字**，6 篇来自"线上" |
| 10-26 | `success 2/11`，平均 **204 字**，2 篇来自"线上" |

10-24/10-25 两轮则是直接失败（代理端口从 7892 变成 7890，脚本提示写死端口）。

### 证据（2026-10-26 实测）

1. **线上**：裸 `httpx` 请求文章返回 `HTTP 401`（DataDome）；带 Referer 只能拿到付费墙预览。
2. **真实浏览器打开也是预览**：`<article>` 只有 124 个汉字，页面上写着「畅读全文 / 订阅《华尔街日报》」，
   没有 SSG 数据（`#__NEXT_DATA__`、`ld+json` 全无），正文是服务端就截断的。
3. **archive.today 的新快照是空壳**：同一天存档页 40 KB、`<title>` 就是 `wsj.com`、正文 0 汉字；
   而 10-10 的老文章快照 624 KB、2538 字，正常。→ 存档站只是把付费墙页面存下来了。
4. **Wayback** 快照与线上逐字相同，都是预览。

结论：**没有订阅身份，就拿不到全文**；这不是代码能绕的。

### 代码侧同时修掉的一个真 bug

旧代码把"两段预览"当成了全文（摘要里 `paywall_preview: false`、`full_text: 2`），
于是这些预览既没被推迟、也没被标注，直接进了书。原因有两条，都已修复：

- `Copyright © … Dow Jones` 这行**预览页也有**，而判定顺序里"完整结尾"排在长度检查之前；
- 判定用的是未清理的原始正文，`live` 抓下来的导航/热门文章/订阅框把 2 段预览撑到 300+ 单位。

现在：先看付费墙语、再看"文末是不是本文自己的链接"、**再看清理后的长度（< 300 单位即预览）**，
只有长稿才用"报纸式结尾"（`Appeared in the … print edition as`）兜底。

### 三条出路（需要你选一条）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A. 接受摘要** | `config.json` 里 `fetch.max_preview_attempts: 0` | 每天都有书，但每篇只有开头两段 + 链接 |
| **B. 用订阅身份拿全文** | 在浏览器登录 WSJ，把 Cookie 填进 `fetch.cookie`（`sources` 里 `live` 放第一位） | 需要有效订阅；Cookie 会过期，要偶尔更新 |
| **C. 暂停推送** | 计划任务里禁用 `WSJ-Kindle-Daily` | 不会有残缺的书，等站点策略变回来 |

### 当前状态：已按方案 C 暂停（2026-10-26）

计划任务 `WSJ-Kindle-Daily` 已置为 `Disabled`，最后一次运行是 2026-10-26 07:00。
`config.json` 里仍是 `max_preview_attempts: -1`（永不接受预览），所以即使手工跑一次，
只要拿不到全文就只会推迟、不会推书。

想恢复时（任选其一）：

```powershell
Enable-ScheduledTask -TaskName 'WSJ-Kindle-Daily'      # 恢复每日 07:00 自动跑
Start-ScheduledTask  -TaskName 'WSJ-Kindle-Daily'      # 只想立刻跑一次
```

如果改主意想每天收"摘要版"，把 `config.json` 的 `fetch.max_preview_attempts` 改成 `0` 再恢复任务即可。


