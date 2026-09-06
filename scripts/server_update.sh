#!/usr/bin/env bash
# A股服务器一键更新：从 GitHub 拉取最新代码 -> 重建容器 -> 重算最新交易日 -> 重建盯盘快照
#
# 用法（在服务器上）：
#   sudo bash /opt/ashare-picker/scripts/server_update.sh
#
# 说明：需要服务器能访问 raw.githubusercontent.com（腾讯云国内一般可访问）。

set -euo pipefail

APP_DIR="/opt/ashare-picker"
BRANCH="${1:-main}"
RAW="https://raw.githubusercontent.com/365941027-ship-it/astock-daily-picker/${BRANCH}"

echo "== [1/4] 从 GitHub 拉取最新代码 =="
FILES=(
  "webapp.py"
  "daily_update.py"
  "daily_picker/config.py"
  "daily_picker/screening.py"
  "daily_picker/replay.py"
  "daily_picker/data_fetch.py"
  "daily_picker/risks.py"
  "daily_picker/strategy.py"
  "daily_picker/market_signal.py"
  "daily_picker/news.py"
  "scripts/intraday_monitor.py"
  "scripts/build_strategy_report.py"
  "scripts/send_push.py"
  "web/index.html"
  "web/app.js"
  "web/style.css"
  "web/intraday.html"
  "web/strategy.html"
  "web/kdj_example.html"
)
for f in "${FILES[@]}"; do
  curl -fsSL -o "${APP_DIR}/${f}" "${RAW}/${f}" && echo "  ✓ ${f}" || echo "  ✗ ${f}（跳过）"
done

echo "== [2/4] 重建并重启容器 =="
cd "$APP_DIR"
sudo docker compose -f deploy/docker-compose.yml build web monitor
sudo docker compose -f deploy/docker-compose.yml up -d web monitor

echo "== [3/4] 重算最新交易日数据（含财务过滤/板块过滤） =="
sudo docker exec ashare-web python daily_update.py --force --no-publish || echo "（更新未完全成功，见上方日志）"

echo "== [4/4] 重建盯盘快照 =="
sudo docker exec ashare-web python scripts/intraday_monitor.py --force || echo "（盯盘快照生成失败，见上方日志）"

echo
echo "============================================="
echo " 更新完成"
echo " 网页：http://<服务器IP>:8235/"
echo " 盯盘：http://<服务器IP>:8235/intraday"
echo "============================================="
