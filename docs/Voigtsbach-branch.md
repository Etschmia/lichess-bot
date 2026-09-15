# Voigtsbach-Branch

Dieser Branch existiert, um den zweiten Lichess-Bot-Account **Voigtsbach**
(Engine: `sparkengine`, aktuell in Entwicklung unter `../sparkengine`) auf
einem separaten Host (dem "Grok Bot"-Rechner) unabhängig vom Martuni-Host
hier zu betreiben.

Warum ein eigener Host statt Mehrfachnutzung dieses Checkouts: lichess-bot
bindet pro Prozess genau einen Lichess-Account (ein OAuth-Token pro
`config.yml`, einmalig beim Start gelesen). Zwei Prozesse auf derselben
Maschine im selben Arbeitsverzeichnis würden sich außerdem das hartcodierte
Auto-Log-Verzeichnis (`lichess_bot_auto_logs/`, siehe
`lib/lichess_bot.py`) und das hier aktive `pgn_directory: "game_records"`
teilen. Ein komplett separater Host umgeht das sauber, ohne Workarounds.

## ToDos nach dem Auschecken dieses Branches auf dem Grok-Bot-Host

1. Repo/Branch holen:
   ```bash
   git clone -b feature/voigtsbach-bot git@github.com:Etschmia/lichess-bot.git
   # oder in einem vorhandenen Klon:
   git fetch origin && git checkout feature/voigtsbach-bot
   ```

2. Python-Umgebung aufsetzen (Python 3.10+):
   ```bash
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt -r test_bot/test-requirements.txt
   ```

3. `sparkengine` bauen, sobald es spielbereit ist (im Sibling-Repo):
   ```bash
   cargo build --release
   ```
   Binary liegt danach unter `sparkengine/target/release/<name>`.

4. Voigtsbach-Account zum Bot-Account machen und OAuth-Token erzeugen —
   siehe Abschnitt unten. **Vorher prüfen: Wurde auf Voigtsbach schon
   irgendeine Partie gespielt (auch nur eine Testpartie)? Falls ja, muss
   ein neuer, komplett unbespielter Account her — das Upgrade schlägt
   sonst fehl.**

5. `config.yml` ist absichtlich **nicht** versioniert (siehe Commit
   `88e77438` "default config file, remove config file from git index")
   und taucht deshalb in diesem Branch nicht auf. Stattdessen
   `config.yml.default` nach `config.yml` kopieren und anpassen:
   - `token:` → Voigtsbachs frisches OAuth-Token
   - `engine.dir` → Pfad zum `sparkengine`-Binary-Verzeichnis
   - `engine.name` → Binary-Name
   - `protocol:` → je nachdem, was sparkengine spricht (voraussichtlich `uci`)
   - `uci_options`, unterstützte Varianten/Zeitkontrollen: an sparkengines
     tatsächliche Fähigkeiten anpassen (analog dazu, wie `CLAUDE.md` hier
     im Repo im Abschnitt "Engine binding (Martuni)" für Martuni
     dokumentiert ist — für Voigtsbach/sparkengine gehört das in eine
     eigene, host-lokale `CLAUDE.md` oder in diese Datei, sobald bekannt)

6. Account einmalig upgraden (mit der neuen `config.yml`):
   ```bash
   venv/bin/python lichess-bot.py -u
   ```

7. Testlauf, dann Dauerbetrieb:
   ```bash
   venv/bin/python lichess-bot.py -v   # Testlauf, alle Lichess-Kommunikation im Log
   ```
   Danach eine eigene systemd-Unit anlegen, analog zu
   `/etc/systemd/system/lichess-bot.service` auf dem Martuni-Host, aber
   mit eigenem Namen (z. B. `lichess-bot-voigtsbach.service`) und
   `WorkingDirectory` auf diesen Checkout zeigend.

8. Eigene Doku pflegen: Der `CLAUDE.md`-Abschnitt "Engine binding" in
   diesem Repo ist Martuni-spezifisch und beschreibt eine andere Maschine
   — für den Grok-Bot-Host entweder eine eigene `CLAUDE.md` schreiben oder
   diese Datei hier um die sparkengine-Details ergänzen, sobald die Engine
   fertig ist.

## Aus einem normalen Lichess-Account einen Bot-Account machen

(Quelle: [lichess-bot Wiki](https://github.com/lichess-bot-devs/lichess-bot/wiki))

**Wichtige Einschränkung zuerst:** Der Account darf noch **nie** eine
Partie gespielt haben — weder rated noch casual, weder gegen Mensch noch
Bot. Lichess verweigert das Upgrade sonst. Wenn Voigtsbach schon bespielt
wurde, hilft nur ein frischer, komplett unbespielter Account.

1. Eingeloggt als der Account, der Bot werden soll, folgenden Link öffnen
   (der nötige Scope ist darin schon vorausgewählt):
   https://lichess.org/account/oauth/token/create?scopes%5B%5D=bot:play&description=lichess-bot
   Erforderlicher Scope: **"Play games with the bot API"** (`bot:play`).

2. Token erzeugen lassen und **sofort sichern** — Lichess zeigt ihn danach
   nie wieder an.

3. Token in die (nicht versionierte) `config.yml` unter `token:` eintragen
   (alternativ als Umgebungsvariable `LICHESS_BOT_TOKEN`).

4. Einmalig ausführen:
   ```bash
   python lichess-bot.py -u
   ```
   Das schaltet den Account auf Bot um und startet direkt eine
   Lichess-Session; der Bot beginnt sofort, Herausforderungen anzunehmen.

5. **Das Upgrade ist laut Lichess irreversibel** — ein Bot-Account lässt
   sich nicht zurück in einen normalen Account verwandeln. Ab hier gilt
   nur noch der normale Betrieb ("Run lichess-bot").
