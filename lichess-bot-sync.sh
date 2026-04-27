#!/bin/bash
set -euo pipefail

REPO=/home/librechat/lichess-bot
LOGFILE=/home/librechat/lichess-bot/lichess_bot_auto_logs/sync.log

mkdir -p "$(dirname "$LOGFILE")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [sync] $*" | tee -a "$LOGFILE"; }

cd "$REPO"

git fetch origin
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse "@{u}")

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Bereits aktuell, kein Neustart nötig."
    exit 0
fi

log "Update gefunden ($(git rev-parse --short @)..$(git rev-parse --short "@{u}")), ziehe upstream..."
git pull --rebase origin master
log "Pull abgeschlossen."

get_playing_count() {
    TOKEN=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c['token'])")
    curl -sf -H "Authorization: Bearer $TOKEN" \
        https://lichess.org/api/account/playing | \
        python3 -c "import json,sys; print(len(json.load(sys.stdin)['nowPlaying']))"
}

while true; do
    PLAYING=$(get_playing_count)
    if [ "$PLAYING" -eq 0 ]; then
        log "Kein Spiel aktiv, starte lichess-bot.service neu..."
        sudo systemctl restart lichess-bot.service
        log "Neustart abgeschlossen."
        exit 0
    fi
    log "Noch $PLAYING Partie(n) laufen, erneute Prüfung in 3 Minuten..."
    sleep 180
done
