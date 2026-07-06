"""Auto-roll session boundaries after a weekly play session.

Runs at the end of the Sunday session-heartbeat workflow. If today's posting
activity looks like a play session actually happened, it closes the currently
open (end-less) session at the in-game frontier reached today and appends the
next session, open-ended, starting the following in-game day — the same
convention used when boundaries were maintained by hand.

Detection is deliberately conservative; ALL of these must hold or the script
is a silent no-op:

  1. Sunday gate   — real-world today (UTC) is a Sunday (unless --force).
                     The heartbeat workflow only runs during the Sunday
                     session window, so this is belt-and-braces.
  2. Burst gate    — at least MIN_BURST_EVENTS events in events.json were
                     POSTED today (by Discord snowflake timestamp). Real
                     sessions produce 47-94; the busiest non-session day on
                     record produced 31, and quiet Sundays produce ~0.
  3. Progress gate — the max in-game date among today's posts exceeds the
                     open session's start by MIN_PROGRESS_DAYS. Sessions
                     advance the clock 15-23 years; between-session RP only
                     drifts days. This also makes the script idempotent:
                     immediately after a rollover the new open session starts
                     past the frontier, so a re-run the same evening fails
                     this gate and does nothing.

The chosen end date ignores in-game dates more than MAX_JUMP_YEARS past the
open session's start, so a single century-typo post (e.g. 1559 meant as 1359
— it has happened) cannot corrupt a boundary.

The end date is an approximation of the save date (the true end is whatever
the end-of-session map export says); nudge it in sessions.html if it matters.

Usage: python tools/roll_session.py --campaign darthsunday
       --now 2026-06-28T23:05:00+00:00   (testing: pretend it's this instant)
       --force                            (testing: skip the Sunday gate)
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DISCORD_EPOCH = 1420070400000

MIN_BURST_EVENTS  = 25
MIN_PROGRESS_DAYS = 365
MAX_JUMP_YEARS    = 30


def log(msg):
    print(msg, flush=True)


def snowflake_to_dt(snowflake):
    ms = (int(snowflake) >> 22) + DISCORD_EPOCH
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_game_date(s):
    """ISO in-game date -> datetime.date, or None. events.json dates are
    already normalised to ISO by the sync pipeline."""
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def write_had_changes(changed):
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if changed else 'false'}\n")


def main():
    ap = argparse.ArgumentParser(description="Auto-roll session boundaries")
    ap.add_argument("--campaign", required=True,
                    help="campaign folder name, e.g. darthsunday")
    ap.add_argument("--now", default="",
                    help="testing: ISO instant to treat as 'now' (UTC assumed)")
    ap.add_argument("--force", action="store_true",
                    help="testing: skip the Sunday gate")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / args.campaign / "data"
    sessions_path = data_dir / "sessions.json"
    events_path   = data_dir / "events.json"

    if args.now:
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    # ── Gate 1: Sunday ────────────────────────────────────────────────────────
    if now.weekday() != 6 and not args.force:
        log(f"Not a Sunday ({now:%A} {now:%Y-%m-%d} UTC) — no-op.")
        write_had_changes(False)
        return

    sessions_data = json.loads(sessions_path.read_text(encoding="utf-8"))
    sessions = sessions_data.get("sessions", [])
    if not sessions:
        log("sessions.json has no sessions — no-op.")
        write_had_changes(False)
        return

    open_session = sessions[-1]
    if open_session.get("end"):
        log(f"Last session ({open_session.get('name')!r}) is already closed — no-op.")
        write_had_changes(False)
        return

    m = re.fullmatch(r"Session (\d+)", open_session.get("name", ""))
    if not m:
        log(f"Open session name {open_session.get('name')!r} doesn't match "
            f"'Session N' — refusing to auto-name a successor; no-op.")
        write_had_changes(False)
        return
    next_name = f"Session {int(m[1]) + 1}"

    open_start = parse_game_date(open_session.get("start", ""))
    if open_start is None:
        log(f"Open session start {open_session.get('start')!r} unparseable — no-op.")
        write_had_changes(False)
        return

    # ── Today's posting burst (by snowflake time, UTC midnight -> now) ───────
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = json.loads(events_path.read_text(encoding="utf-8")).get("events", [])

    burst = 0
    frontier = None          # max sane in-game date posted today
    try:
        clamp = date(open_start.year + MAX_JUMP_YEARS, open_start.month, open_start.day)
    except ValueError:  # Feb 29 start, non-leap target year
        clamp = date(open_start.year + MAX_JUMP_YEARS, 3, 1)
    for ev in events:
        try:
            posted = snowflake_to_dt(ev.get("id"))
        except (TypeError, ValueError):
            continue
        if not (window_start <= posted <= now):
            continue
        burst += 1
        d = parse_game_date(ev.get("date"))
        if d is None or d > clamp:
            continue  # unparseable or a typo-jump — never let it set the boundary
        if frontier is None or d > frontier:
            frontier = d

    # ── Gate 2: burst size ────────────────────────────────────────────────────
    if burst < MIN_BURST_EVENTS:
        log(f"Only {burst} event(s) posted today (need {MIN_BURST_EVENTS}) — "
            f"doesn't look like a session; no-op.")
        write_had_changes(False)
        return

    # ── Gate 3: in-game progression ───────────────────────────────────────────
    threshold = open_start + timedelta(days=MIN_PROGRESS_DAYS)
    if frontier is None or frontier < threshold:
        log(f"In-game frontier today is {frontier} (need >= {threshold}, i.e. "
            f"{MIN_PROGRESS_DAYS}d past open-session start {open_start}) — "
            f"doesn't look like a session; no-op.")
        write_had_changes(False)
        return

    # ── Roll over ─────────────────────────────────────────────────────────────
    open_session["end"] = frontier.isoformat()
    sessions.append({
        "name":  next_name,
        "start": (frontier + timedelta(days=1)).isoformat(),
    })
    sessions_path.write_text(
        json.dumps(sessions_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"Session rolled: closed {open_session['name']!r} at {open_session['end']} "
        f"(burst={burst}), opened {next_name!r} from {sessions[-1]['start']}.")
    write_had_changes(True)


if __name__ == "__main__":
    main()
