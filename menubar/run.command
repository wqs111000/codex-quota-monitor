#!/bin/zsh
set -euo pipefail

ROOT="${CODEX_QUOTA_RUNTIME_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="/opt/homebrew/bin/python3"
APP="$ROOT/CodexQuotaMenuBar.app/Contents/MacOS/CodexQuotaMenuBar"

"$PYTHON" "$ROOT/app.py" >/tmp/codex-quota-service.log 2>&1 &
SERVICE_PID=$!

cleanup() {
  kill "$SERVICE_PID" 2>/dev/null || true
  wait "$SERVICE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in {1..30}; do
  if /usr/bin/curl -sf http://127.0.0.1:5077/api/status >/dev/null 2>&1; then break; fi
  sleep 0.2
done

"$APP"
