#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/Quota.app/Contents/MacOS build/Quota.app/Contents/Resources
swiftc menubar/CodexQuotaMenuBar.swift -o build/Quota.app/Contents/MacOS/Quota -framework Cocoa
cp menubar/Info.plist build/Quota.app/Contents/Info.plist
cp assets/Quota.icns build/Quota.app/Contents/Resources/Quota.icns
echo "Built build/Quota.app"
