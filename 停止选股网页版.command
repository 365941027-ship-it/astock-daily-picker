#!/bin/zsh
# 停止 A股每日选股网页版服务

echo "正在停止选股网页版服务…"

PIDS=$(pgrep -f "webapp.py --port" 2>/dev/null)
if [ -z "$PIDS" ]; then
  echo "没有正在运行的选股服务。"
else
  for pid in $PIDS; do
    kill "$pid" 2>/dev/null && echo "已停止进程 $pid"
  done
fi

read "?按回车关闭…"
