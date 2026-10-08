# 用 GitHub Actions 做每日推送（本机关机也照跑）

思路来自书伴的 [《用 GitHub Actions 让 Calibre 定时推送新闻到 Kindle》](https://bookfere.com/post/1107.html)：
把任务交给 GitHub 的 runner（常开、免费额度足够），用 **Environment secrets** 存 SMTP 凭据，
用 **cron** 定时，产物进 **Artifacts**。本项目把"Calibre 抓 RSS"换成了我们自己的流水线。

## 与那篇教程的三点差异（重要）

| | 书伴的方案 | 本项目 |
|---|---|---|
| 抓取 | Calibre Recipe 抓 RSS/网页 | sitemap 发现 + archive.today 取全文（见两个项目 README） |
| 转 EPUB | runner 上装 Calibre | **不需要 Calibre**：`build_epub.py --engine auto` 在没有 Calibre 时自动改用纯 Python（zip + XHTML + OPF），已实测能被 Calibre 正常解析转换 |
| 状态 | 每次重新生成 | **`data/seen.json` 提交回仓库**——runner 是一次性的，不这样做每天都会重复推送同一批文章 |

## 前置条件

1. GitHub 账号（私有仓库即可，**建议私有**）；
2. 一个开了 SMTP 的邮箱（QQ/163 用**授权码**，Gmail 用 App Password）；
3. 你的 `xxx@kindle.com`，并把发件邮箱加进亚马逊「已批准的个人文档电子邮箱列表」。

---

## 步骤

### 1. 建仓库并上传

本机没装 git 的话先装一个（或用 GitHub Desktop 图形界面）：

```powershell
winget install --id Git.Git -e
```

然后在工作区根目录初始化并推上去（`.gitignore` 已经写好，会**自动排除抓下来的新闻正文**）：

```powershell
cd D:\Documents\deepseek-harness\default-workspace
git init -b main
git add .
git commit -m "wsj digest pipeline"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

推上去之后确认仓库里有这些：

```
.github/workflows/wsj-digest.yml     ← 定时任务
deploy/requirements.txt
deploy/preflight.py
wsj-kindle/{config.json,scripts/,README.md}
wsj-politics/{config.json,scripts→共用,README.md}
```

> `wsj-kindle/articles/`、`public/`、`out/`、`logs/`、`urls.txt` 都被忽略了，不会上传——
> 一是里面是受版权保护的新闻正文，二是体积会失控。**唯一入库的运行时文件是 `data/seen.json`。**

### 2. 建 Environment 和 secrets

仓库页面 → **Settings → Environments → New environment**，名字填 **`kindle`**（要和 workflow 里的
`environment: kindle` 一致）→ 在该 Environment 里点 **Add environment secret**，按书伴教程同名的变量填：

| Name | 说明 | 示例 |
|---|---|---|
| `FROM` | 发件邮箱（已在亚马逊批准列表里） | `me@qq.com` |
| `TO` | 你的 Kindle 邮箱（多个用英文逗号分隔） | `xxx@kindle.com` |
| `SMTP` | SMTP 服务器 | `smtp.qq.com` |
| `PORT` | 端口 | `465` |
| `ENCRYPT` | 加密方式：`SSL` 或 `STARTTLS` | `SSL` |
| `SECRET` | SMTP 密码/授权码 | `xxxxxxxxxxxx` |

> 本项目脚本同时认 `WSJ_*` 前缀的名字（`WSJ_SMTP_HOST`、`WSJ_KINDLE_TO` 等），
> 但上表那套名字和书伴教程一致，照着填最省事。凭据只经环境变量传入，**不会写进 config.json**。
>
> 注意：Environment secrets 在**私有仓库**同样可用（书伴那篇说"仅公开项目可用"是当时的情况）。
> 如果这里找不到 Environments，用 **Settings → Secrets and variables → Actions** 建仓库级 secrets 也一样，
> 只要把 workflow 里的 `environment: kindle` 那行删掉即可。

### 3. 先手动跑一次

**Actions → 左侧 WSJ daily digest → Run workflow**：

- `project` 选 `all`（或先只跑 `wsj-kindle` 试水）
- `force` 留空（勾上会忽略 seen.json 重抓全部，第一次不必）

跑完看两个地方：

1. **日志**：会先打印一段预检（Python 依赖 / Calibre / sitemap 连通性 / **archive.today 能不能用**），
   然后才是发现 → 抓取 → RSS → EPUB → 推送。看到 `sent_to: [...]` 就说明邮件发出去了。
2. **Artifacts**：页面底部有 `wsj-kindle-<run号>`，里面有 EPUB、feed.xml 和日志，
   可以下载确认排版。EPUB 也会存 30 天。

### 4. 调整定时

`.github/workflows/wsj-digest.yml` 里这一行（**UTC 时间**）：

```yaml
    - cron: "0 23 * * *"     # UTC 23:00 = 北京时间次日 07:00
```

换算公式：`UTC = 本地时间 − 时区偏移量`（不够减就 +24 并往前一天）。比如想北京时间 06:30 收到就写
`30 22 * * *`。改完提交即可生效；也可以拿 [crontab.guru](https://crontab.guru) 生成。

两个项目默认**串行**跑（`max-parallel: 1`），因为它们都要访问 archive.today，同时打容易触发限流。
总共约 8~12 分钟，一天一次，远在免费额度内（私有仓库每月 2000 分钟）。

### 5. 想临时停掉

- 暂停定时：注释掉 workflow 里的 `schedule:` 段并提交；
- 或 **Settings → Actions → General → Disable actions**（书伴那篇第六节的做法）。

---

## 排错

| 现象 | 处理 |
|---|---|
| 预检里 `archive.today` 全失败（429 / 只抽出几十字） | **这是最需要留意的风险**：archive.today 可能屏蔽机房 IP。改 `config.json` 的 `fetch.delay_seconds` 到 6~8 秒先试；仍不行就换方案（见下） |
| 预检里 sitemap 失败 | runner 到 wsj 不通（少见）。基本只能换 runner 区域或改自建机 |
| 工作流没跑起来 | 私有仓库要在 **Settings → Actions → General** 允许 Actions；fork 来的仓库默认关闭 |
| 日志里 `kindle_enabled: false` | secrets 没生效：确认 Environment 名字是 `kindle`、secrets 建在 Environment 里（不是 Variables） |
| 邮件没到也没报错 | 发件邮箱不在亚马逊「已批准列表」；或首次触发 Amazon 的二次确认邮件（去点一下 Verify） |
| 报 `附件超过 50MB` | 把 `config.json` 的 `feed.max_items` / `epub.max_items` 调小（中文 30 篇约 15MB） |
| 每天都收到重复文章 | `data/seen.json` 没提交成功：看 Actions 日志的"提交状态"那步，确认 `permissions: contents: write` 生效 |
| Actions 提示没有写权限 | 仓库 **Settings → Actions → General → Workflow permissions** 选 "Read and write permissions" |
| 想只看产物不推送 | Run workflow 时把 secrets 里的 `TO` 临时清掉，或日志里忽略 send 步骤的失败 |

## 如果 runner 的 IP 被 archive.today 挡住

GitHub Actions 的出口是 Azure 机房 IP，而 archive.today 对机房 IP 有时会限流。三条退路（按推荐度）：

1. **换到家里的常开设备**（NAS / 树莓派 / 旧笔记本）：家庭宽带 IP 最不容易被挡，
   用 [`README-server.md`](README-server.md) 里的 systemd 方案，一次配好长期无人值守；
2. **VPS**：同样用 systemd 方案，但只有**住宅/小众机房**的 IP 才比较稳，纯大厂机房可能和 GitHub 一样被挡；
3. **只推标题+链接**：把 `fetch.sources` 改成 `["live"]`（中文站能拿到预览段落），放弃全文。

不管走哪条，**先跑一次 `python3 deploy/preflight.py`**，它会直接告诉你这台机器能不能拿到全文。

## 两种云端方案怎么选

| | GitHub Actions | VPS / NAS + systemd |
|---|---|---|
| 成本 | 免费（私有仓库每月 2000 分钟） | 一台常开设备 |
| 本机关机 | ✅ 照跑 | ✅ 照跑 |
| 出网 IP | Azure 机房（可能被 archive.today 限流） | 自选（住宅 IP 最稳） |
| 维护 | 零维护，改配置就是改仓库 | 要管系统更新、日志、依赖 |
| 适合 | 想零成本、能接受偶尔漏推 | 想要稳定并自己掌控 |
