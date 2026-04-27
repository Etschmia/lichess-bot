#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

LOGFILE="challenge_manual_nohup.log"

nohup venv/bin/python challenge_manual.py >> "$LOGFILE" 2>&1 &

echo "challenge_manual.py gestartet (PID $!)"
echo "Logs: $(pwd)/$LOGFILE"
echo "Abbrechen: kill $!"
