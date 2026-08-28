#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
./menubar/build.command
USER_HOME="/Users/$(id -un)"
AGENT_DIR="$USER_HOME/Library/LaunchAgents"
AGENT_PATH="$AGENT_DIR/com.wqs.codex-quota-menubar.plist"
RUNTIME_ROOT="$USER_HOME/Library/Application Support/CodexQuotaMonitor"
mkdir -p "$AGENT_DIR"
mkdir -p "$RUNTIME_ROOT/static" "$RUNTIME_ROOT/data"
cp app.py "$RUNTIME_ROOT/app.py"
cp -R static/. "$RUNTIME_ROOT/static/"
cp -R build/CodexQuotaMenuBar.app "$RUNTIME_ROOT/"
cp menubar/run.command "$RUNTIME_ROOT/run.command"
chmod +x "$RUNTIME_ROOT/run.command"
if [[ -d data ]]; then cp -R data/. "$RUNTIME_ROOT/data/"; fi
cp menubar/com.wqs.codex-quota-menubar.plist "$AGENT_PATH"
launchctl bootout "gui/$(id -u)/com.wqs.codex-quota-menubar" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_PATH"
echo "Installed and started $AGENT_PATH"
