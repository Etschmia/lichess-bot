# Wie `/api/bot/online` in Lichess funktioniert

Recherchiert 2026-04-25 anhand des Lila-Quellcodes (lichess-org/lila auf GitHub).

---

## Relevante Quelldateien in Lila

| Datei | Inhalt |
|---|---|
| `modules/bot/src/main/OnlineApiUsers.scala` | Kern-Registry: TTL-Cache, `setOnline` |
| `modules/api/src/main/EventStream.scala` | Ruft `setOnline` alle 7 s vom Control-Stream aus auf |
| `modules/bot/src/main/GameStateStream.scala` | Ruft `setOnline` alle 7 s vom Game-Stream aus auf |
| `modules/user/src/main/UserApi.scala` | `visibleBotsByIds()` — die MongoDB-Query hinter dem Endpoint |
| `app/controllers/PlayApi.scala` | HTTP-Handler für `GET /api/bot/online` |

---

## Was den Endpoint steuert

### 1. "Online" = aktiver SSE-Stream, nicht WebSocket-Präsenz

Lichess führt einen `ExpireCallbackMemo[UserId]` mit **10-Sekunden-TTL**:

```scala
// OnlineApiUsers.scala
private val cache = ExpireCallbackMemo[UserId](scheduler, 10.seconds, ...)

def setOnline(userId: UserId): Unit =
  cache.put(userId)   // setzt den 10-s-Timer zurück
```

Ein Bot-ID bleibt im Cache nur solange ein Stream-Actor `setOnline` aufruft.
Zwei Code-Pfade tun das automatisch, jeweils alle **7 Sekunden**:

- **Control-Stream** (`/api/stream/event`) — `EventStream.scala`
- **Game-Stream** (`/api/bot/game/stream/{id}`) — `GameStateStream.scala`

→ Bricht der Control-Stream ab **und** läuft kein Game-Stream, fliegt der Bot nach ≤ 10 s aus der Liste.

### 2. MongoDB-Filter — was gezeigt wird

```scala
Match(inIds                      // nur IDs aus dem TTL-Cache
  ++ botWithBioSelect            // title: "BOT" UND profile.bio existiert
  ++ enabledSelect               // Konto nicht gesperrt
  ++ notLame)                    // nicht als engine/booster markiert
Sort(Descending("roles"),        // verifizierte/offizielle Bots zuerst
     Descending("time.human"))   // dann nach Spielzeit gegen Menschen (!)
Limit(200)
```

**`botWithBioSelect` = `{ title: "BOT", "profile.bio": { $exists: true } }`**  
Kein Bio → niemals gelistet, egal wie aktiv.

### 3. Ranking innerhalb der 200

1. Bots mit Site-Rollen (offiziell/verifiziert) — immer oben
2. **`time.human`** — akkumulierte Spielzeit gegen menschliche Gegner, absteigend

Bei `?nb=200` (unser Check-Skript) sieht man alle 200; auf lichess.org/player/bots
werden weniger angezeigt. Je weiter oben Martuni steht, desto häufiger
taucht es in kleineren Slices auf.

### 4. Challenge-Aktivität hat keinen Einfluss

Challenge-API und `OnlineApiUsers` sind vollständig getrennt. Der challenge-cron
verändert das Listing **nicht** direkt. Die beobachtete Korrelation
(cron an → offline) hatte eine andere Ursache (Rate-Limit-Incident, Stream-Drops).

---

## Warum Martuni intermittierend verschwindet

Die 10-s-TTL läuft ab, wenn der SSE-Control-Stream abbricht und nicht schnell
genug reconnectet. In `lichess.py`:

```python
def get_event_stream(self) -> requests.models.Response:
    return self.api_get("stream_event", stream=True, timeout=15)
```

`timeout=15` ist ein einzelner Integer → gilt als **Connect-Timeout** (und implizit
Read-Timeout pro `socket.recv()`-Aufruf). Lila sendet alle 7 s ein Keepalive-`\n`,
das `iter_lines()` als `b""` liefert und damit den 15-s-Read-Timeout
zurücksetzt. Bei einer stabilen Verbindung sollte das ausreichen.

Tritt aber ein TCP-Reset auf (Netzwerk-Glitch, Cloud-NAT-Timeout), fängt
`watch_control_stream` den Fehler, schläft 1 s, und `api_get` läuft erneut
mit `@backoff(max_time=60)`. Während der Reconnect-Phase fehlt das `setOnline`,
und nach 10 s fällt der Bot aus der Liste.

### Mögliche Verbesserung: expliziter Read-Timeout

`api_get` hat `timeout: int` statt `timeout: int | tuple`. Mit einem Tupel
könnte man Connect- und Read-Timeout trennen und einen aggressiveren Read-Timeout
setzen, der bei ausbleibendem Keepalive nach z. B. 20 s sofort reconnectet,
statt auf einen TCP-Level-Timeout zu warten:

```python
# lib/lichess.py — get_event_stream
return self.api_get("stream_event", stream=True, timeout=(15, 20))
# und api_get-Signatur auf timeout: int | tuple[int, int] = 2 erweitern
```

Das wäre eine minimale Änderung mit upstream-PR-Potenzial.

---

## Hebel für höheres Listing

| Maßnahme | Effekt | Aufwand |
|---|---|---|
| **`time.human` erhöhen** (mehr Partien gegen Menschen) | Direktes Ranking-Kriterium | Mittel — Challenge-Cron auf menschliche Gegner ausrichten oder eingehende Challenges von Menschen annehmen |
| Bio im Lichess-Profil gesetzt halten | Pflichtvoraussetzung | Einmalig, bereits erledigt |
| Stream-Stabilität verbessern (Read-Timeout-Fix) | Verhindert unnötige Offline-Phasen | Klein — 2 Zeilen in `lib/lichess.py` |
| Cron-Pacing (≥ 30 s zwischen Challenges, Abbruch bei 429) | Verhindert Rate-Limit → Verhindert längere Offline-Phasen | Bereits umgesetzt (2026-04-25) |

---

## Monitoring

`martuni_bot_check.csv` — 10-Minuten-Intervall via `check_martuni_bot.sh`,
prüft `GET /api/bot/online?nb=200` auf `"id":"martuni"`.
