#!/bin/zsh
set -e
CHANGJUAN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$CHANGJUAN_ROOT"
mkdir -p "$CHANGJUAN_ROOT/artifacts/testing"
"$CHANGJUAN_ROOT/.venv/bin/pytest" -q --junitxml="$CHANGJUAN_ROOT/artifacts/testing/pytest.xml" --basetemp="$CHANGJUAN_ROOT/.cache/tests"
"$CHANGJUAN_ROOT/.venv/bin/ruff" check src/novel_agent/studio src/novel_agent/cli.py tests/test_studio.py --select F
pnpm --dir "$CHANGJUAN_ROOT/web" build
