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
# 用 git worktree 把 site/ 同步到 gh-pages 分支
WT="$(git worktree list --porcelain | awk '/^worktree /{p=$0} /branch refs\/heads\/gh-pages/{sub(/^worktree /, "", p); print p; exit}')"
if [ -z "$WT" ]; then
  WT="$(mktemp -d /tmp/astock-gh-pages.XXXXXX)"
  git worktree add "$WT" -b gh-pages >/dev/null 2>&1 || git worktree add "$WT" gh-pages >/dev/null 2>&1
fi

rsync -a --delete --exclude ".git" "$ROOT/site/" "$WT/"
cd "$WT"
git add -A
if git diff --cached --quiet; then
  echo "没有内容变更，跳过推送。"
else
  git commit -m "静态站点更新 $(date '+%Y-%m-%d %H:%M')"
  git push origin gh-pages
  echo "已推送 gh-pages"
fi

echo "== 完成 =="
