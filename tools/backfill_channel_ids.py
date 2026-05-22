#!/usr/bin/env python3
"""One-shot: backfill `channel_id` on events.json entries that lack it.

Older events (posted before `sync_events.py` started storing channel_id, and
already pruned from `event_meta`'s 7-day cache) have no way to build a
"View on Discord" link. This walks the guild's threads (active + archived
public) + main channel and tries `GET /channels/{ch}/messages/{msg_id}` for
each candidate. The first channel that returns 200 owns the message; the
script records the channel_id back into events.json.

Candidate filtering: a thread can't contain messages posted before the thread
was created, so we skip threads whose own snowflake (thread_id) is greater
than the message's snowflake. That alone trims most of the search space.

Usage:
    DISCORD_TOKEN=... python tools/backfill_channel_ids.py --campaign darthsunday
or via workflow_dispatch with backfill_channel_ids=true.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
BASE = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {DISCORD_TOKEN}"}
DISCORD_EPOCH = 1_420_070_400_000


def log(msg):
    print(msg, flush=True)


def snowflake_ms(s):
    return (int(s) >> 22) + DISCORD_EPOCH


def api_get(path, params=None, retries=5):
    url = f"{BASE}{path}"
    for _ in range(retries):
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code == 200:
            return r.status_code, r.json()
        if r.status_code == 404:
            return 404, None
        if r.status_code == 403:
            return 403, None
        if r.status_code == 429:
            try:
                wait = float(r.json().get("retry_after", 1))
            except (ValueError, TypeError):
                wait = 1.0
            log(f"  Rate limited. Waiting {wait:.1f}s ...")
            time.sleep(wait + 0.1)
            continue
        log(f"  Unexpected {r.status_code} on {path}: {r.text[:200]}")
        time.sleep(1)
    return None, None


def fetch_active_threads(guild_id, parent_channel_id):
    _, data = api_get(f"/guilds/{guild_id}/threads/active")
    if not data:
        return []
    return [t for t in data.get("threads", []) if t.get("parent_id") == parent_channel_id]


def fetch_archived_public_threads(channel_id, max_pages=10):
    out = []
    before = None
    for _ in range(max_pages):
        params = {"limit": 100}
        if before is not None:
            params["before"] = before
        _, data = api_get(f"/channels/{channel_id}/threads/archived/public", params)
        if not data:
            break
        threads = data.get("threads", [])
        out.extend(threads)
        if not data.get("has_more") or not threads:
            break
        meta = threads[-1].get("thread_metadata") or {}
        before = meta.get("archive_timestamp")
        if not before:
            break
    return out


def load_campaign(folder):
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "campaigns.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("campaigns", []):
        if entry.get("folder") == folder:
            sync = entry.get("discord_sync") or {}
            if not sync.get("enabled"):
                sys.exit(f"campaign {folder!r} has no discord_sync.enabled in campaigns.json")
            return repo_root, sync.get("channel_id"), sync.get("guild_id")
    sys.exit(f"campaign {folder!r} not found in campaigns.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Look up but don't write events.json.")
    args = ap.parse_args()

    repo_root, parent_channel_id, guild_id = load_campaign(args.campaign)
    if not guild_id:
        sys.exit("Campaign missing discord_sync.guild_id; add it to campaigns.json.")
    events_path = repo_root / args.campaign / "data" / "events.json"
    data = json.loads(events_path.read_text(encoding="utf-8"))

    todo = [e for e in data["events"] if not e.get("channel_id")]
    if not todo:
        log("All events already have channel_id. Nothing to do.")
        return

    log(f"Backfill target: {len(todo)} event(s) missing channel_id.")
    log(f"Loading channel list (parent={parent_channel_id}, guild={guild_id}) ...")
    active = fetch_active_threads(guild_id, parent_channel_id)
    archived = fetch_archived_public_threads(parent_channel_id)
    log(f"  {len(active)} active thread(s), {len(archived)} archived thread(s).")

    # Dedupe and form candidate list with the main channel first.
    seen = {parent_channel_id}
    candidates = [(parent_channel_id, "main channel")]
    for t in active + archived:
        tid = t.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            candidates.append((tid, t.get("name") or "(unnamed)"))
    log(f"  {len(candidates)} total candidates (1 main + {len(candidates)-1} threads).")

    resolved = 0
    not_found = []
    for e in todo:
        msg_id = e["id"]
        msg_ms = snowflake_ms(msg_id)
        # Filter: skip threads whose snowflake is >= the message's snowflake
        # (the thread didn't exist yet). Main channel is always viable.
        viable = [(cid, name) for cid, name in candidates
                  if cid == parent_channel_id or snowflake_ms(cid) <= msg_ms]
        log(f"\n  {msg_id} ({e.get('author', '?')}, {e.get('province', '?')}): "
            f"checking {len(viable)} viable channel(s)")
        hit = None
        for cid, name in viable:
            code, _ = api_get(f"/channels/{cid}/messages/{msg_id}")
            if code == 200:
                hit = (cid, name)
                break
        if hit:
            cid, name = hit
            log(f"    -> FOUND in {name} ({cid})")
            if not args.dry_run:
                e["channel_id"] = cid
            resolved += 1
        else:
            log(f"    -> NOT FOUND anywhere (message deleted or in private thread?)")
            not_found.append(msg_id)

    log("")
    log(f"Summary: {resolved}/{len(todo)} channel_id(s) resolved.")
    if not_found:
        log(f"  Unresolved IDs: {', '.join(not_found)}")

    if not args.dry_run and resolved > 0:
        events_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log(f"Wrote {events_path}")
    elif args.dry_run:
        log("(dry-run; no file written)")

    # Surface had_changes for the workflow's commit-and-push step.
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if (resolved > 0 and not args.dry_run) else 'false'}\n")


if __name__ == "__main__":
    main()
