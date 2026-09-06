#!/usr/bin/env bash
# A股每日选股 + 盘中实时盯盘 · Docker 云服务器一键部署（Ubuntu 24.04 + Docker 29）
#
# 前提：
#   1) 项目已上传到服务器，如 /opt/ashare-picker
#      rsync -av --exclude .git --exclude site /Users/yexiyan/Documents/A股复盘/ root@服务器IP:/opt/ashare-picker/
#   2) 服务器已装 Docker（本机 Docker 29.6 自带 compose 插件）
#
# 运行：
#   cd /opt/ashare-picker && sudo bash deploy/install_cloud_docker.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$APP_DIR/logs"

echo "== [1/4] 确认 Docker =="
docker --version
docker compose version || { echo "未找到 docker compose 插件，请确认 Docker 安装完整"; exit 1; }

mkdir -p "$LOG_DIR"

echo "== [2/4] 构建镜像并启动网页服务 =="
cd "$APP_DIR"
docker compose build web
docker compose up -d web
sleep 3
docker ps --filter name=ashare-web --format "网页服务容器：{{.Names}} 状态={{.Status}}"

echo "== [3/4] 首次拉取数据（容器内直连行情，无需代理） =="
# 仅在缓存为空时做首次完整更新；否则跳过
if ! docker exec ashare-web test -f /app/daily_picker/cache/replay/index.json 2>/dev/null; then
  docker exec ashare-web python daily_update.py --no-publish >> "$LOG_DIR/initial.log" 2>&1 \
    && echo "首次盘后更新完成" || echo "首次更新未完成，见 logs/initial.log"
else
  echo "检测到已有缓存，跳过首次拉取。"
fi

echo "== [4/4] 配置宿主机定时任务 =="
# 交易日 18:05 盘后更新（容器内直连）；09:10 启动盯盘容器（内部 15:10 自动退出）
CRON_UPD="5 18 * * 1-5 cd $APP_DIR && docker exec ashare-web python daily_update.py --no-publish >> $LOG_DIR/daily.log 2>&1"
CRON_MON="10 9 * * 1-5 cd $APP_DIR && docker start ashare-monitor >/dev/null 2>&1 || docker compose up -d monitor"
CRON_CATCH="30 9 * * 1-5 cd $APP_DIR && docker exec ashare-web python scripts/daily_catchup.py >> $LOG_DIR/catchup.log 2>&1 || true"
(crontab -l 2>/dev/null | grep -v "ashare-web\|ashare-monitor\|daily_catchup" ; \
  echo "$CRON_UPD" ; echo "$CRON_MON" ; echo "$CRON_CATCH") | crontab -
echo "  已写入 crontab：18:05 盘后更新 / 09:10 启动盯盘 / 09:30 补跑检查"

IP=$(hostname -I | awk '{print $1}')
echo
echo "============================================="
echo " 部署完成"
echo " 网页版/盯盘页： http://$IP:8235/intraday"
echo " 请在云控制台安全组放行 TCP 8235"
echo " 日志： $LOG_DIR"
echo " 手动测试盯盘： docker start ashare-monitor"
echo "============================================="
