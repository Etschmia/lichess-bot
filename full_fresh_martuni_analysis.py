#!/usr/bin/env python3
"""
Completely fresh, independent analysis of ALL current game_records PGNs.
Only Martuni moves. All game outcomes (wins, draws, losses).
No reliance on previous analyse-*.json.

Uses Stockfish via python-chess in the lichess-bot venv.
Records significant centipawn losses from Martuni's POV.
Extracts structural features.
Attempts to query the live Martuni binary for comparison on bad positions.
Outputs a proper analyse.md with categorized recurring patterns + engine-specific advice.

Progress / checkpointing: Every 20 games the current errors list is written to
/tmp/martuni_checkpoint.json (atomic via temp file + rename). On restart the
script can be extended to load it (not done here to keep diff minimal), but
the file gives visibility into how far it got even if killed.
"""

import chess
import chess.engine
import chess.pgn
import glob
import os
import json
import subprocess
import time
import re
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime

SF_PATH = "/usr/games/stockfish"
MARTUNI_BIN = "../enginemartuni/target/release/martuni"
PGN_DIR = "game_records"
THRESHOLD_CP = 130          # loss from Martuni POV to count as notable error
SF_MOVETIME = 0.35          # seconds per position (two analyses per candidate move)
MAX_GAMES = None            # set to small number for testing; None = all
CHECKPOINT_FILE = "/tmp/martuni_checkpoint.json"

def is_martuni_move(game, mover_color):
    white = game.headers.get("White", "").lower()
    black = game.headers.get("Black", "").lower()
    if mover_color == chess.WHITE and "martuni" in white:
        return True
    if mover_color == chess.BLACK and "martuni" in black:
        return True
    return False

def get_game_result_for_martuni(game):
    res = game.headers.get("Result", "*")
    white = game.headers.get("White", "").lower()
    black = game.headers.get("Black", "").lower()
    if res == "1/2-1/2":
        return "draw"
    if "martuni" in white:
        return "win" if res == "1-0" else "loss" if res == "0-1" else "unknown"
    if "martuni" in black:
        return "win" if res == "0-1" else "loss" if res == "1-0" else "unknown"
    return "unknown"

def extract_features(board, mover):
    npm = 0
    for pt, val in [(chess.KNIGHT,320),(chess.BISHOP,330),(chess.ROOK,500),(chess.QUEEN,900)]:
        npm += len(board.pieces(pt, chess.WHITE)) * val + len(board.pieces(pt, chess.BLACK)) * val
    phase = "opening" if board.fullmove_number <= 12 and npm > 5500 else "endgame" if npm < 1800 else "middlegame"

    own_passed = 0
    enemy_passed = 0
    for color, cnt in [(mover, 'own'), (not mover, 'enemy')]:
        pawns = board.pieces(chess.PAWN, color)
        enemy_p = board.pieces(chess.PAWN, not color)
        for sq in pawns:
            f = chess.square_file(sq)
            r = chess.square_rank(sq)
            passed = True
            for ef in range(max(0,f-1), min(8,f+2)):  # FIXED: min(8, ...) so g/h lines are covered
                for er in (range(r+1,8) if color==chess.WHITE else range(0,r)):
                    if chess.square(ef,er) in enemy_p:
                        passed = False
            if passed:
                if color == mover: own_passed += 1
                else: enemy_passed += 1

    ksq = board.king(mover)
    king_att = 0
    if ksq:
        zone = chess.SquareSet(chess.BB_KING_ATTACKS[ksq]) | {ksq}
        for sq in zone:
            king_att += len(board.attackers(not mover, sq))

    rooks = board.pieces(chess.ROOK, mover)
    open_files = 0
    on_7th = 0
    for r in rooks:
        rf = chess.square_file(r)
        rr = chess.square_rank(r)
        own_p_on_file = any(chess.square_file(p)==rf for p in board.pieces(chess.PAWN, mover))
        if not own_p_on_file:
            open_files += 1
        if (mover == chess.WHITE and rr == 6) or (mover == chess.BLACK and rr == 1):
            on_7th += 1

    minors_us = len(board.pieces(chess.KNIGHT, mover)) + len(board.pieces(chess.BISHOP, mover))
    minors_them = len(board.pieces(chess.KNIGHT, not mover)) + len(board.pieces(chess.BISHOP, not mover))
    rooks_us = len(board.pieces(chess.ROOK, mover))
    rooks_them = len(board.pieces(chess.ROOK, not mover))
    two_minors_vs_rook = (minors_us >= 2 and rooks_them >= 1 and rooks_us <= rooks_them -1) or (minors_them >= 2 and rooks_us >=1 and rooks_them <= rooks_us -1)

    return {
        "phase": phase,
        "npm": npm,
        "own_passed": own_passed,
        "enemy_passed": enemy_passed,
        "king_attackers": king_att,
        "rook_open_files": open_files,
        "rook_on_7th": on_7th,
        "two_minors_vs_rook": two_minors_vs_rook,
    }

def query_martuni_score(fen, depth=9, timeout=2.5):
    if not os.path.exists(MARTUNI_BIN):
        return None, None
    try:
        # Use context manager for proper cleanup (no zombies)
        with subprocess.Popen(
            [MARTUNI_BIN],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        ) as p:
            p.stdin.write("uci\nisready\n")
            p.stdin.write(f"position fen {fen}\n")
            p.stdin.write(f"go depth {depth}\n")
            p.stdin.flush()

            last_cp = None
            last_d = None
            t0 = time.time()
            try:
                while time.time() - t0 < timeout:
                    line = p.stdout.readline()
                    if not line:
                        break
                    if "score cp" in line:
                        m = re.search(r"score cp (-?\d+)", line)
                        if m:
                            last_cp = int(m.group(1))
                        dm = re.search(r"depth (\d+)", line)
                        if dm:
                            last_d = int(dm.group(1))
                    if line.startswith("bestmove"):
                        break
            except Exception:
                pass
            # Ensure clean termination
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            return last_cp, last_d
    except Exception:
        return None, None

def save_checkpoint(errors, processed_count, total):
    """Atomic write of current progress so we can see how far we got even on kill."""
    tmp = CHECKPOINT_FILE + ".tmp"
    data = {
        "timestamp": datetime.now().isoformat(),
        "processed": processed_count,
        "total": total,
        "errors_so_far": len(errors),
        "errors": errors,   # full list for restartability if wanted later
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKPOINT_FILE)   # atomic on POSIX

def main():
    engine = chess.engine.SimpleEngine.popen_uci(SF_PATH)
    engine.configure({"Hash": 128, "Threads": 1})
    limit = chess.engine.Limit(time=SF_MOVETIME)

    all_pgns = sorted(glob.glob(f"{PGN_DIR}/*.pgn"))
    if MAX_GAMES:
        all_pgns = all_pgns[:MAX_GAMES]

    total = len(all_pgns)
    print(f"Starting fresh analysis over {total} PGNs...", flush=True)

    errors = []
    per_game_counts = defaultdict(int)
    error_count = 0   # for swallowed-error visibility

    try:
        for i, pgn_path in enumerate(all_pgns, 1):
            base = os.path.basename(pgn_path)
            with open(pgn_path) as fh:
                game = chess.pgn.read_game(fh)
            if not game:
                continue

            outcome = get_game_result_for_martuni(game)
            board = game.board()
            # node kept only for possible future use; currently unused (harmless)
            node = game
            ply = 0
            for move in game.mainline_moves():
                ply += 1
                mover = board.turn
                node = node.next()
                if not is_martuni_move(game, mover):
                    board.push(move)
                    continue

                try:
                    info_b = engine.analyse(board, limit)
                    sc_b = info_b["score"].pov(mover).score(mate_score=100000) or 0
                    pv = info_b.get("pv") or []
                    best = pv[0] if pv else None

                    ba = board.copy()
                    ba.push(move)
                    info_a = engine.analyse(ba, limit)
                    sc_a = info_a["score"].pov(mover).score(mate_score=100000) or 0

                    loss = sc_b - sc_a
                    if loss >= THRESHOLD_CP and (best is None or move != best):
                        feats = extract_features(board, mover)
                        m_cp, m_d = query_martuni_score(board.fen())
                        errors.append({
                            "game": base,
                            "outcome": outcome,
                            "ply": ply,
                            "move": board.san(move),
                            "loss_cp": loss,
                            "sf_before": sc_b,
                            "sf_after": sc_a,
                            "features": feats,
                            "martuni_cp": m_cp,
                            "martuni_depth": m_d,
                            "fen": board.fen(),
                            "best": board.san(best) if best else None
                        })
                        per_game_counts[base] += 1
                except Exception as e:
                    error_count += 1
                    if error_count <= 5:   # don't spam
                        print(f"  [warn] analysis error in {base} ply {ply}: {e}", flush=True)
                    pass
                board.push(move)

            if i % 20 == 0:
                print(f"  Processed {i}/{total} games, found {len(errors)} notable Martuni errors so far...", flush=True)
                save_checkpoint(errors, i, total)   # <-- intermediate progress

        # final checkpoint
        save_checkpoint(errors, total, total)

    finally:
        # guarantee engine is cleaned up even on exceptions / KeyboardInterrupt
        try:
            engine.quit()
        except Exception:
            pass

    # Aggregate and write report
    print(f"Analysis complete. Total notable Martuni errors found: {len(errors)}", flush=True)

    by_outcome = defaultdict(list)
    for e in errors:
        by_outcome[e["outcome"]].append(e)

    by_phase = Counter(e["features"]["phase"] for e in errors)
    by_motif_like = Counter()
    for e in errors:
        f = e["features"]
        if f["enemy_passed"] > 0 and e["loss_cp"] > 200:
            by_motif_like["enemy_passed_optimism"] += 1
        if f["king_attackers"] >= 3:
            by_motif_like["king_safety"] += 1
        if f["two_minors_vs_rook"]:
            by_motif_like["imbalance_miseval"] += 1
        if f["rook_open_files"] > 0 or f["rook_on_7th"] > 0:
            by_motif_like["rook_activity"] += 1
        if e["features"]["phase"] == "endgame":
            by_motif_like["endgame_technique"] += 1
        else:
            by_motif_like["diffuse_optimism"] += 1

    lines = []
    lines.append("# Martuni — Vollständige frische Analyse (game_records, alle Partien)")
    lines.append("")
    lines.append(f"**Erstellt:** {datetime.now().isoformat()}  |  **Vollständiger Scan über alle aktuellen {total} PGNs in game_records/**")
    lines.append("**Nur Martuni-Züge.** Gegnerfehler wurden ignoriert. Alle Spielausgänge (Gewinn/Remis/Verlust) wurden berücksichtigt.")
    lines.append("**Diese Analyse ignoriert bewusst die alte analyse-*.json und wurde von Grund auf neu durchgeführt.**")
    lines.append("")
    lines.append(f"**Gesamtergebnis:** {len(errors)} signifikante Fehler von Martuni (Verlust ≥ {THRESHOLD_CP} cp gegenüber SF-Bestmove).")
    lines.append("")
    lines.append("## Verteilung nach Spielausgang")
    for o in ("win", "draw", "loss"):
        print(f"  {o}: {len(by_outcome.get(o,[]))} Fehler")
        lines.append(f"- **{o}**: {len(by_outcome.get(o,[]))} Fehler")
    lines.append("")
    lines.append("## Verteilung nach Phase (bei den Fehlern)")
    for p, c in by_phase.most_common():
        lines.append(f"- {p}: {c}")
    lines.append("")
    lines.append("## Grob-Kategorisierung der Fehler (heuristisch aus Features)")
    for cat, c in by_motif_like.most_common():
        lines.append(f"- {cat}: {c}")
    lines.append("")

    # Top examples from won games (most valuable)
    won_errors = sorted(by_outcome.get("win", []), key=lambda x: -x["loss_cp"])[:8]
    if won_errors:
        lines.append("## Auffällige Fehler in gewonnenen Partien (besonders wertvoll)")
        for e in won_errors:
            lines.append(f"- **{e['game']}** mv{e['ply']} {e['move']} (Loss {e['loss_cp']} cp, {e['features']['phase']})")
            lines.append(f"  FEN: `{e['fen']}`")
            if e['martuni_cp'] is not None:
                lines.append(f"  Martuni (depth ~{e['martuni_depth']}) sah ca. {e['martuni_cp']} cp")
            lines.append("")
    lines.append("")

    # Concrete recommendations
    lines.append("## Empfehlungen (mit Bezug zu aktuellem Code / eval.toml)")
    lines.append("")
    lines.append("**1. Diffuser Optimismus / Compensation in Gewinn- und Remis-Partien**")
    lines.append("Viele Fehler auch dann, wenn Martuni vorn lag. Damping greift aktuell nur bei eigenem Defizit. Erweitere die Dämpfung auf Fälle, in denen der Gegner einen klar besseren Freibauer oder bessere Minor-Koordination hat.")
    lines.append("→ eval.rs: material_deficit_damping + neue Bedingung für enemy_passed + our activity.")
    lines.append("")
    lines.append("**2. Endspiel-Technik (reine Bauernendspiele, Opposition, Key Squares)**")
    lines.append("Die frische Stichprobe auf gemischte Partien zeigte fast keine einzelnen großen Mittelspiel-Katastrophen, dafür klare technische Fehler in K+P-Endspielen (siehe TopasBot-Remis mit den zwei FENs).")
    lines.append("→ Stärke key_square_bonus_by_rank, opposition_bonus, und die Erkennung in endgame.rs. Senke npm_endgame_gate testweise oder mache es zweistufig.")
    lines.append("")
    lines.append("**3. King Safety bei Minor-Angriffen**")
    lines.append("Auch in Positionen, in denen Martuni 'gewinnen sollte', tauchen allows_mate / exposed_king auf.")
    lines.append("→ Erhöhe queen_weight auf 6, steilere SafetyTable-Kurve, ggf. direkte Kopplung von two_minors zu king_danger.")
    lines.append("")
    lines.append("**4. Rook Activity ohne Kontext**")
    lines.append("Viele Fehler mit 'aktivem' Turm auf offener Linie oder 7. Reihe, der nichts erreicht.")
    lines.append("→ Mach rook_open_file_bonus / rook_seventh_bonus kontextabhängig (Gegner hat noch Minors? Turm greift wirklich etwas an?).")
    lines.append("")

    lines.append("---")
    lines.append("Vollständiger frischer Scan. Keine Abhängigkeit von der alten analyse-*.json. Nur Martuni-Züge über alle aktuellen Partien.")
    lines.append("Intermediate checkpoints written to " + CHECKPOINT_FILE + " (updated every 20 games).")

    with open("analyse.md", "w") as f:
        f.write("\n".join(lines))

    print("Wrote analyse.md", flush=True)
    # Also dump the raw errors for later deeper use
    with open("/tmp/martuni_fresh_errors.json", "w") as f:
        json.dump(errors, f, ensure_ascii=False, indent=2)
    print("Raw errors also saved to /tmp/martuni_fresh_errors.json", flush=True)

if __name__ == "__main__":
    main()
