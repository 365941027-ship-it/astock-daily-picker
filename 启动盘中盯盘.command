#!/bin/zsh
# 双击启动盘中盯盘助手：交易时段(9:15-15:10)每30秒刷新并推送状态变化。
# 停止：在终端窗口按 Ctrl+C。

cd "$(dirname "$0")"

PY="/Users/yexiyan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

echo "======================================"
echo " A股盘中盯盘助手"
echo " 交易时段自动运行，约每30秒刷新一次"
echo " 停止请按 Ctrl+C"
echo "======================================"
echo

# 有现成状态文件则先打印上次快照概览
if [ -f "daily_picker/cache/intraday_status.json" ]; then
  "$PY" -c "
import json, datetime
d=json.load(open('daily_picker/cache/intraday_status.json'))
items=d.get('items',[])
n=sum(1 for i in items if i['status'] in ('broke','weak','tested','tested_weak','target'))
print(f\"上次快照 {d.get('generated_at','')}：候选{len(items)}只，其中需关注/提示 {n} 只\")
"
fi

echo
echo "开始盯盘…"
"$PY" scripts/intraday_monitor.py --watch --proxy "socks5h://127.0.0.1:7897" 2>&1 | tee -a /tmp/stock_intraday_monitor.log

echo
echo "盯盘已结束（Ctrl+C 或已过 15:10）。"
read "?按回车关闭窗口…"
