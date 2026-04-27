#!/usr/bin/env python3
"""Manual challenge runner: one-off, sends challenges sequentially.

Iterates through the time controls 3+0, 5+0, 10+5, 15+10. Sends one challenge
at a time and waits for the resulting game to finish (or the challenge to be
declined / time out) before starting the next time control.

Reuses helpers from challenge_cron.py.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import challenge_cron as cc

# Fixed sequence of time controls to play through.
TC_SEQUENCE = [
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),   
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),   
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),        
    (300, 0, "5+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),
    (180, 0, "3+0"),
    (300, 0, "5+0"),
]

# How long we are willing to wait for a single challenge to be accepted
# before cancelling it and moving to the next bot. One-off script, so give
# each challenge a generous window.
CHALLENGE_WAIT_SECONDS = 15 * 60
MAX_ATTEMPTS_PER_TC = 10

# Pause between finishing one TC round and starting the next challenge.
INTER_TC_DELAY = 30

# ---------------------------------------------------------------------------
# Separate log + state files so we don't collide with the cron variant
# ---------------------------------------------------------------------------
LOG_FILE = cc.SCRIPT_DIR / "lichess_bot_auto_logs" / "challenge_manual.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("challenge_manual")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)

# Redirect challenge_cron's logger to ours so helper calls show up too.
cc.log = log

# Override the cron's challenge-wait so wait_for_challenge uses our window.
cc.CHALLENGE_WAIT = CHALLENGE_WAIT_SECONDS

# Redirect state file so we don't overwrite the real cron's rapid state.
cc.STATE_FILE = cc.SCRIPT_DIR / "challenge_manual_state.json"


def wait_until_idle(my_username: str) -> None:
    """Block until the bot has at most one ongoing game."""
    while True:
        ongoing = cc.get_my_ongoing_games()
        if len(ongoing) <= 1:
            return
        log.info("Bot currently has %d ongoing game(s) — waiting until at most 1 remains.", len(ongoing))
        time.sleep(cc.POLL_INTERVAL)


def run_one_tc(clock_limit: int, clock_inc: int, tc_label: str, my_username: str) -> bool:
    """Send challenges until one bot accepts and the game finishes.

    Returns True on success (game played through), False if we exhausted
    MAX_ATTEMPTS_PER_TC without anybody accepting.
    """
    log.info("-" * 60)
    log.info("Time control: %s", tc_label)

    wait_until_idle(my_username)

    tried: set[str] = set()
    for attempt in range(1, MAX_ATTEMPTS_PER_TC + 1):
        opponent_info = cc.choose_opponent(my_username, exclude=tried)
        if opponent_info is None:
            log.warning("[%s] No more eligible bots to challenge.", tc_label)
            return False

        opponent = opponent_info["username"]
        tried.add(opponent.lower())
        log.info("[%s] Attempt %d: challenging %s — waiting %ds first",
                 tc_label, attempt, opponent, cc.PRE_CHALLENGE_DELAY)
        time.sleep(cc.PRE_CHALLENGE_DELAY)
        resp = cc.create_challenge(opponent, clock_limit, clock_inc)

        decline_key = resp.get("declineReasonKey", "")
        if decline_key:
            log.info("[%s] %s immediately declined: %s (%s)",
                     tc_label, opponent, resp.get("declineReason", ""), decline_key)
            if decline_key.lower() == "nobot":
                cc.record("no_bot_challenges", opponent, tc_label)
            else:
                cc.record("no_response", opponent, tc_label)
            continue

        challenge_data = resp.get("challenge", resp)
        if "id" not in challenge_data:
            log.error("[%s] Challenge creation failed: %s", tc_label, resp.get("error", str(resp)))
            continue

        challenge_id = challenge_data["id"]
        log.info("[%s] Challenge created vs %s (id=%s)", tc_label, opponent, challenge_id)

        # wait_for_challenge blocks until the game finishes (on accept) or
        # returns "declined"/"timeout". On accept it calls monitor_game which
        # writes state "done" and returns normally.
        result = cc.wait_for_challenge(challenge_id, opponent, tc_label, my_username)
        if result == "accepted":
            return True
        # else: try next bot
    log.warning("[%s] Exhausted %d attempts — no bot accepted.", tc_label, MAX_ATTEMPTS_PER_TC)
    return False


def main() -> None:
    log.info("=" * 60)
    log.info("Manual challenge runner started (PID %d) at %s",
             os.getpid(), datetime.now(timezone.utc).isoformat())
    log.info("Sequence: %s", ", ".join(tc[2] for tc in TC_SEQUENCE))

    my_username = cc.get_my_username()
    log.info("Bot account: %s", my_username)

    for i, (clock_limit, clock_inc, tc_label) in enumerate(TC_SEQUENCE):
        if i > 0:
            log.info("Waiting %ds before next challenge...", INTER_TC_DELAY)
            time.sleep(INTER_TC_DELAY)
        try:
            run_one_tc(clock_limit, clock_inc, tc_label, my_username)
        except cc.RateLimited as e:
            log.warning("Rate-limited by Lichess — aborting entire run: %s", e)
            break
        except SystemExit:
            # wait_for_challenge/monitor_game call sys.exit in some paths
            # (legacy from cron). Swallow it so we continue the sequence.
            log.info("[%s] helper called sys.exit — continuing to next TC.", tc_label)
            continue

    log.info("All time controls done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(130)
    except Exception:
        log.exception("Unhandled exception in manual challenge runner")
        sys.exit(1)
