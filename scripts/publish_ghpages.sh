#!/bin/zsh
# 生成静态站点并推送到 GitHub Pages（gh-pages 分支）。
# 用法：
#   bash scripts/publish_ghpages.sh [--proxy socks5h://127.0.0.1:7897]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/yexiyan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$PY" ]; then PY="python3"; fi

cd "$ROOT"

echo "== [1/2] 生成静态站点 =="
"$PY" scripts/build_static_site.py "$@"

echo "== [2/2] 推送到 GitHub Pages =="
# 若有代理参数，则生成站点和 git 推送都走该代理（自动化环境通常需要）
PROXY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --proxy) PROXY="$2"; shift 2;;
    *) shift;;
  esac
done
if [ -n "$PROXY" ]; then
  export HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" ALL_PROXY="$PROXY"
fi

# 用独立临时克隆同步 gh-pages 分支（避免 worktree 残留/损坏问题）
PUB="$(mktemp -d /tmp/astock-gh-pages-pub.XXXXXX)"
REMOTE="https://github.com/365941027-ship-it/astock-daily-picker.git"
cd "$PUB"
git init -q
git remote add origin "$REMOTE"
git fetch -q --depth 1 origin gh-pages 2>/dev/null || git fetch -q --depth 1 origin main
git checkout -q -b gh-pages FETCH_HEAD 2>/dev/null || git checkout -q --orphan gh-pages

rsync -a --delete --exclude ".git" "$ROOT/site/" "$PUB/"
git add -A
if git diff --cached --quiet; then
  echo "没有内容变更，跳过推送。"
else
  git commit -m "静态站点更新 $(date '+%Y-%m-%d %H:%M')"
  git push -q origin gh-pages
  echo "已推送 gh-pages"
fi
rm -rf "$PUB"

echo "== 完成 =="
