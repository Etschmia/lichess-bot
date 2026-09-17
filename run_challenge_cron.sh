#!/usr/bin/env bash
# Run challenge_cron with Voigtsbach token; never print the token.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
if [ -z "${LICHESS_BOT_TOKEN:-}" ]; then
  if [ -f "$ROOT/token.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/token.env"
    set +a
  elif [ -f /workspace/.env ]; then
    set -a
    # shellcheck disable=SC1091
    source /workspace/.env
    set +a
    export LICHESS_BOT_TOKEN="${LICHESS_API_KEY:-}"
    unset LICHESS_API_KEY || true
  fi
fi
[ -n "${LICHESS_BOT_TOKEN:-}" ] || { echo "FEHLT: LICHESS_BOT_TOKEN" >&2; exit 1; }
MODE="${1:?mode blitz|rapid}"
VARIANT="${2:-standard}"
exec "$ROOT/venv/bin/python" "$ROOT/challenge_cron.py" --mode "$MODE" --variant "$VARIANT"
