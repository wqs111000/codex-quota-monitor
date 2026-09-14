#!/bin/zsh
set -euo pipefail

ROOT="${CODEX_QUOTA_RUNTIME_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="/opt/homebrew/bin/python3"
APP="$ROOT/Quota.app/Contents/MacOS/Quota"
HOST="${CHATGPT_QUOTA_BIND_HOST:-127.0.0.1}"

# Optional local-only notification settings; never commit this file.
if [[ -f "$ROOT/notification.env" ]]; then
  set -a
  source "$ROOT/notification.env"
  set +a
fi

"$PYTHON" "$ROOT/app.py" --host "$HOST" >/tmp/codex-quota-service.log 2>&1 &
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
