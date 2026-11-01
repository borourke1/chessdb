#!/usr/bin/env bash
# Install (or reinstall) the two launchd agents behind the monthly OTB ingest.
# See src/chessdb/ingest/scheduled.py for what each one does.
#
#   scripts/install_schedule.sh             install / update both agents
#   scripts/install_schedule.sh --uninstall remove them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABELS=(com.chessdb.otb-remind com.chessdb.otb-ingest-new)

for label in "${LABELS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
done
if [[ "${1:-}" == "--uninstall" ]]; then
  echo "Removed ${LABELS[*]}"
  exit 0
fi

[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv - see README Setup" >&2; exit 1; }
mkdir -p "$REPO/data/logs" "$REPO/data/downloads/otb_lumbra" "$AGENTS"
LOG="$REPO/data/logs/scheduled_ingest.log"

# $1 label, $2 subcommand, $3 extra plist keys (trigger)
write_agent() {
  cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/.venv/bin/python</string>
    <string>-m</string>
    <string>chessdb.ingest.scheduled</string>
    <string>$2</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
$3
</dict>
</plist>
EOF
  plutil -lint -s "$AGENTS/$1.plist"
  launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"
}

DAILY='  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>'

write_agent com.chessdb.otb-remind remind "$DAILY"
write_agent com.chessdb.otb-ingest-new ingest-new "  <key>WatchPaths</key>
  <array><string>$REPO/data/downloads/otb_lumbra</string></array>
$DAILY"

echo "Installed ${LABELS[*]} (logs: $LOG)"
