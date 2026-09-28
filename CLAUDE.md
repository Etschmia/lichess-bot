# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Upstream `lichess-bot-devs/lichess-bot` — a Python bridge between the Lichess Bot API and a local chess engine. This checkout runs the Lichess account **Voigtsbach** with the engine **Funken** from the sibling repo `../sparkengine` (Rust, UCI). Upstream defaults live in `config.yml.default` — diff against it before changing `config.yml`, and mirror upstream changes into Voigtsbach-specific overrides rather than reverting them. Operational details (watchdog, 429 incidents, PGN archive, analysis timer) are in `BETRIEB-voigtsbach.md`.

Two configuration truths to keep straight: `config.yml` is the **live operator config** (engine path and Voigtsbach-specific tweaks; not versioned — `.gitignore` excludes `*.yml`; its template is `../sparkengine/lichess/config.yml.example`). The Lichess OAuth token is **not** in `config.yml` but in `token.env` (0600, gitignored), loaded by the systemd unit via `EnvironmentFile`. `config.yml.default` is the upstream template used both as documentation and as the schema `lib/config.py` validates against.

## Engine binding (Funken)

- Engine binary: `/var/www/but2/botdir/sparkengine/target/release/funken` (built in the sibling Rust crate with `cargo build --release`). `engine.dir` + `engine.name` in `config.yml` point here.
- Protocol: `uci`. `ponder: false` — Funken does not support pondering.
- `uci_options`: `Move Overhead` (with space), `Threads: 1` (Funken is single-threaded, max 1), `Hash`. Only list options Funken actually exposes. `move_overhead` at the top level is lichess-bot's separate network buffer; both apply.
- No outside move sources: polyglot book, online moves (chessdb, cloud analysis, opening explorer, online EGTB) and local Syzygy/Gaviota tablebases are all disabled — every move comes from Funken's own search. Bridge-side resign/draw offers are disabled too.
- Accepted play: `standard` only; bullet, blitz, rapid, and classical; `concurrency: 1`. Correspondence is intentionally disabled.
- When Funken gains or loses a UCI option / variant / time control, update `config.yml` here in the same change — the two repos are co-maintained.

## Runtime (systemd)

The bot runs as the system unit **`lichess-bot-voigtsbach.service`** (`/etc/systemd/system/lichess-bot-voigtsbach.service`, `User=but2developer`, `WorkingDirectory=/var/www/but2/botdir/lichess-bot`, `ExecStart=venv/bin/python lichess-bot.py --config config.yml`, `Restart=always`, `KillSignal=SIGINT`, `TimeoutStopSec=300`, `ProtectSystem=strict` with only this directory writable). Don't start a second instance by hand while debugging — stop the unit first or you will get duplicate Lichess sessions.

**Config changes require a restart.** `config.yml` is read exactly once at startup by `load_config` in `lib/lichess_bot.py`; there is no file watcher and no SIGHUP handler (only SIGINT is wired up). The same applies to `lib/versioning.yml`.

**Engine rebuilds do not.** The engine is spawned per game (`engine_wrapper.create_engine(config, game)` in `play_game`, `lib/lichess_bot.py`), so a fresh `cargo build --release` in `../sparkengine` takes effect from the next game on; the running game keeps the old binary. To check which build a game used, compare the mtime of `target/release/funken` with the game's start time.

- Graceful restart: wait until `GET /api/account/playing` returns an empty `nowPlaying`, then `sudo systemctl restart lichess-bot-voigtsbach.service`. SIGINT plus `quit_after_all_games_finish: true` lets a running game finish, but systemd sends SIGKILL after 300 s.
- Last start: `systemctl show lichess-bot-voigtsbach.service -p ActiveEnterTimestamp -p NRestarts`
- Logs: `journalctl -u lichess-bot-voigtsbach.service -f` (plus the repo's own `lichess_bot_auto_logs/`).

## Common commands

Run from the repo root, inside the existing `venv/` (or after `pip install -r requirements.txt -r test_bot/test-requirements.txt`).

```bash
# Run the bot against Lichess (uses config.yml)
python lichess-bot.py
python lichess-bot.py -v                      # verbose: log all Lichess traffic
python lichess-bot.py --config other.yml
python lichess-bot.py -u                      # one-time: upgrade account to BOT

# Full test suite (matches CI)
pytest --log-cli-level=10
pytest test_bot/test_bot.py                   # single file
pytest test_bot/test_bot.py::test_name        # single test
# Engine-integration tests download Stockfish/Fairy-Stockfish into TEMP/ on first run.

# Lint (CI uses this exact invocation)
ruff check --config test_bot/ruff.toml

# Type check (CI runs --strict; keep it clean)
mypy --strict .
```

## Tests

`test_bot/` uses pytest. Notable pieces:

- `conftest.py` spins up shared fixtures; engine tests may download external engines into `TEMP/` (cached in CI).
- `lichess.py` and `uci_engine.py` / `xboard_engine.py` / `buggy_engine.py` are **test doubles**, not production code. `test_bot.py` runs end-to-end loops against the fake Lichess server.
- `test_external_moves.py` exercises the online move sources in `engine_wrapper.py` and needs network access (or VCR-style stubs where present).
- Ruff config for tests lives at `test_bot/ruff.toml` and selects `ALL` with a curated ignore list — respect it rather than adding per-file `# noqa` when a rule is intentionally off globally.

## Versioning

`lib/versioning.yml` holds `lichess_bot_version`, `minimum_python_version`, `deprecated_python_version`, and a `deprecation_date`. `.github/workflows/update_version.py` bumps the version automatically (see the "Auto update version" commits). Don't hand-edit the version unless that workflow is also being changed.
