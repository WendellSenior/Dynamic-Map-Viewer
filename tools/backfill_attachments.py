#!/usr/bin/env python3
"""One-shot: refetch every event with un-mirrored images, get freshly-signed
Discord CDN URLs, and download the attachments to <campaign>/data/attachments/.

Background: Discord adds a `?ex=<hex_ts>` expiration parameter to every
attachment URL, typically valid ~24h. Once expired, the URL 404s permanently.
The normal sync only refetches messages within the 7-day edit-lookback window,
which means events older than that have permanently-stale URLs in events.json.

This script reverses that for the backlog:
  1. Walk events.json finding entries whose images[] lack a `local` field.
  2. Refetch each via the Discord API — that response carries freshly-signed
     URLs valid for the next ~24h.
  3. Pipe those into sync_events.mirror_event_images() which downloads each
     attachment to <campaign>/data/attachments/<att_id>_<filename> and stamps
     a `local` path on the JSON entry.

Idempotent: events whose images are all already mirrored are skipped on the
candidate-find pass. Failed downloads (404 / network) leave the URL-only
entry in place so a later run can try again.

Usage:
    DISCORD_TOKEN=... python tools/backfill_attachments.py --campaign darthsunday

or via workflow_dispatch with backfill_attachments=true.
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Reuse the Discord client + mirror helpers from sync_events.py rather than
# duplicating the auth/rate-limit machinery.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_events as se


def has_unmirrored_image(event, attachments_dir):
    """True if at least one image on the event lacks a usable local mirror.
    "Usable" = the JSON has a `local` field AND that file actually exists on
    disk (covers the case where someone deleted a file by hand)."""
    for img in (event.get("images") or []):
        local_rel = img.get("local")
        if not local_rel:
            return True
        if not os.path.exists(os.path.join(attachments_dir, local_rel)):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campaign", required=True,
                    help="Campaign folder name (matched against campaigns.json).")
    args = ap.parse_args()

    cfg = se.load_campaign_config(args.campaign)
    se.CHANNEL_ID      = cfg["channel_id"]
    se.ATTACHMENTS_DIR = cfg["folder"]
    events_file        = f"{cfg['folder']}/data/events.json"

    events_data = se.load_json(events_file, {"events": []})
    events = events_data.get("events", [])

    todo = [ev for ev in events
            if has_unmirrored_image(ev, se.ATTACHMENTS_DIR)]
    se.log(f"=== Backfill attachments: campaign={cfg['folder']} ===")
    se.log(f"  {len(todo)} event(s) need mirroring (of {len(events)} total).")

    if not todo:
        # Still surface had_changes=false to the workflow so its commit step skips.
        _write_workflow_output(False)
        return

    mirrored_count   = 0     # events with at least one new local file
    refetch_failures = 0     # messages we couldn't even refetch (deleted / 403)
    skipped_no_chan  = 0     # events with no channel_id (run backfill_channel_ids first)

    for i, ev in enumerate(todo, 1):
        se.log(f"[{i}/{len(todo)}] {ev['id']} ({ev.get('date')}, {ev.get('country')})")
        ch_id = ev.get("channel_id") or ""
        if not ch_id:
            se.log("  ! no channel_id stored — run backfill_channel_ids first")
            skipped_no_chan += 1
            continue

        msg = se.fetch_message(ch_id, ev["id"])
        if msg is None:
            se.log("  ! refetch failed (403 / network)")
            refetch_failures += 1
            continue
        if msg is se._DELETED:
            se.log("  ! source message was deleted on Discord — leaving entry as-is")
            refetch_failures += 1
            continue

        # Replace this event's images[] with the freshly-fetched URLs while
        # preserving any `local` paths we already have for the same attachment
        # (matched by att_id). The freshly-fetched entries carry valid URLs;
        # mirror_event_images then downloads any not-yet-mirrored ones.
        fresh = se._msg_images(msg)
        existing_by_att = {im.get("att_id"): im for im in (ev.get("images") or [])
                           if im.get("att_id")}
        for img in fresh:
            existing = existing_by_att.get(img.get("att_id"))
            if existing and existing.get("local"):
                img["local"] = existing["local"]
        ev["images"] = fresh

        if se.mirror_event_images(ev):
            mirrored_count += 1

        # Tiny pause to be polite about rate limits. fetch_message already
        # handles 429s with retry, but spacing requests avoids triggering
        # them in the first place on big backlogs.
        time.sleep(0.05)

    se.log("")
    se.log(f"Backfill complete: mirrored on {mirrored_count} event(s), "
           f"{refetch_failures} refetch failure(s), {skipped_no_chan} no-channel-id.")

    if mirrored_count:
        se.save_json(events_file, events_data)
        se.log(f"Saved {events_file}.")

    _write_workflow_output(mirrored_count > 0)


def _write_workflow_output(had_changes):
    """Set the had_changes step output for the GH Action's commit-and-push gate."""
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")


if __name__ == "__main__":
    main()
