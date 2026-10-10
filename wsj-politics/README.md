# wsj-politics — 华尔街日报英文版政治板块 → RSS → Kindle

> ## ⏸ 已停用（不再推送）
>
> 按需求，**定时推送已摘除**，现在只推送中文版（[`../wsj-kindle/`](../wsj-kindle/README.md)）。
> 具体做了这些：
>
> - `.github/workflows/wsj-digest.yml` 里不再包含这个项目（没有 matrix 项）
> - `deploy/wsj-politics.service` 与 `.timer` 已删除（服务器路线也不会跑）
> - 项目代码、`config.json`、脚本都**原样保留**，手动执行仍可用：
>   `python ../wsj-kindle/scripts/run_pipeline.py --project .`
>
> 想恢复每日推送：把这个项目加回 workflow 的 job（或复制一份 job 改 `PROJECT`），
> 服务器路线则从 git 历史里取回那两个 systemd 单元即可。

每天自动抓 WSJ **英文站政治板块**的文章，合成带全文的 RSS，转 EPUB 推到 Kindle。

> 脚本与中文版**共用** [`../wsj-kindle/scripts/`](../wsj-kindle/README.md)，
> 靠 `--project` 指向各自的 `config.json`。**两条链路用的是同一套方法**
> （sitemap 发现 + archive.today 取全文），只是 sitemap 地址和板块路径前缀不同：
> 中文站是 `cn.wsj.com/wsj_cn_google_news.xml` + `articles/`，
> 英文站是 `www.wsj.com/wsjsitemaps/wsj_google_news.xml` + `politics/`。

---

## 0. 已验证到什么程度

2026-10-08 在这台机器上完整跑通（`run_daily.cmd` 实跑，退出码 0）：

```
discover  Google News sitemap 388 个 <url> → 按 politics/ 前缀过滤 → 12 篇（带真实标题）
fetch     12/12 成功，全部来自 archive.today，平均 1071 词（最少 340，最多 3145）
          全文 12 篇，付费墙预览 0 篇
feed      feed.xml 12 条，guid 唯一，content:encoded 非空，图片全绝对 URL
epub      wsj-politics-20261008-2354.epub，6.87 MB，28 张图，mimetype/container/CRC 全合法
selfcheck 全部 ok: true
```

**12/12 全部拿到真正的全文**（不是预览）。中文版走同一套方法后同样拿到全文
（30 篇、中位 1333 字），差别只在 sitemap 地址和板块路径前缀。

---

## 1. 三个关键发现（都是实测，决定了整条链路的设计）

### 1.1 WSJ 的官方 RSS 已经死了

```
RSSWorldNews.xml    200  但最新条目停在 Mon, 27 Jan 2025
RSSOpinion.xml      200  停在 2025-01-27
WSJcomUSBusiness.xml 200 停在 2025-01-24
RSSWSJD.xml / RSSMarketsMain.xml  200 停在 2025-01-27
RSSPolitics.xml / RSSUSnews.xml / RSSHeardOnTheStreet.xml ...  403 AccessDenied（S3，文件不存在）
RSSUSNews.xml（大写 N） 200 但 0 条
```

所以 `bpc-fetch discover wsj.com` 那条路彻底没戏（就算 feed 活着，它的路径表里也没有 WSJ 的 feed 名）。

### 1.2 发现走 sitemap：`robots.txt` 和 `sitemap.xml` 不在 DataDome 后面

```
https://www.wsj.com/politics          → 401（Google Referer / Googlebot / bingbot 全部 401）
https://www.wsj.com/robots.txt        → 200 ✅
https://www.wsj.com/sitemap.xml       → 200 ✅（索引，224 个子 sitemap）
https://www.wsj.com/wsjsitemaps/wsj_google_news.xml → 200 ✅（388 个 <url>，含 48 小时内文章）
```

Google News sitemap 按规范只含最近 48 小时的文章，而且带 `news:title` 和
`news:lastmod`——标题和日期都不用额外请求。

**更关键的是**：WSJ 新版文章 URL 是按**板块路径**组织的，不再是老的 `/articles/<slug>-<hash>`：

```
https://www.wsj.com/politics/policy/new-cdc-director-is-eager-to-change-its-culture-7f2319be
https://www.wsj.com/politics/elections/democrats-expect-to-win-the-house-...
https://www.wsj.com/world/middle-east/...     https://www.wsj.com/tech/ai/...
```

于是"政治板块"= 路径以 `politics/` 开头，**过滤是确定性的**，不需要猜、不需要分类。

### 1.3 正文走 archive.today（Wayback 和线上都不行）

| 来源 | 结果 |
|---|---|
| 线上（Chrome/Googlebot/bingbot UA + Google Referer） | **401**，Google Referer 只对中文站有效 |
| Wayback 对新式 `politics/...` URL | **404 无快照** |
| Wayback 对老式 `/articles/...` URL | 有快照，但只有 90~116 词的**付费墙预览** |
| Wayback "Save Page Now" 现抓 | IA 爬虫也只拿到 68 词预览 |
| **archive.today（`archive.ph/newest/<url>`）** | **341 / 584 / 2378 / 636 词，结尾带 `Appeared in the ... print edition` 或 `Copyright ©2026 Dow Jones` → 全文** |

最终 12/12 全部从 archive.today 取到全文。代价是它有限流，所以抓取是**串行 + 每篇间隔 3 秒**
（`fetch.delay_seconds`），对 archive.today 客气一点。

---

## 2. 日常使用

```powershell
cd D:\Documents\deepseek-harness\default-workspace\wsj-politics

# 一条命令走完：发现 → 抓取 → RSS → EPUB（→ 配好就发 Kindle）
& 'F:\Program Files\Python\python.exe' ..\wsj-kindle\scripts\run_pipeline.py --project .

# 或者直接跑 launcher（会写 logs\pipeline.log）
.\run_daily.cmd
```

单步调试（注意都要带 `--project .`）：

```powershell
$sc = '..\wsj-kindle\scripts'
python $sc\wsj_discover_sitemap.py --project .            # 只看发现
python $sc\wsj_fetch_multi.py --project . --limit 3 --force
python $sc\wsj_fetch_multi.py --project . --sources wayback   # 换单个来源试
python $sc\build_feed.py --project .                      # 只重建 RSS
python $sc\build_epub.py --project . --all                # 用全部文章出 EPUB
python $sc\selfcheck.py --project .                       # 产物自检
python $sc\serve_feed.py --project .                      # 托管 http://127.0.0.1:8081/feed.xml
```

配 Kindle 推送：改 `config.json` 的 `kindle`（收件地址 + SMTP 授权码），把 `enabled` 改成 `true`，
然后 `python ..\wsj-kindle\scripts\send_kindle.py --project . --epub out\xxx.epub --dry-run` 先校验。
详细步骤（亚马逊"已批准发件人"、QQ 邮箱授权码、中国区账号问题）见
[中文版 README 第 2 节](../wsj-kindle/README.md)，两边完全一样。

每天自动跑：

```powershell
schtasks /create /tn "WSJ Politics to Kindle" /tr "D:\Documents\deepseek-harness\default-workspace\wsj-politics\run_daily.cmd" /sc daily /st 07:10
```

**本机关机也要跑**（云端部署）有两个现成方案：

- **GitHub Actions**（零成本零维护）：[deploy/github-actions-setup.md](../deploy/github-actions-setup.md)
- **VPS / NAS + systemd**（住宅 IP 更稳）：[deploy/README-server.md](../deploy/README-server.md)

两者第一步都是 `python3 deploy/preflight.py`——它会告诉你这台机器能不能访问 archive.today，
也就是**能不能拿到全文**。两个项目共用一个 workflow / 一套 systemd 单元，已经配好串行执行
（都要打 archive.today，同时跑容易触发限流）。

---

## 3. 配置项（`config.json`）

| 键 | 说明 |
|---|---|
| `pipeline.discover_script` / `fetch_script` | 用哪套发现/抓取脚本（本项目的关键差异点） |
| `discover.path_prefixes` | 要哪个板块，`["politics/"]`；换 `["tech/ai/"]`、`["world/middle-east/"]` 就是别的板块 |
| `discover.since` | 时间窗口，默认 `2d`（sitemap 本身只覆盖 48 小时） |
| `discover.use_month_fallback` | Google News sitemap 拿不到时，退回当月 sitemap |
| `fetch.sources` | 取文来源顺序：`archive_today` → `wayback` → `live` |
| `fetch.delay_seconds` | archive.today 的请求间隔（默认 3 秒，别调太小） |
| `fetch.good_enough_words` | 拿到这么多词就不再试后面的来源（默认 400） |
| `fetch.cookie` | **有订阅就填这里**，`live` 来源立刻能拿全文（见下） |
| `fetch.paywall_note` | 只拿到预览时附加的提示语 |
| `feed.base_url` / `server.port` | 本项目的 feed 地址，用 8081，和中文版的 8080 错开 |
| `feed.author` | 中文版默认"华尔街日报中文版"，这里设成 WSJ |

**换板块只改一行**：把 `discover.path_prefixes` 改成 `["tech/ai/"]`，再把 `feed.title` 改掉即可。

---

## 4. 排错表

| 现象 | 原因 / 处理 |
|---|---|
| 发现 0 条，notes 报连接超时 | **本机系统代理（`127.0.0.1:7892`）没开**。直连 `www.wsj.com` 是不通的（实测 ConnectTimeout），必须走代理 |
| 发现 0 条但 sitemap 能开 | `discover.path_prefixes` 写错了；WSJ 板块路径形如 `politics/policy`、`politics/elections`，用 `politics/` 就能全覆盖 |
| 某篇 `archive_today:no_snapshot` | archive.today 上还没人存过这篇，会自动退到 wayback/live；都不行就跳过该篇（不影响其它文章） |
| 三源全失败：`archive_today:404; wayback:404; live:401` | **刚发布的文章还没被任何存档站收录**，属正常现象。流程会跳过 EPUB 并以**退出码 0** 结束，且该 URL 不写入 `seen.json`，下次运行自动重试（48 小时窗口内基本都会补上） |
| 大量 429 / 抓取变慢 | archive.today 限流了；把 `fetch.delay_seconds` 调大（5~8 秒），或减少单轮文章数 |
| 文章只有几百词且带 `paywall_note` | 那是预览，说明三个来源都没给全文；填 `fetch.cookie` 可彻底解决 |
| `run_daily.cmd` 退出码 1 | 只有当某一步真的失败时才是 1；"没有新文章"是正常结果，返回 0 |
| EPUB 里没有图 | 图片来自 archive.today 的存档页，某些文章本来就没图；`selfcheck.py` 会报缺图 |
| 想连中文版一起跑 | 两个 `run_daily.cmd` 都挂到任务计划程序，错开 10 分钟即可（都要用这个系统代理） |

---

## 5. 合规提醒

archive.today 与 bpc-fetch 同属绕过付费墙的取文手段，这条流水线适合**个人阅读**。
`public/feed.xml` 是含全文的 RSS，**只在你自己的机器/局域网内托管**，不要公开分享或对外分发。
