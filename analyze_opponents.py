#!/usr/bin/env python3
"""
analyze_opponents.py — Wertet Martuni-Partien aus den PGN-Verzeichnissen aus.

Verzeichnisse:
  game_archiv/   — ältere abgeschlossene Partien
  game_records/  — neuere Partien (laufende Saison)

Dateiformat: "WeißerSpieler vs SchwarzSpieler - GameID.pgn"
PGN-Header:  [WhiteTitle "BOT"] / [BlackTitle "BOT"] kennzeichnet Bot-Accounts.
"""

import re
import glob
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).parent
PGN_DIRS = [
    REPO_ROOT / "game_archiv",
    REPO_ROOT / "game_records",
]
MARTUNI = "Martuni"


def load_games() -> list[dict]:
    """Liest alle PGN-Dateien und gibt eine Liste von Spiel-Dicts zurück.

    Jedes Dict enthält: white, black, white_title, black_title, file.
    """
    games = []
    for pgn_dir in PGN_DIRS:
        for pgn_file in sorted(pgn_dir.glob("*.pgn")):
            with open(pgn_file, encoding="utf-8", errors="replace") as f:
                content = f.read()

            def header(tag: str) -> str:
                m = re.search(rf'\[{tag} "(.+?)"\]', content)
                return m.group(1) if m else ""

            games.append({
                "white":       header("White"),
                "black":       header("Black"),
                "white_title": header("WhiteTitle"),
                "black_title": header("BlackTitle"),
                "file":        pgn_file.name,
            })
    return games


def opponent_info(games: list[dict]) -> dict[str, dict]:
    """Gibt je Gegnername zurück: Anzahl Partien und ob Bot-Account."""
    info: dict[str, dict] = defaultdict(lambda: {"games": 0, "is_bot": False})

    for g in games:
        for color, title_key in [("white", "white_title"), ("black", "black_title")]:
            name = g[color]
            if name and name != MARTUNI:
                info[name]["games"] += 1
                if g[title_key] == "BOT":
                    info[name]["is_bot"] = True

    return dict(info)


def main() -> None:
    games = load_games()
    opponents = opponent_info(games)

    bots     = {n: d for n, d in opponents.items() if d["is_bot"]}
    non_bots = {n: d for n, d in opponents.items() if not d["is_bot"]}

    print(f"Partien gesamt:          {len(games)}")
    print(f"Verschiedene Gegner:     {len(opponents)}")
    print(f"  davon BOT-Accounts:    {len(bots)}")
    print(f"  davon keine Bots:      {len(non_bots)}")

    if non_bots:
        print("\nNicht-Bot-Gegner:")
        for name, d in sorted(non_bots.items()):
            print(f"  {name}  ({d['games']} Partie{'n' if d['games'] != 1 else ''})")

    print("\nAlle Gegner (alphabetisch, Partienanzahl):")
    for name, d in sorted(opponents.items()):
        tag = "[BOT]" if d["is_bot"] else "[Mensch]"
        print(f"  {tag:9} {name}  ({d['games']}x)")


if __name__ == "__main__":
    main()
