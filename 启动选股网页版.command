#!/bin/zsh
# 双击启动 A股每日选股网页版，并在浏览器中打开

cd "$(dirname "$0")"
PORT="${1:-8235}"

# 1) 服务已在运行 → 直接打开浏览器
if curl -s -m 2 "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1; then
  echo "选股服务已在运行（端口 $PORT），直接打开浏览器…"
  open "http://127.0.0.1:$PORT"
  read "?按回车关闭本窗口（服务仍在后台运行）…"
  exit 0
fi

# 2) 端口被占用但服务无响应 → 清理后重启
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 被占用但服务无响应，先停止旧进程…"
  pkill -f "webapp.py --port $PORT" 2>/dev/null
  sleep 1
fi

# 优先使用捆绑的 Python 运行环境（自带 python-docx 等依赖）
if [ -x "/Users/yexiyan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
  PY="/Users/yexiyan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "未找到 Python，请先安装 Python 3。"
  read "?按回车退出..."
  exit 1
fi

echo "======================================"
echo " A股每日选股 · 网页版"
echo " 启动中… 完成后会自动打开浏览器"
echo " 关闭本窗口即停止服务"
echo "======================================"

"$PY" webapp.py --host 0.0.0.0 --port "$PORT" &
SERVER_PID=$!

# 等待服务就绪（最多 30 秒）
for i in {1..60}; do
  if curl -s -m 2 "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if curl -s -m 2 "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT"
else
  echo "启动失败：请检查上方错误信息，或查看是否有其他程序占用端口 $PORT。"
fi

# 保持终端窗口打开，Ctrl+C 或关闭窗口停止服务
wait "$SERVER_PID"
