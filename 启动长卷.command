#!/bin/zsh
set -e
CHANGJUAN_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$CHANGJUAN_ROOT"
export UV_CACHE_DIR="$CHANGJUAN_ROOT/.cache/uv"
if [[ ! -x "$CHANGJUAN_ROOT/.venv/bin/novel-agent" ]]; then
  if ! command -v uv >/dev/null; then
    print "请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
    read "?按回车关闭"; exit 1
  fi
  uv sync --frozen --extra dev
fi
if [[ ! -f "$CHANGJUAN_ROOT/web/dist/index.html" ]]; then
  pnpm --dir "$CHANGJUAN_ROOT/web" install --frozen-lockfile --store-dir "$CHANGJUAN_ROOT/.cache/pnpm"
  pnpm --dir "$CHANGJUAN_ROOT/web" build
fi
print "长卷正在启动：http://127.0.0.1:8765"
exec "$CHANGJUAN_ROOT/.venv/bin/novel-agent" serve --open
