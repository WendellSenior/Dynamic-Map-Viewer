#!/usr/bin/env python3
"""Idempotent: add the 🗺️ (:map:) reaction to every event in events.json,
confirming in-channel that the post was parsed and plotted on the map.

The normal sync (sync_events.py) reacts to NEW events as it ingests them.
This script handles the existing BACKLOG — the events that predate the
reaction feature.

Rate limits: Discord's reaction-add route is throttled tightly (~1 request
per 250ms per channel), so we pace requests at REACTION_PACE_SECONDS and lean
on the 429 handling in sync_events.add_reaction as a backstop. Already-reacted
message ids are tracked in processed_ids.json (reacted_ids) — shared with the
normal sync — so re-runs skip them. Reactions are also idempotent on Discord's
side (re-adding is a 204 no-op), so even a reacted_ids loss only costs wasted
API calls, never duplicate reactions.

Progress is flushed to processed_ids.json periodically so a locally-interrupted
run keeps what it did (the GH workflow only commits after the step completes,
so cross-run resumption there relies on the reactions themselves + idempotency).

Usage:
    DISCORD_TOKEN=... python tools/react_events.py --campaign darthsunday
or via workflow_dispatch with react_events=true.
"""

import argparse
import os
import sys
import time

# Reuse the Discord client + reaction helper from sync_events.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_events as se

# Seconds between reaction PUTs. Discord's reaction routes allow ~1 per 250ms
# per channel; 0.3s keeps us comfortably under without making the sweep crawl.
REACTION_PACE_SECONDS = 0.3

# Flush reacted_ids to disk every N reactions so a local Ctrl-C keeps progress.
FLUSH_EVERY = 25


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campaign", required=True,
                    help="Campaign folder name (matched against campaigns.json).")
    args = ap.parse_args()

    cfg = se.load_campaign_config(args.campaign)
    se.CHANNEL_ID = cfg["channel_id"]
    events_file   = f"{cfg['folder']}/data/events.json"
    cache_file    = f"{cfg['folder']}/data/processed_ids.json"

    events_data = se.load_json(events_file, {"events": []})
    events = events_data.get("events", [])
    cache = se.load_json(cache_file, {})
    reacted = set(cache.get("reacted_ids", []))

    missing_chan = sum(1 for e in events if not e.get("channel_id"))
    todo = [e for e in events if e.get("channel_id") and e["id"] not in reacted]

    se.log(f"=== React events: campaign={cfg['folder']} ===")
    se.log(f"  {len(todo)} to react "
           f"(of {len(events)} total; {len(reacted)} already done, "
           f"{missing_chan} missing channel_id — run backfill_channel_ids first "
           f"if that's nonzero).")

    if not todo:
        _write_output(False)
        return

    def _flush():
        cache["reacted_ids"] = sorted(reacted)
        se.save_json(cache_file, cache)

    succeeded = failed = 0
    for i, ev in enumerate(todo, 1):
        if se.add_reaction(ev["channel_id"], ev["id"]):
            reacted.add(ev["id"])
            succeeded += 1
        else:
            failed += 1
        if i % FLUSH_EVERY == 0 or i == len(todo):
            se.log(f"  [{i}/{len(todo)}] reacted={succeeded} failed={failed}")
            _flush()
        time.sleep(REACTION_PACE_SECONDS)

    _flush()
    se.log(f"Done: {succeeded} reacted, {failed} failed, {len(reacted)} total in cache.")
    _write_output(succeeded > 0)


def _write_output(had_changes):
    """Set the had_changes step output for the GH Action's commit gate."""
    out = os.environ.get("GITHUB_OUTPUT", "")
    if out:
        with open(out, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")


if __name__ == "__main__":
    main()
