# Betrieb: Lichess-Bot Voigtsbach (Engine Funken) — Host SYR-PE-BUTDEV

Gegenstück zu Martunis `BETRIEB-lichess-bot.md`, aber für diesen Host.
Dauerbetrieb läuft über **systemd**, nicht über die `voigtsbach-*.sh`-Skripte
(die stammen vom alten Grok-Host und bleiben nur als manuelle Fallbacks).

## 1. Layout

```
/var/www/but2/botdir/
├── .env                      # LICHESS_API_KEY (0600, Quelle des Tokens)
├── lichess-bot/              # Bridge-Fork, Branch feature/voigtsbach-bot
│   ├── config.yml            # nicht versioniert (.gitignore: *.yml)
│   ├── token.env             # nicht versioniert, 0600
│   ├── venv/                 # Python 3.14
│   └── game_records/         # PGN-Archiv
└── sparkengine/              # Engine Funken
    └── target/release/funken # UCI-Binary
```

Toolchain: Rust via rustup unter `~/.cargo` (user-lokal), `gcc`/`binutils`/
`libc6-dev` und `python3-venv` per apt nachinstalliert.

## 2. Token

Einzige Quelle ist `/var/www/but2/botdir/.env` (`LICHESS_API_KEY`).
Daraus abgeleitet: `lichess-bot/token.env` mit `LICHESS_BOT_TOKEN=…` (0600).
Die Bridge liest die Variable und überschreibt das Platzhalter-Feld in
`config.yml` (`lib/config.py`: `if "LICHESS_BOT_TOKEN" in os.environ`).
Beide Dateien sind gitignored. **Nie** ins Repo, nie in Logs.

Token neu setzen:

```bash
cd /var/www/but2/botdir/lichess-bot
set -a; . ../.env; set +a
umask 077; printf 'LICHESS_BOT_TOKEN=%s\n' "$LICHESS_API_KEY" > token.env
sudo systemctl restart lichess-bot-voigtsbach.service
```

## 3. systemd

Unit: `/etc/systemd/system/lichess-bot-voigtsbach.service`
(User `but2developer`, `Restart=always`, `KillSignal=SIGINT`,
`ProtectSystem=strict` + `ReadWritePaths` auf das Bridge-Verzeichnis).

```bash
sudo systemctl status  lichess-bot-voigtsbach.service
sudo systemctl restart lichess-bot-voigtsbach.service
journalctl -u lichess-bot-voigtsbach.service -f
```

Erwarteter Start-Log: `Engine configuration OK` → `Welcome Voigtsbach!` →
`awaiting challenges.`

`KillSignal=SIGINT`, weil die Bridge nur SIGINT sauber behandelt
(`lib/lichess_bot.py`, `signal.signal(signal.SIGINT, …)`). Zusammen mit
`quit_after_all_games_finish: true` spielt sie eine laufende Partie noch zu
Ende; `TimeoutStopSec=300` begrenzt das.

**Nie zwei Instanzen mit demselben Token.** Vor manuellem Start immer
`sudo systemctl stop lichess-bot-voigtsbach.service`.

## 4. Graceful Restart (Config-/Binary-Update)

```bash
cd /var/www/but2/botdir/lichess-bot
set -a; . ./token.env; set +a
curl -s -H "Authorization: Bearer $LICHESS_BOT_TOKEN" \
  https://lichess.org/api/account/playing | python3 -c \
  "import sys,json;print(len(json.load(sys.stdin)['nowPlaying']),'laufende Partien')"
# erst wenn 0:
sudo systemctl restart lichess-bot-voigtsbach.service
```

Engine-Update:

```bash
cd /var/www/but2/botdir/sparkengine && git pull
~/.cargo/bin/cargo build --release
./target/release/funken perft 5     # muss 4865609 liefern
# dann Graceful Restart wie oben
```

## 5. Rate-Limit auf `/api/stream/event`

Lichess kann diesen Endpunkt sperren:

```
429 {"error":"Please don't poll this endpoint, it is intended to be streamed."}
```

### Wie die Bridge damit umgeht

`api_get()` in `lib/lichess.py` sperrt den Endpunkt nach einem 429 **selbst,
clientseitig**, für 60 s (`set_rate_limit_delay`). Das Gate sitzt in
`get_path_template()` **vor** dem HTTP-Call, es geht in der Zeit also kein
Paket an Lichess. Ein enger Retry-Loop ist deshalb nicht das Problem —
typischerweise sieht man im Log einen Countdown von 59 s herunter und danach
normalen Betrieb.

Auf dem Martuni-Host treten solche Episoden im Schnitt alle ~2,7 Tage auf und
klingen **immer nach ~60 s** ab (Auskunft der Martuni-Session, 21.09.2026,
12 Episoden über 32 Tage). Ein 429 direkt nach Prozessstart ist dort ebenfalls
bekannt und folgenlos.

### Dauersperre (Vorfall 21.09.2026, offen)

Davon zu unterscheiden ist eine **anhaltende** Sperre. Am 21.09.2026 lieferte
`/api/stream/event` für den Voigtsbach-Token über mehr als 25 Minuten
durchgehend 429 — auch bei gestopptem Dienst und einer einzelnen, sauber
gestreamten `curl`-Verbindung nach 90 s Ruhe. Es lag also weder an der
Reconnect-Frequenz noch an einer zweiten Instanz:

- kein zweiter Prozess auf dem Host, nur ein Aufrufer von `get_event_stream()`
  (`lib/lichess_bot.py:121`, im Kindprozess)
- `https://lichess.org/api/user/voigtsbach` meldete kein `online`
- `/api/account` und `/api/account/playing` antworteten normal mit 200
- der allererste Stream-Versuch dieser Sitzung (15:50) bekam sofort 429
- betroffen ist der **echte Bridge-Client** (6 Treffer `HTTPError: 429` aus
  `lib/lichess.py`), also mit korrektem `User-Agent`
  (`lichess-bot/… user:Voigtsbach`), `timeout=(15, 20)` und `stream=True` —
  nicht nur die `curl`-Kontrollen
- acht Minuten mit vollständig gestopptem Dienst und *keinem einzigen*
  Stream-Request reichten nicht: der erste Versuch danach war wieder 429

**Was ausgeschlossen ist:** eine zweite Stream-Verbindung. `/api/user/voigtsbach`
meldet kein `online`, und bei Bot-Accounts hängt der Online-Status an genau
einer bestehenden Event-Stream-Verbindung. Es hält also niemand einen Stream,
auch nicht der alte Grok-Host.

**Was ausdrücklich nicht belegt ist:** die Ursache. Der `seenAt`-Zeitstempel
15:41 taugt nicht als Indiz für Fremdnutzung — `.env` wurde um 15:40:52
angelegt, neun Sekunden davor, das war also mit hoher Wahrscheinlichkeit das
Erzeugen und Testen des Tokens. `seenAt` wird zudem von jedem
authentifizierten Call aktualisiert, nicht nur von Streams.

Übrig bleibt eine serverseitig hinterlegte Sperre ohne aktiven Stream. Dafür
gibt es keinen positiven Beleg, nur die Abwesenheit anderer Erklärungen.
Offen ist insbesondere, **woran** sie hängt — Lichess limitiert auch per IP,
und dieser Entwicklungshost ist als Träger mindestens so plausibel wie der
Token:

|                  | alter Token | frischer Token |
|------------------|-------------|----------------|
| SYR-PE-BUTDEV    | gesperrt    | ungetestet     |
| andere IP        | ungetestet  | ungetestet     |

Nur das erste Feld ist gemessen. **Nächste Schritte:** ein frisches
OAuth-Token (Scope `bot:play`) beantwortet die halbe Frage sofort und ist
billig; ein Versuch über eine andere IP (notfalls Handy-Hotspot) trennt die
Zeilen. Beides braucht Tobias.

### Regeln

- `/api/stream/event` **nie** manuell mit `curl` testen. Jeder kurze, wieder
  abgebrochene Request ist aus Lichess' Sicht genau das Polling, das die
  Fehlermeldung benennt — Diagnose kann die Sperre also verlängern. Für
  Diagnose `/api/account` oder `/api/account/playing` benutzen.
- Soll die Erholung gemessen werden, braucht es **vollständige** Funkstille:
  Dienst aus, kein `curl`, kein `/api/account`, gar nichts mit dem Token.
  Solange irgendetwas weiter anfragt, setzt sich die Quote nicht zurück.
- Zwischen Neustarts ein bis zwei Minuten Abstand lassen.
- Der Dienst muss bei einer Sperre nicht angefasst werden: `Restart=always`
  plus das Backoff unten verbinden automatisch, sobald die Sperre fällt.

### Lokaler Patch

`lib/lichess_bot.py`, `watch_control_stream`, aufbauend auf `bbc306f`:
`RateLimitedError` wird getrennt behandelt, die gemeldete Wartezeit
respektiert und über aufeinanderfolgende Treffer verdoppelt
(60 s → 120 s → 240 s, Deckel 300 s), Reset nach erfolgreicher Verbindung.
`stop.terminated` wird im Sekundentakt geprüft, damit das Herunterfahren
reaktionsfähig bleibt.

Zweck ist **nicht**, den 60-s-Normalfall zu ändern (dort ist Strike 1 mit 60 s
identisch zum Upstream-Verhalten), sondern bei einer Dauersperre nicht jede
Minute einen echten Request zu schicken. Der Martuni-Host fährt hier
unverändert Upstream und kommt damit aus.

## 6. Zugquellen

Ausschließlich Funken. In `config.yml` sind deaktiviert: Polyglot-Buch,
`chessdb_book`, `lichess_cloud_analysis`, `lichess_opening_explorer`,
`online_egtb`, Syzygy, Gaviota, Ponder sowie Resign/Draw-Offer durch die
Bridge. `uci_options` enthält nur `Threads: 1`, `Hash: 256`,
`Move Overhead: 100` — mehr kennt Funken nicht (`funken` meldet per `uci`
genau diese drei).

## 7. Angenommene Herausforderungen

`standard` (keine Varianten — Funken kann keine), `bullet`/`blitz`/`rapid`/
`classical` (kein correspondence), `casual` **und** `rated`,
`concurrency: 1` (Engine ist single-threaded), `accept_bot: true`,
Grundzeit 60 s bis 10800 s.

## 8. Ausgehende Challenges (derzeit aus)

`matchmaking.allow_matchmaking: false`. Cron-Skripte sind vorhanden, aber
**nicht** eingerichtet. Siehe `VOIGTSBACH-CRON.md`. Wenn gewünscht:

```bash
./run_challenge_cron.sh blitz standard   # nur standard, kein chess960
```

429-Cooldown in `challenge_cron_cooldown.json` respektieren, nicht anfassen.
Erst nach nachweislich stabilem Bot-Betrieb aktivieren.

**Warnung aus dem Martuni-Betrieb (21.09.2026):** Der schmerzhafte 429 kam
dort nicht vom Event-Stream, sondern von ausgehenden Challenges. Zwei Crons
feuerten je 10 Challenges in 1–2 Sekunden ohne Backoff (~40 Calls/h); Lichess
nahm den Account daraufhin stundenlang aus der Bot-Wiese, und weil die Crons
weiterliefen, setzte sich die Quote nie zurück. Der Bot-Dienst selbst sah
dabei unauffällig aus ("awaiting challenges") — das macht die Diagnose
schwierig.

Martunis Gegenmittel, vor einer Aktivierung hier zu übernehmen:
- eigene RateLimited-Exception in `challenge_cron.py`, ausgelöst bei HTTP 429
  **und** bei "too many requests" im Response-Body (Lichess antwortet nicht
  immer mit 429)
- `main()` bricht den **ganzen Lauf** ab, nicht nur den einzelnen Versuch —
  ein Cron-Run feuert dann höchstens eine Anfrage statt zwanzig
- `handle_challenge()` in `lib/lichess.py` liest `ratelimit.seconds` aus dem
  Body; das ist genauer als eine geratene Pause

## 9. Alter Grok-Host

Dort ist Voigtsbach abgeschaltet und muss es bleiben — paralleler Betrieb mit
demselben Token führt zu genau dem Rate-Limit aus Abschnitt 5.
Prüfung, ob sonst jemand mit dem Token verbunden ist:

```bash
curl -s https://lichess.org/api/user/voigtsbach | grep -o '"online":[a-z]*'
```

Bei gestopptem Dienst hier muss das `online:false` bzw. kein Feld liefern.

---

## Anhang: Referenzwerte vom Martuni-Host (Stand 21.09.2026)

Auskunft der Martuni-Session, zum Einordnen, was „normal“ ist:

- **Event-Stream-Reconnects in 7 Tagen: null.** Der Stream steht im
  Normalbetrieb einfach durch. Der Reconnect-Pfad — und damit auch das
  Backoff aus Abschnitt 5 — ist im laufenden Betrieb praktisch toter Code.
  Der Engpass liegt vollständig im *ersten* Verbindungsaufbau.
- 429-Episoden auf `/api/stream/event`: ~alle 2,7 Tage, Dauer **immer ~60 s**.
  Eine Dauersperre wie hier am 21.09. ist dort nie aufgetreten.
- Ein 429 unmittelbar nach Prozessstart ist auch dort bekannt und folgenlos.
- `move_overhead: 1000` (Bridge) + `MoveOverhead: 100` (Engine, dort ohne
  Leerzeichen im Namen): in 952 Partien **15 Zeitüberschreitungen, alle beim
  Gegner**, keine einzige bei Martuni — bis hinunter zu 60+0 und 30+0.
  Einschränkung: dort 2 Kerne mit `concurrency: 2`.
- Martunis Unit setzt kein `KillSignal`, schickt also SIGTERM, obwohl im Bot
  nur SIGINT verdrahtet ist — der Prozess geht dort hart runter. Deshalb hier
  `KillSignal=SIGINT`. Achtung: ein **zweites** SIGINT beendet sofort
  („Received second SIGINT. Quitting now.“), relevant nur bei manuellem
  Nachhelfen.
