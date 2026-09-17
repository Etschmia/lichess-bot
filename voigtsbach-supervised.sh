#!/usr/bin/env bash
# Single-instance supervised runner for Voigtsbach.
# This host has no systemd init — flock replaces "never two instances",
# RestartSec mirrors Martuni's lichess-bot.service (see BETRIEB-lichess-bot.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOCK="$ROOT/voigtsbach.lock"
PIDFILE="$ROOT/voigtsbach.pid"
LOG="$ROOT/voigtsbach.log"
START="$ROOT/voigtsbach-start.sh"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) already running (lock held); exit" >>"$LOG"
  exit 0
fi

echo $$ >"$PIDFILE"
cleanup() { rm -f "$PIDFILE"; }
trap cleanup EXIT

# Martuni uses RestartSec=5; after rate-limit wait longer so we don't hammer /api/stream/event
backoff=5
while true; do
  echo "$(date -Is) supervised start" >>"$LOG"
  set +e
  "$START" >>"$LOG" 2>&1
  code=$?
  set -e
  if tail -n 80 "$LOG" | grep -q 'rate-limited'; then
    backoff=120
  else
    backoff=5
  fi
  echo "$(date -Is) exited code=$code; sleep ${backoff}s before restart" >>"$LOG"
  sleep "$backoff"
done
