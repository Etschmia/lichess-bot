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

### PGN-Archiv und die Härtung

`pgn_directory` und `pgn_file_grouping` müssen in `config.yml` **aktiv** sein —
in `config.yml.default` sind beide auskommentiert. Fehlen sie, steigt der Code
still aus (`lib/lichess_bot.py:1046` und `:1182`, jeweils
`if not config.pgn_directory`): kein Fehler, keine Warnung, keine Datei. Der
Bot spielt dabei völlig normal. Prüfen:

```bash
grep -nE "^pgn_directory|^pgn_file_grouping" config.yml
venv/bin/python -c "import sys;sys.path.insert(0,'.');from lib.config import load_config;print(load_config('config.yml').pgn_directory)"
```

Der Pfad ist relativ zum `WorkingDirectory` der Unit; anlegen muss man ihn
nicht (`os.makedirs(..., exist_ok=True)`, Zeile 1190). Bei
`pgn_file_grouping: "game"` entsteht eine Datei pro Partie
(`Weiß vs Schwarz - GameId.pgn`).

**Host-spezifisch:** Diese Unit läuft mit `ProtectSystem=strict`, das
Dateisystem ist also bis auf `ReadWritePaths` schreibgeschützt.
`game_records/` liegt darunter — geprüft mit einem Testschreibvorgang unter
denselben Sandbox-Eigenschaften:

```bash
sudo systemd-run --quiet --wait --collect --pipe \
  --property=User=but2developer --property=Group=www-data \
  --property=WorkingDirectory=/var/www/but2/botdir/lichess-bot \
  --property=ProtectSystem=strict \
  --property=ReadWritePaths=/var/www/but2/botdir/lichess-bot \
  /bin/bash -c 'touch game_records/.probe && rm game_records/.probe && echo OK'
```

Wer `ReadWritePaths` ändert oder `pgn_directory` aus dem Bridge-Verzeichnis
heraus verlegt, muss das erneut prüfen — sonst schreibt der Bot still nichts.

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

### Dauersperre (Vorfall 21.09.2026, GELÖST)

Davon zu unterscheiden ist eine **anhaltende** Sperre. Am 21.09.2026 lieferte
`/api/stream/event` für den Voigtsbach-Token von 15:50 bis 16:19 durchgehend
429 — betroffen war der echte Bridge-Client (6 Treffer `HTTPError: 429` aus
`lib/lichess.py`, also mit korrektem `User-Agent` und `stream=True`), nicht
nur manuelle `curl`-Tests. Acht Minuten mit vollständig gestopptem Dienst
und keinem einzigen Stream-Request reichten nicht.

**Ursache: der alte Grok-Host.** Nachdem Tobias dort um ~16:20 alles gelöscht
hatte, verband sich der Bot beim nächsten Start um 16:21:05 sofort und
fehlerfrei — kein 429, kein Reconnect, ESTABLISHED-Socket vom
Control-Stream-Kindprozess. Der alte Host hatte den Token also weiterhin
belegt oder dessen Quote verbraucht, obwohl er als abgeschaltet galt.

**Lehre für die Diagnose: den richtigen Endpunkt fragen.** Während des
Vorfalls habe ich `/api/user/voigtsbach` abgefragt, dort blieb `online` leer
— auch dann noch, als der Stream nachweislich stand. Daraus „das Feld ist
unbrauchbar“ zu schließen war falsch: `/api/user/{}` ist in der Bridge
`public_data` (`lib/lichess.py:471`) und trägt den Online-Status gar nicht.
Die richtige Quelle ist `/api/users/status`:

```bash
curl -s "https://lichess.org/api/users/status?ids=voigtsbach"
# [{"name":"Voigtsbach","title":"BOT","id":"voigtsbach","online":true}]
```

Genau die nutzt auch die Bridge selbst in `is_online()`
(`lib/lichess.py:466`). `/api/user/{}` wird im Code nirgends für den
Online-Status ausgewertet.

Der Fehlschluss, der hier eine halbe Stunde gekostet hat, war also nicht das
Feld, sondern meine Folgerung daraus: „kein `online`, also hält niemand einen
Stream, also kann kein Fremdsystem die Ursache sein.“ Der erste Halbsatz war
schon nicht gemessen.

Primärindikator bleibt trotzdem der lokale Socket — er misst die Verbindung
selbst statt Lichess' Sicht darauf, und genau diese Differenz *war* der
Vorfall:

```bash
sudo ss -tnp | grep 37.187.        # ESTAB vom Kindprozess = Stream steht
journalctl -u lichess-bot-voigtsbach.service --since "-10min" | grep -cE "429|Control stream error"
curl -s "https://lichess.org/api/users/status?ids=voigtsbach"   # zweite, unabhängige Quelle
```

### Watchdog: der Bot startet sich selbst neu

`check_online_status()` (`lib/lichess_bot.py:542`, aufgerufen Zeile 450) prüft
**stündlich** (`Timer(hours(1))`) über `is_online()`, ob Lichess den Account
als online sieht, und setzt bei `false` `stop.restart = True` — im Log
sichtbar als `Will restart lichess-bot`.

Bei einer **Stream**-Sperre ist das ein Verstärker: Der Stream kommt nicht
hoch, Lichess sieht den Account offline, der Watchdog startet neu, der
Neustart ist ein neuer Stream-Versuch, der wieder 429 bekommt. Am 21.09. hat
er nicht mitgemischt (null Treffer, die Sperre dauerte unter einer Stunde) —
bei einer mehrstündigen Stream-Sperre käme pro Stunde ein zusätzlicher
Versuch dazu.

**Nicht übertragen auf eine Challenge-Sperre** (wie die 5h45/5h15 im Anhang):
Dort ist nur `/api/challenge/{}` dicht, der Event-Stream steht weiter, Lichess
sieht den Account als online, und `is_online()` liefert `true` — die Bedingung
des Watchdogs ist nie erfüllt, er schlägt gar nicht an. Die beiden Fälle
sehen im Journal ähnlich aus, haben hier aber nichts miteinander zu tun.

Prüfen mit:

```bash
journalctl -u lichess-bot-voigtsbach.service --since "-24h" | grep -c "Will restart lichess-bot"
```

Auch `seenAt` trägt wenig: es wird von **jedem** authentifizierten Call
aktualisiert, nicht nur von Streams, und war hier schlicht der Zeitstempel
der Token-Erzeugung (`.env` 15:40:52, `seenAt` 15:41:01).

**Wenn es wieder auftritt:** zuerst prüfen, ob irgendein anderer Host den
Token benutzt — das ist nach diesem Vorfall die mit Abstand wahrscheinlichste
Ursache. Erst danach an Quoten denken. Zur Größenordnung: auf dem
Martuni-Host dauerten zwei Sperren des *Challenge*-Endpunkts 5 h 45 min bzw.
5 h 15 min, und zwar **obwohl** durchgehend weiter angefragt wurde
(~40 abgewiesene Requests/h über 14 Cron-Läufe). Das spricht für ein
zeitbasiertes Quotenfenster, nicht für „solange du klopfst, bleibt zu“ —
Funkstille ist also nicht der Wirkmechanismus, Geduld schon. Anderer
Endpunkt, andere Quote: als Größenordnung nehmen, nicht als Messwert.

Falls sich kein Fremdnutzer findet und die Sperre bleibt, trennt diese Matrix
tokengebunden von hostgebunden (Lichess limitiert auch per IP):

|                  | alter Token | frischer Token |
|------------------|-------------|----------------|
| SYR-PE-BUTDEV    | gemessen    | ungetestet     |
| andere IP        | ungetestet  | ungetestet     |

Ein frisches OAuth-Token (Scope `bot:play`) ist billig und beantwortet die
halbe Frage; „andere IP“ reicht notfalls als Handy-Hotspot.

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

**Achtung, falscher Endpunkt:** `/api/user/{}` trägt den Online-Status nicht.
Richtig ist:

```bash
curl -s "https://lichess.org/api/users/status?ids=voigtsbach"
```

Bei gestopptem Dienst hier muss das `"online":false` bzw. kein `online`
liefern. Meldet es `true`, benutzt ein anderer Host den Token.

Verlass dich darauf aber nicht allein: Am 21.09.2026 lief auf dem Grok-Host
trotz „abgeschaltet“ noch etwas mit diesem Token und blockierte den Betrieb
hier knapp 30 Minuten lang, bis dort alles gelöscht wurde. Sicher ist nur,
auf dem anderen Host selbst nachzusehen.

---

## Anhang: Referenzwerte vom Martuni-Host (Stand 21.09.2026)

Auskunft der Martuni-Session, zum Einordnen, was „normal“ ist:

- **Event-Stream-Reconnects in 7 Tagen: null.** Der Stream steht im
  Normalbetrieb einfach durch. Der Reconnect-Pfad — und damit auch das
  Backoff aus Abschnitt 5 — ist im laufenden Betrieb praktisch toter Code.
  Der Engpass liegt vollständig im *ersten* Verbindungsaufbau.
- 429-Episoden auf `/api/stream/event`: ~alle 2,7 Tage, Dauer **immer ~60 s**.
  Eine Dauersperre wie hier am 21.09. ist dort nie aufgetreten.
- Watchdog-Neustarts (`Will restart lichess-bot`): null — allerdings nur für
  den Zeitraum 20.08.–21.09.2026 belegt, weiter zurück reichen die Logs
  dort nicht. Für die April-Episoden unten ist es nicht feststellbar.
- Ein 429 unmittelbar nach Prozessstart ist auch dort bekannt und folgenlos.
- `move_overhead: 1000` (Bridge) + `MoveOverhead: 100` (Engine, dort ohne
  Leerzeichen im Namen): in 955 Partien aus zehn Tagen (11.–21.09.2026)
  **15 Zeitüberschreitungen, alle beim Gegner**, keine einzige bei Martuni —
  bis hinunter zu 60+0 und 30+0. Einschränkung: dort 2 Kerne mit
  `concurrency: 2`, und es ist ein 10-Tage-Fenster, nicht die Gesamthistorie.
- Martunis Unit setzt kein `KillSignal`, schickt also SIGTERM, obwohl im Bot
  nur SIGINT verdrahtet ist — der Prozess geht dort hart runter. Deshalb hier
  `KillSignal=SIGINT`. Achtung: ein **zweites** SIGINT beendet sofort
  („Received second SIGINT. Quitting now.“), relevant nur bei manuellem
  Nachhelfen.
