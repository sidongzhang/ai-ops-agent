#!/usr/bin/env bash
# 安装每日备份（macOS launchd）：每天 03:30 执行 scripts/backup.sh。
# 幂等：重复执行会先卸载旧任务再加载。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.aiops.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR="${BACKUP_HOUR:-3}"
MINUTE="${BACKUP_MINUTE:-30}"

mkdir -p "$HOME/Library/LaunchAgents"
cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/backup.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/logs/backup.launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/backup.launchd.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

echo "已安装：${PLIST}（每天 ${HOUR}:$(printf '%02d' "$MINUTE")）"
echo "立即验证一次：$ROOT/scripts/backup.sh && $ROOT/scripts/backup.sh check"
