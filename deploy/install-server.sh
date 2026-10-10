#!/usr/bin/env bash
# 在 Ubuntu / Debian 服务器上安装 wsj 每日推送（中文版 + 英文政治板块）
#
# 用法：
#   sudo APP_ROOT=/opt/wsj-digest bash install-server.sh
#   WITH_PLAYWRIGHT=1 sudo -E APP_ROOT=/opt/wsj-digest bash install-server.sh   # 需要 bpc-fetch 兜底时
#
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/wsj-digest}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$USER}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 安装目标：$APP_ROOT    运行用户：$RUN_USER"

echo
echo "==> 1/6 系统依赖"
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates wget

echo
echo "==> 2/6 Calibre（提供 ebook-convert，用来生成 EPUB）"
if command -v ebook-convert >/dev/null 2>&1; then
  echo "  已安装：$(ebook-convert --version | head -1)"
else
  wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sh /dev/stdin
  echo "  已安装：$(ebook-convert --version | head -1)"
fi
# 服务器无图形界面时，Calibre 偶尔需要离屏后端
if ! grep -q QT_QPA_PLATFORM /etc/environment 2>/dev/null; then
  echo 'QT_QPA_PLATFORM=offscreen' >> /etc/environment || true
fi

echo
echo "==> 3/6 虚拟环境 + Python 依赖"
install -d -o "$RUN_USER" -g "$RUN_USER" "$APP_ROOT"
if [ ! -x "$APP_ROOT/venv/bin/python" ]; then
  sudo -u "$RUN_USER" python3 -m venv "$APP_ROOT/venv"
fi
sudo -u "$RUN_USER" "$APP_ROOT/venv/bin/pip" install -U pip wheel
sudo -u "$RUN_USER" "$APP_ROOT/venv/bin/pip" install -r "$HERE/requirements.txt"

if [ "${WITH_PLAYWRIGHT:-0}" = "1" ]; then
  echo "  （可选）安装 playwright + chromium，供 bpc-fetch 兜底来源使用"
  sudo -u "$RUN_USER" "$APP_ROOT/venv/bin/pip" install playwright
  sudo -u "$RUN_USER" "$APP_ROOT/venv/bin/playwright" install --with-deps chromium
else
  echo "  跳过 Playwright（archive.today 是主力来源，不需要浏览器）"
fi

echo
echo "==> 4/6 预检脚本就位"
install -o "$RUN_USER" -g "$RUN_USER" -m 755 "$HERE/preflight.py" "$APP_ROOT/preflight.py"

echo
echo "==> 5/6 systemd 单元"
for unit in wsj-kindle.service wsj-kindle.timer; do
  sed -e "s#__APP_ROOT__#$APP_ROOT#g" -e "s#__USER__#$RUN_USER#g" \
      "$HERE/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
echo "  已写入 /etc/systemd/system/wsj-*.{service,timer}"

echo
echo "==> 6/6 完成"
cat <<EOF

接下来三步：

  1) 上传项目（在本机 Windows 上执行，或在服务器上 git clone）：
       powershell -File sync-to-server.ps1 -Server <服务器IP> -User root
     （也可以手工：把 wsj-kindle/ 放到 $APP_ROOT/ 下，
       不必上传 articles/ public/ out/ data/ logs/ 这些产物目录）

  2) 填配置：$APP_ROOT/wsj-kindle/config.json
       - kindle.addresses 改成你的 @kindle.com
       - kindle.smtp 填发件邮箱 + 授权码（QQ/163 要授权码，不是登录密码）
       - kindle.enabled 改成 true
     别忘了先去亚马逊「管理我的内容和设备 → 首选项 → 个人文档设置」
     把发件邮箱加进「已批准的个人文档电子邮箱列表」。

  3) 预检 + 起定时任务：
       $APP_ROOT/venv/bin/python $APP_ROOT/preflight.py
       systemctl start wsj-kindle.service        # 立刻手动跑一次看看
       journalctl -u wsj-kindle -n 50 --no-pager
       systemctl enable --now wsj-kindle.timer

EOF
