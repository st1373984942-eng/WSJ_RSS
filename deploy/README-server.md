# 部署到自己的服务器（VPS / NAS / 树莓派）

用 systemd 定时器每天跑一次，本机关机也照跑。适合想要"住宅 IP 出网"（archive.today 最不容易被限流）
或想完全自己掌控的场景。想零成本、零维护就用 [`github-actions-setup.md`](github-actions-setup.md)。

## 这个目录里的东西

| 文件 | 作用 |
|---|---|
| `install-server.sh` | 一键装依赖（python venv、Calibre）、建 venv、装 systemd 单元 |
| `requirements.txt` | Python 依赖（`bpc-fetch` + `markdown`） |
| `preflight.py` | **上机第一件事**：检查依赖、Calibre、sitemap 连通性、archive.today 可用性 |
| `wsj-kindle.{service,timer}` | 中文版每日任务 |
| `sync-to-server.ps1` | 在本机 Windows 上把项目传上去（只传代码和配置） |

## 步骤

### 1. 选机器

- 系统：Ubuntu / Debian（22.04+ 或 24.04 都行）；
- 配置：1 核 1GB 起步就够（Calibre 转换时内存会到几百 MB）；
- **出网 IP 很关键**：住宅宽带（家里的 NAS/树莓派）最稳；大厂机房 IP 有可能被 archive.today 限流，
  所以**先跑预检再配定时任务**。

### 2. 上传代码

在本机（Windows）执行：

```powershell
cd D:\Documents\deepseek-harness\default-workspace
powershell -File deploy\sync-to-server.ps1 -Server <服务器IP> -User root
```

它会把 `wsj-kindle/`、`deploy/` 传上去，**不带** `articles/ public/ out/ data/ logs/`
这些产物目录（服务器上会自己生成）。

### 3. 安装

```bash
ssh root@<服务器IP>
sudo APP_ROOT=/opt/wsj-digest bash /opt/wsj-digest/deploy/install-server.sh
```

- 脚本会装 Calibre（提供 `ebook-convert`）。**其实也可以不装**——`build_epub.py --engine auto`
  在找不到 Calibre 时会自动用纯 Python 引擎生成 EPUB，只是 Calibre 的排版略好一点。
- 默认**不装 Playwright 浏览器**（几百 MB，只有 `bpc_fetch` 这个兜底来源才需要；
  archive.today 是主力来源）。真要装：`WITH_PLAYWRIGHT=1 sudo -E bash install-server.sh`。

### 4. 填配置

```bash
vi /opt/wsj-digest/wsj-kindle/config.json      # 中文版
```

改 `kindle` 段：`addresses` 填你的 `@kindle.com`，`smtp` 填发件邮箱 + 授权码，
`enabled` 改成 `true`。（别忘了先去亚马逊把发件邮箱加进「已批准的个人文档电子邮箱列表」。）

> 不想把授权码写进文件，也可以留空并在 systemd 单元里用环境变量提供
> （`WSJ_SMTP_PASSWORD`、`WSJ_KINDLE_TO` 等，见 `send_kindle.py` 头部注释）。
> 单元文件里已经预留了注释掉的代理配置行。

### 5. 预检

```bash
/opt/wsj-digest/venv/bin/python /opt/wsj-digest/preflight.py
```

看到 `=== 结论：可以跑 ===` 再往下走。它会逐项报告：依赖、Calibre、两个 sitemap、
archive.today（用固定的老文章做样本，避免"新文章还没存档"造成误判）。

### 6. 手动跑一次，再开定时

```bash
systemctl start wsj-kindle.service          # 立刻跑一次
journalctl -u wsj-kindle -n 80 --no-pager   # 或者看日志文件
tail -n 80 /opt/wsj-digest/wsj-kindle/logs/pipeline.log

systemctl enable --now wsj-kindle.timer
systemctl list-timers 'wsj-*'               # 确认下次触发时间
```

定时器默认（`deploy/*.timer`）：北京时间 07:00。`Persistent=true` 表示服务器重启或错过时间点会补跑一次。

### 7. 日常运维

```bash
systemctl list-timers 'wsj-*'                       # 下次什么时候跑
journalctl -u wsj-kindle -n 50 --no-pager           # 看运行日志
cat /opt/wsj-digest/wsj-kindle/logs/pipeline.log    # 同上（文件形式）
ls -lh /opt/wsj-digest/wsj-kindle/out/              # 生成的 EPUB
```

更新代码：在本机重新跑一次 `sync-to-server.ps1`（会覆盖脚本和 config.json——
**如果你在服务器上改过 config.json，先备份**）。

## 排错

| 现象 | 处理 |
|---|---|
| 预检里 sitemap 连接超时 | 这台机器直连不到 wsj。在 `wsj-kindle.service` 里取消 `HTTPS_PROXY` 注释并填上代理地址，`systemctl daemon-reload` 后重试 |
| 预检里 archive.today 429 / 只抽出几十字 | 机房 IP 被限流。先把 `fetch.delay_seconds` 调到 6~8 秒；仍不行就换住宅 IP 的机器 |
| `ebook-convert: command not found` | 没装 Calibre，但**不影响**：会自动走纯 Python 引擎。想装就 `apt install -y calibre-bin` 或跑 `install-server.sh` |
| systemd 报 `status=203/EXEC` | 单元里的路径不存在：确认 `APP_ROOT` 和实际目录一致（`grep ExecStart /etc/systemd/system/wsj-*.service`） |
| 日志里中文乱码 | 单元已设 `PYTHONUTF8=1` / `LANG=C.UTF-8`；如果你手工改了单元，把这两行加回来 |
| 每天都重复推送 | 状态没落下：确认 `data/seen.json` 有更新（`ls -l`），且服务用户对项目目录有写权限 |
| 定时没触发 | `systemctl status wsj-kindle.timer`；`OnCalendar` 用的是带时区的写法（`Asia/Shanghai`），改时间后要 `systemctl daemon-reload` |
