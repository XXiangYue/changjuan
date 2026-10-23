#!/bin/zsh
set -e
CHANGJUAN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$CHANGJUAN_ROOT"
export UV_CACHE_DIR="$CHANGJUAN_ROOT/.cache/uv"
uv sync --frozen --extra dev
pnpm --dir "$CHANGJUAN_ROOT/web" install --frozen-lockfile --store-dir "$CHANGJUAN_ROOT/.cache/pnpm"
pnpm --dir "$CHANGJUAN_ROOT/web" build
exec "$CHANGJUAN_ROOT/.venv/bin/novel-agent" serve --open
