# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Upstream `lichess-bot-devs/lichess-bot` — a Python bridge between the Lichess Bot API and a local chess engine. This checkout is operated as a **Martuni host**: `config.yml` is customized to launch the sibling project at `/home/librechat/enginemartuni` (Rust, UCI). Upstream defaults live in `config.yml.default` — diff against it before changing `config.yml`, and mirror upstream changes into Martuni-specific overrides rather than reverting them.

## Engine binding (Martuni)

- Engine binary: `/home/librechat/enginemartuni/target/release/martuni` (built in the sibling Rust crate with `cargo build --release`). `engine.dir` + `engine.name` in `config.yml` point here.
- Protocol: `uci`. `ponder: true` — Martuni implements real pondering (open deadline on `go ponder`, TT-based pondermove).
- `uci_options`: Martuni only supports `Hash`, `MoveOverhead`, `Ponder`. Do **not** add `Threads`, `SyzygyPath`, or `UCI_ShowWDL` — unimplemented, will warn at startup. `MoveOverhead` (no space in the name) is Martuni-internal; `move_overhead` at the top level is lichess-bot's separate network buffer and both apply.
- Accepted play: `standard` variant only; `blitz`/`rapid`/`classical`. Bullet and correspondence are intentionally disabled (bullet too tight for current timing; correspondence by choice).
- When Martuni gains or loses a UCI option / variant / time control, update `config.yml` here in the same change — the two repos are co-maintained.

## Runtime (systemd)

The bot runs as the system unit **`lichess-bot.service`** (`/etc/systemd/system/lichess-bot.service`, `User=librechat`, `WorkingDirectory=/home/librechat/lichess-bot`, `ExecStart=venv/bin/python lichess-bot.py`, `Restart=always`). Don't start a second instance by hand while debugging — stop the unit first or you will get duplicate Lichess sessions.

**Config changes require a restart.** `config.yml` is read exactly once at startup by `load_config` in `lib/lichess_bot.py`; there is no file watcher and no SIGHUP handler (only SIGINT is wired up). The same applies to `lib/versioning.yml` and to rebuilds of Martuni (`cargo build --release` in the sibling repo) — the engine binary is spawned as a subprocess at bot start, so a fresh build is only picked up on restart.

- Hard restart (interrupts live games): `sudo systemctl restart lichess-bot.service`
- Graceful: set `quit_after_all_games_finish: true` in `config.yml` first, or wait until no games are active, then restart. `Restart=always` brings the process back up automatically if you just let it exit.
- Logs: `journalctl -u lichess-bot.service -f` (plus the repo's own `lichess_bot_auto_logs/`).

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

CI matrix runs Python 3.10 and 3.14 on Linux/macOS/Windows for tests and build; mypy runs on Windows only. Target floor is Python 3.10 (`ruff.toml` pins `target-version = "py310"`).

## Architecture (big picture)

Entry point `lichess-bot.py` is a two-line shim into `lib/lichess_bot.py::start_program`. Everything interesting lives under `lib/`:

- **`lichess_bot.py`** — process orchestrator. Owns the main event loop, the multiprocessing `Pool` of game workers, the control/correspondence/logging/PGN queues, signal handling, auto-restart, and version/Python-deprecation checks driven by `lib/versioning.yml`. Dispatches incoming Lichess stream events to per-game workers.
- **`lichess.py`** — thin HTTP client around the Lichess Bot API with `backoff` retry decorators. All network I/O funnels through here; `stop` is the shared shutdown flag.
- **`model.py`** — dataclasses for `Game`, `Challenge`, `Player`, etc. These wrap the raw JSON event payloads typed in `lichess_types.py` (a `TypedDict` wall between untyped JSON and typed Python).
- **`engine_wrapper.py`** — abstract engine interface plus concrete UCI / XBoard / Homemade adapters. Handles `go` command construction, time management (using `lib/timer.py`), draw/resign policy, polyglot books, lichess-side syzygy, and online move sources (chessdb, lichess cloud eval, opening explorer, online EGTB). This is where config from `engine:` in `config.yml` is consumed.
- **`matchmaking.py`** — proactive challenger: picks opponents when the bot is idle, tracks recent matchups and block lists.
- **`config.py`** — loads `config.yml`, validates it against the shape of `config.yml.default`, and exposes a `Configuration` wrapper that supports dotted access. Unknown keys are errors.
- **`conversation.py`** — chat handling for in-game and spectator rooms; implements the `!help`/`!name`/etc. command protocol.
- **`homemade.py`** (repo root, not `lib/`) — user-extensible Python engines selected via `protocol: homemade`. `extra_game_handlers.py` is the corresponding hook file for custom game-time logic.

Two configuration truths to keep straight: `config.yml` is the **live operator config** (contains the Lichess OAuth token, engine path, and Martuni-specific tweaks — treat it as secret). `config.yml.default` is the upstream template used both as documentation and as the schema `lib/config.py` validates against.

## Tests

`test_bot/` uses pytest. Notable pieces:

- `conftest.py` spins up shared fixtures; engine tests may download external engines into `TEMP/` (cached in CI).
- `lichess.py` and `uci_engine.py` / `xboard_engine.py` / `buggy_engine.py` are **test doubles**, not production code. `test_bot.py` runs end-to-end loops against the fake Lichess server.
- `test_external_moves.py` exercises the online move sources in `engine_wrapper.py` and needs network access (or VCR-style stubs where present).
- Ruff config for tests lives at `test_bot/ruff.toml` and selects `ALL` with a curated ignore list — respect it rather than adding per-file `# noqa` when a rule is intentionally off globally.

## Versioning

`lib/versioning.yml` holds `lichess_bot_version`, `minimum_python_version`, `deprecated_python_version`, and a `deprecation_date`. `.github/workflows/update_version.py` bumps the version automatically (see the "Auto update version" commits). Don't hand-edit the version unless that workflow is also being changed.
