#!/bin/zsh
# 双击运行：打印当前分享链接（若隧道未运行则自动启动）

cd "$(dirname "$0")"

if [ ! -x "/Users/yexiyan/Documents/A股复盘/tools/cloudflared" ]; then
  echo "未找到 cloudflared，请先让 Codex 完成一次性安装。"
  read "?按回车关闭…"
  exit 1
fi

# 确保网页服务在运行（launchd 已注册常驻，这里只做兜底）
if ! curl -s -m 2 "http://127.0.0.1:8235/" >/dev/null 2>&1; then
  launchctl kickstart -k gui/$(id -u)/com.a-share.webapp 2>/dev/null
fi

# 确保隧道在运行
if ! curl -s -m 3 "http://127.0.0.1:20241/metrics" >/dev/null 2>&1; then
  launchctl kickstart -k gui/$(id -u)/com.a-share.tunnel 2>/dev/null
fi

echo "======================================"
echo " A股每日选股 · 临时分享链接"
echo "======================================"
echo

for i in {1..40}; do
  URL=$(rg -o "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/stock_tunnel.log 2>/dev/null | tail -1)
  if [ -n "$URL" ]; then
    echo " 请把这个链接发给朋友："
    echo
    echo "   $URL"
    echo
    echo " 提示：链接为临时地址，隧道服务关闭后会失效。"
    echo " 保持本窗口开着即可（关闭窗口不会停止隧道）。"
    break
  fi
  sleep 1
done

if [ -z "$URL" ]; then
  echo "隧道尚未就绪，请稍后再试，或查看 /tmp/stock_tunnel.log"
fi

read "?按回车关闭本窗口…"
