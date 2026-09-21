#!/usr/bin/env bash
# Start Voigtsbach (Funken) lichess-bot in the foreground.
# Token is loaded from token.env (or LICHESS_BOT_TOKEN already in the environment).
set -euo pipefail
export PYTHONUNBUFFERED=1
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ -z "${LICHESS_BOT_TOKEN:-}" ]; then
  if [ -f "$ROOT/token.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/token.env"
    set +a
  elif [ -f "$ROOT/../.env" ]; then
    # Fallback: map LICHESS_API_KEY -> LICHESS_BOT_TOKEN without printing
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/../.env"
    set +a
    if [ -n "${LICHESS_API_KEY:-}" ]; then
      export LICHESS_BOT_TOKEN="$LICHESS_API_KEY"
      unset LICHESS_API_KEY
    fi
  fi
fi

if [ -z "${LICHESS_BOT_TOKEN:-}" ]; then
  echo "FEHLT: LICHESS_BOT_TOKEN (set env or create token.env from ../.env)." >&2
  exit 1
fi

ENGINE="${FUNKEN_BIN:-$ROOT/../sparkengine/target/release/funken}"
[ -x "$ENGINE" ] || { echo "FEHLT: $ENGINE — build sparkengine first."; exit 1; }
[ -f "$ROOT/config.yml" ] || { echo "FEHLT: $ROOT/config.yml"; exit 1; }
[ -x "$ROOT/venv/bin/python" ] || { echo "FEHLT: venv — run python3 -m venv venv && venv/bin/pip install -r requirements.txt"; exit 1; }

EXTRA_ARGS=("$@")
if [ ${#EXTRA_ARGS[@]} -eq 0 ]; then
  EXTRA_ARGS=(-v)
fi

set +x
exec "$ROOT/venv/bin/python" "$ROOT/lichess-bot.py" --config "$ROOT/config.yml" "${EXTRA_ARGS[@]}"
