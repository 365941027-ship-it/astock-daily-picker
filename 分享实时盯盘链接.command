#!/bin/zsh
# 双击运行：显示可发给朋友的“实时盯盘”公网链接，并复制到剪贴板

cd "$(dirname "$0")"

# 确保隧道与网页服务在运行
launchctl kickstart gui/$(id -u)/com.a-share.webapp 2>/dev/null
launchctl kickstart gui/$(id -u)/com.a-share.tunnel 2>/dev/null

URL=""
for i in {1..20}; do
  URL=$(rg -o "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/stock_tunnel.log 2>/dev/null | tail -1)
  if [ -n "$URL" ]; then break; fi
  sleep 1
done

echo "======================================"
echo " 实时盯盘 · 分享给朋友"
echo "======================================"
echo

if [ -n "$URL" ]; then
  FULL="$URL/intraday"
  echo " 请把下面这个链接发给朋友："
  echo
  echo "   $FULL"
  echo
  echo " 提示：电脑需保持开机，朋友在交易时段打开即可实时看盘。"
  echo " 已在剪贴板，直接粘贴发送即可。"
  echo -n "$FULL" | pbcopy
else
  echo " 隧道尚未就绪，请稍后重试或查看 /tmp/stock_tunnel.log"
fi

echo
read "?按回车关闭窗口…"
