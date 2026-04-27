#!/usr/bin/env python3
"""Identifiziert Angstgegner und Leichtegegner anhand der PGN-Historie."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import chess.pgn

ELO_DIFF_THRESHOLD = 80
UNEXPECTED_SHARE = 0.60
MIN_GAMES_PER_PAIRING = 5  # "mehr als 4" nicht-Remis-Partien

ANGST_CSV = Path("AngstGegner.csv")
LEICHT_CSV = Path("LeichteGegner.csv")

ANGST_FIELDS = ["Name", "Anzahl Spiele ohne Remis", "Anzahl Niederlagen", "Zeitpunkt"]
LEICHT_FIELDS = ["Name", "Anzahl Spiele ohne Remis", "Anzahl Siege", "Zeitpunkt"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="game_records", type=Path,
                        help="Verzeichnis mit PGN-Dateien (Default: game_records)")
    parser.add_argument("player", nargs="?", default="Martuni",
                        help="Spielername (Default: Martuni)")
    return parser.parse_args()


def iter_games(directory: Path):
    for pgn_path in sorted(directory.rglob("*.pgn")):
        with pgn_path.open(encoding="utf-8", errors="replace") as fh:
            while True:
                try:
                    game = chess.pgn.read_game(fh)
                except (ValueError, UnicodeDecodeError):
                    break
                if game is None:
                    break
                yield game, pgn_path


def collect_pairings(directory: Path, player: str) -> dict[str, list[dict[str, Any]]]:
    pairings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game, _ in iter_games(directory):
        white = game.headers.get("White", "")
        black = game.headers.get("Black", "")
        if player not in (white, black):
            continue
        result = game.headers.get("Result", "")
        if result not in ("1-0", "0-1"):
            continue
        try:
            white_elo = int(game.headers.get("WhiteElo", ""))
            black_elo = int(game.headers.get("BlackElo", ""))
        except ValueError:
            continue

        player_is_white = white == player
        opponent = black if player_is_white else white
        player_elo = white_elo if player_is_white else black_elo
        opp_elo = black_elo if player_is_white else white_elo
        player_won = (player_is_white and result == "1-0") or \
                     (not player_is_white and result == "0-1")

        pairings[opponent].append({
            "date": game.headers.get("UTCDate", "") + game.headers.get("UTCTime", ""),
            "player_elo": player_elo,
            "opp_elo": opp_elo,
            "elo_diff": abs(player_elo - opp_elo),
            "player_won": player_won,
        })
    return pairings


def load_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["Name"]: row for row in csv.DictReader(fh)}


def save_csv(path: Path, fields: list[str], rows: dict[str, dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for name in sorted(rows):
            writer.writerow(rows[name])


def main() -> int:
    args = parse_args()
    if not args.directory.is_dir():
        print(f"Verzeichnis nicht gefunden: {args.directory}", file=sys.stderr)
        return 1

    pairings = collect_pairings(args.directory, args.player)
    timestamp = datetime.now().isoformat(timespec="seconds")

    angst_new: dict[str, dict[str, str]] = {}
    leicht_new: dict[str, dict[str, str]] = {}
    qualifying = 0

    for opponent, games in pairings.items():
        if len(games) < MIN_GAMES_PER_PAIRING:
            continue
        games.sort(key=lambda g: g["date"])
        # Elo-Differenz nur einmal prüfen: erste (älteste) Partie der Paarung.
        if games[0]["elo_diff"] <= ELO_DIFF_THRESHOLD:
            continue

        qualifying += 1
        total = len(games)
        wins = sum(1 for g in games if g["player_won"])
        losses = total - wins
        unexpected_losses = sum(
            1 for g in games if not g["player_won"] and g["player_elo"] > g["opp_elo"]
        )
        unexpected_wins = sum(
            1 for g in games if g["player_won"] and g["player_elo"] < g["opp_elo"]
        )
        unexpected = unexpected_losses + unexpected_wins
        if unexpected / total <= UNEXPECTED_SHARE:
            continue

        if unexpected_losses > unexpected_wins:
            angst_new[opponent] = {
                "Name": opponent,
                "Anzahl Spiele ohne Remis": str(total),
                "Anzahl Niederlagen": str(losses),
                "Zeitpunkt": timestamp,
            }
        elif unexpected_wins > unexpected_losses:
            leicht_new[opponent] = {
                "Name": opponent,
                "Anzahl Spiele ohne Remis": str(total),
                "Anzahl Siege": str(wins),
                "Zeitpunkt": timestamp,
            }

    print(f"Paarungen gesamt: {len(pairings)}, auswertbar (>{MIN_GAMES_PER_PAIRING - 1} "
          f"nicht-Remis-Partien, Elo-Diff > {ELO_DIFF_THRESHOLD}): {qualifying}")

    if qualifying == 0:
        print("zuwenig auswertbares Material")
        return 0

    if not angst_new and not leicht_new:
        print("Material vorhanden, keine Angstgegner ermittelt")
        return 0

    angst_rows = load_csv(ANGST_CSV)
    leicht_rows = load_csv(LEICHT_CSV)

    # Wechselt ein Gegner die Kategorie, aus der anderen Liste entfernen.
    for name in angst_new:
        leicht_rows.pop(name, None)
    for name in leicht_new:
        angst_rows.pop(name, None)

    angst_rows.update(angst_new)
    leicht_rows.update(leicht_new)

    if angst_rows:
        save_csv(ANGST_CSV, ANGST_FIELDS, angst_rows)
        print(f"AngstGegner aktualisiert/neu: {len(angst_new)} (gesamt: {len(angst_rows)})")
    else:
        print("Material vorhanden, keine Angstgegner ermittelt")

    if leicht_rows:
        save_csv(LEICHT_CSV, LEICHT_FIELDS, leicht_rows)
        print(f"LeichteGegner aktualisiert/neu: {len(leicht_new)} (gesamt: {len(leicht_rows)})")
    else:
        print("Material vorhanden, keine Leichtegegner ermittelt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
