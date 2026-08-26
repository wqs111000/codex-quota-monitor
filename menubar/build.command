#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/CodexQuotaMenuBar.app/Contents/MacOS
swiftc menubar/CodexQuotaMenuBar.swift -o build/CodexQuotaMenuBar.app/Contents/MacOS/CodexQuotaMenuBar -framework Cocoa
cp menubar/Info.plist build/CodexQuotaMenuBar.app/Contents/Info.plist
echo "Built build/CodexQuotaMenuBar.app"
