#!/usr/bin/env bash
# Wöchentlicher Upstream-Sync: lichess-bot-devs/lichess-bot → eigener Fork.
# Bei Merge-Konflikt → Benachrichtigungs-Mail via Resend.
set -uo pipefail

REPO=/home/librechat/lichess-bot
LOGFILE="$REPO/lichess_bot_auto_logs/upstream-sync.log"
RESEND_ENV=/home/librechat/pgn-db/server/.env.local
NOTIFY_TO="brendler@syrcon.com"

mkdir -p "$(dirname "$LOGFILE")"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [upstream-sync] $*" | tee -a "$LOGFILE"; }

# Resend-Key aus pgn-db .env.local
RESEND_API_KEY=""
[[ -f "$RESEND_ENV" ]] && RESEND_API_KEY=$(grep '^RESEND_API_KEY=' "$RESEND_ENV" | cut -d= -f2- | tr -d '"'"'")

send_conflict_mail() {
    local subject="$1"
    local body_file="$2"
    if [[ -z "$RESEND_API_KEY" ]]; then
        log "WARNUNG: RESEND_API_KEY nicht gefunden — Mail übersprungen."
        return
    fi
    python3 -c "
import json, urllib.request, sys
key, to, subject, bodyfile = sys.argv[1:]
body = open(bodyfile).read()
payload = json.dumps({
    'from': 'lichess-bot <noreply@martuni.de>',
    'to': [to],
    'subject': subject,
    'html': '<pre style=\"font-family:monospace;white-space:pre-wrap\">' + body + '</pre>'
}).encode()
req = urllib.request.Request(
    'https://api.resend.com/emails',
    data=payload,
    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req)
    print('Mail OK:', r.status)
except Exception as e:
    print('Mail-Fehler:', e, file=sys.stderr)
    sys.exit(1)
" "$RESEND_API_KEY" "$NOTIFY_TO" "$subject" "$body_file" >> "$LOGFILE" 2>&1 \
    && log "Benachrichtigungs-Mail gesendet." \
    || log "FEHLER beim Mail-Versand."
}

cd "$REPO"

# ── Upstream fetchen ──────────────────────────────────────────────────────
log "============================="
log "Upstream-Sync gestartet."
if ! git fetch upstream master >> "$LOGFILE" 2>&1; then
    log "FEHLER: git fetch upstream gescheitert."
    exit 1
fi

LOCAL=$(git rev-parse HEAD)
UPSTREAM_HEAD=$(git rev-parse upstream/master)

if [[ "$LOCAL" = "$UPSTREAM_HEAD" ]]; then
    log "Bereits aktuell — kein Merge nötig."
    exit 0
fi

NEW_COMMITS=$(git log --oneline HEAD..upstream/master)
log "Neue Upstream-Commits:"
while IFS= read -r line; do log "  $line"; done <<< "$NEW_COMMITS"

# ── Merge versuchen ───────────────────────────────────────────────────────
MERGE_TMP=$(mktemp)
if git merge --no-ff --no-edit upstream/master > "$MERGE_TMP" 2>&1; then
    log "Merge erfolgreich."
    cat "$MERGE_TMP" >> "$LOGFILE"
    rm -f "$MERGE_TMP"

    if ! git push origin master >> "$LOGFILE" 2>&1; then
        log "FEHLER: git push origin gescheitert."
        exit 1
    fi
    log "Push zu origin (Etschmia/lichess-bot) erfolgreich."

    # Graceful Bot-Neustart
    TOKEN=$(python3 -c "import yaml; print(yaml.safe_load(open('config.yml'))['token'])")
    while true; do
        PLAYING=$(curl -sf -H "Authorization: Bearer $TOKEN" \
            https://lichess.org/api/account/playing \
            | python3 -c "import json,sys; print(len(json.load(sys.stdin)['nowPlaying']))")
        if [[ "$PLAYING" -eq 0 ]]; then
            log "Kein Spiel aktiv — starte lichess-bot.service neu."
            sudo systemctl restart lichess-bot.service
            log "Neustart abgeschlossen."
            break
        fi
        log "Noch $PLAYING Partie(n) aktiv — prüfe erneut in 3 Minuten."
        sleep 180
    done
else
    log "KONFLIKT beim Merge — breche ab."
    CONFLICT_FILES=$(git diff --name-only --diff-filter=U)
    git merge --abort >> "$LOGFILE" 2>&1 || true
    cat "$MERGE_TMP" >> "$LOGFILE"
    rm -f "$MERGE_TMP"

    MAIL_BODY=$(mktemp)
    cat > "$MAIL_BODY" << EOF
Upstream-Sync für lichess-bot ist mit einem Merge-Konflikt gescheitert.

Neue Upstream-Commits:
$NEW_COMMITS

Konflikt-Dateien:
$CONFLICT_FILES

Manuell lösen:
  cd $REPO
  git fetch upstream
  git merge --no-ff upstream/master
  # Konflikte in den obigen Dateien beheben, dann:
  git add <datei>
  git commit
  git push origin master
  sudo systemctl restart lichess-bot.service
EOF

    send_conflict_mail "ToDo: Merge-Konflikt lichess-bot upstream-sync" "$MAIL_BODY"
    rm -f "$MAIL_BODY"
    exit 1
fi
