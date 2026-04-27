#!/usr/bin/env bash
# check_martuni_bot.sh
# Prüft ob "Martuni" in der Liste der aktuell online sichtbaren Lichess-Bots erscheint.
# Das entspricht exakt dem was auf https://lichess.org/player/bots angezeigt wird.
#
# Endpoint: GET /api/bot/online  (NDJSON-Stream, max. 50 Bots per Default)
# Mit nb=200 holen wir mehr — Lichess erlaubt bis zu 200.
#
# CSV-Format:
#   datetime_iso,unix_timestamp,online,http_status,detail
#
# online:
#   1  = Martuni ist online und in der Bot-Liste sichtbar
#   0  = Martuni ist nicht in der Bot-Liste (offline oder ausgeblendet)
#  -2  = Netzwerkfehler / unerwarteter HTTP-Status

set -euo pipefail

# ── Konfiguration ──────────────────────────────────────────────────────────────
BOTNAME="Martuni"
API_URL="https://lichess.org/api/bot/online?nb=200"
CSV_FILE="/home/librechat/lichess-bot/martuni_bot_check.csv"
TIMEOUT=20
# ──────────────────────────────────────────────────────────────────────────────

DATETIME=$(date --iso-8601=seconds)
UNIX_TS=$(date +%s)

# CSV-Header anlegen falls Datei neu
if [[ ! -f "$CSV_FILE" ]]; then
    echo "datetime,unix_timestamp,online,http_status,detail" > "$CSV_FILE"
fi

HTTP_BODY=$(mktemp)
HTTP_STATUS=$(curl \
    --silent \
    --max-time "$TIMEOUT" \
    --write-out "%{http_code}" \
    --output "$HTTP_BODY" \
    --header "Accept: application/x-ndjson" \
    "$API_URL" 2>/dev/null) || HTTP_STATUS="-2"

if [[ "$HTTP_STATUS" == "-2" ]]; then
    ONLINE="-2"
    DETAIL="curl_error"

elif [[ "$HTTP_STATUS" == "200" ]]; then
    # NDJSON: jede Zeile ist ein Bot-Objekt {"id":"martuni","username":"Martuni",...}
    # Suche auf dem id-Feld (Lichess IDs sind immer lowercase)
    BOTNAME_LOWER=$(echo "$BOTNAME" | tr '[:upper:]' '[:lower:]')
    if grep -q "\"id\":\"${BOTNAME_LOWER}\"" "$HTTP_BODY"; then
        ONLINE="1"
        DETAIL="online_and_listed"
    else
        ONLINE="0"
        BOT_COUNT=$(grep -c '"id"' "$HTTP_BODY")
        DETAIL="not_listed_in_${BOT_COUNT}_bots"
    fi

else
    ONLINE="-2"
    DETAIL="unexpected_http_${HTTP_STATUS}"
fi

rm -f "$HTTP_BODY"

echo "${DATETIME},${UNIX_TS},${ONLINE},${HTTP_STATUS},${DETAIL}" >> "$CSV_FILE"
echo "[${DATETIME}] online=${ONLINE} status=${HTTP_STATUS} detail=${DETAIL}" >&2