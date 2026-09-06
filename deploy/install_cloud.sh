#!/usr/bin/env bash
# A股每日选股 + 盘中实时盯盘 · 云服务器一键部署脚本（Ubuntu 22.04/Debian 12）
#
# 用法：
#   1) 把整个项目上传到服务器（任选其一）：
#      - rsync -av --exclude .git /Users/yexiyan/Documents/A股复盘/ root@服务器IP:/opt/ashare-picker/
#      - scp -r ...  （注意目录会包含缓存，建议 rsync）
#   2) 在服务器上运行：
#      cd /opt/ashare-picker && sudo bash deploy/install_cloud.sh
#
# 完成后：
#   - 网页版      http://服务器IP:8235
#   - 盘中盯盘页  http://服务器IP:8235/intraday
#   - 每天18:05自动盘后更新；交易日9:10自动启动盯盘（15:10自停）

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$APP_DIR/logs"
PY=/usr/bin/python3

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行：sudo bash deploy/install_cloud.sh"
  exit 1
fi

echo "== [1/5] 安装系统依赖 =="
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv curl rsync git

echo "== [2/5] 安装 python-docx（生成 Word 报告用，可选） =="
python3 -m pip install --break-system-packages -q python-docx || \
python3 -m pip install -q python-docx || echo "python-docx 安装失败（不影响网页/盯盘）"

mkdir -p "$LOG_DIR"

echo "== [3/5] 安装 systemd 服务 =="
install -m 644 "$APP_DIR/deploy/ashare-picker.service" /etc/systemd/system/ashare-picker.service
install -m 644 "$APP_DIR/deploy/ashare-intraday.service" /etc/systemd/system/ashare-intraday.service
systemctl daemon-reload
systemctl enable --now ashare-picker
echo "  网页服务已启动：http://$(hostname -I | awk '{print $1}'):8235"

echo "== [4/5] 配置定时任务（每日更新 + 交易日盯盘） =="
CRON_LINE_UPDATE="5 18 * * 1-5 cd $APP_DIR && $PY daily_update.py --no-publish >> $LOG_DIR/daily.log 2>&1"
CRON_LINE_INTRADAY="10 9 * * 1-5 systemctl start ashare-intraday >/dev/null 2>&1 || true"
CRON_LINE_CATCHUP="30 9 * * 1-5 cd $APP_DIR && $PY scripts/daily_catchup.py --proxy '' >> $LOG_DIR/catchup.log 2>&1 || true"
(crontab -l 2>/dev/null | grep -v "ashare-picker\|daily_update.py\|daily_catchup.py\|ashare-intraday" ; \
  echo "$CRON_LINE_UPDATE" ; echo "$CRON_LINE_INTRADAY"; echo "$CRON_LINE_CATCHUP") | crontab -
echo "  已写入 crontab：交易日 18:05 盘后更新 / 09:10 启动盯盘"

echo "== [5/5] 首次拉取最新数据（若服务器无缓存，可能需要几分钟） =="
if [ -f "$APP_DIR/daily_picker/cache/replay/index.json" ]; then
  echo "  检测到已有缓存，跳过首次拉取。"
else
  cd "$APP_DIR" && timeout 600 $PY daily_update.py --no-publish >> "$LOG_DIR/initial.log" 2>&1 \
    && echo "  首次盘后更新完成。" || echo "  首次更新未完成，请查看 $LOG_DIR/initial.log（不影响已启动的服务）"
fi

echo
echo "============================================="
echo " 部署完成"
echo " 网页版/盯盘页：http://$(hostname -I | awk '{print $1}'):8235/intraday"
echo " 请在云控制台安全组放行 TCP 8235"
echo " 日志目录：$LOG_DIR"
echo "============================================="
