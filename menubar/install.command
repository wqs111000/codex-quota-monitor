#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
./menubar/build.command
USER_HOME="/Users/$(id -un)"
AGENT_DIR="$USER_HOME/Library/LaunchAgents"
AGENT_PATH="$AGENT_DIR/com.wqs.codex-quota-menubar.plist"
mkdir -p "$AGENT_DIR"
cp menubar/com.wqs.codex-quota-menubar.plist "$AGENT_PATH"
launchctl bootout "gui/$(id -u)/com.wqs.codex-quota-menubar" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_PATH"
echo "Installed and started $AGENT_PATH"
