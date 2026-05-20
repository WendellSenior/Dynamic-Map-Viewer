#!/usr/bin/env python3

import argparse
import os
import re
import json
import time
import sys
from datetime import datetime, timedelta, timezone

import requests

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID    = "1434628920371581079"
EVENTS_FILE   = "darthsunday/data/events.json"
CACHE_FILE    = "darthsunday/data/processed_ids.json"
EDIT_LOOKBACK_DAYS = 7

DISCORD_EPOCH = 1_420_070_400_000

BASE = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

VALID_TAGS = {
    "wardec":      "WarDec",
    "battle":      "Battle",
    "character":   "Character",
    "trade":       "Trade",
    "economy":     "Economy",
    "discover":    "Discover",
    "treaty":      "Treaty",
    "meeting":     "Meeting",
    "interaction": "Meeting",
    "diplomacy":   "Meeting",
    "history":     "History",
    "religion":    "Religion",
    "catholic":    "Catholic",
    "muslim":      "Muslim",
    "jewish":      "Jewish",
    "hindu":       "Hindu",
    "buddhism":    "Buddhism",
    "orthodox":    "Orthodox",
    "taoism":      "Taoism",
}

TAG_RE = re.compile(
    r"\[Date:\s*(\d{4}-\d{2}-\d{2})\s*\]"
    r"\s*"
    r"\[Country:\s*([^\]]+?)\s*\]"
    r"\s*"
    r"\[Location:\s*([^\]]+?)\s*\]"
    r"\s*"
    r"(?:\[Tag:\s*([^\]]+?)\s*\])?",
    re.IGNORECASE,
)


def log(msg):
    print(msg, flush=True)


def date_to_snowflake(dt):
    ms = int(dt.timestamp() * 1000)
    return str((max(ms - DISCORD_EPOCH, 0)) << 22)


def snowflake_to_dt(snowflake):
    ms = (int(snowflake) >> 22) + DISCORD_EPOCH
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def api_get(path, params=None, retries=5):
    url = f"{BASE}{path}"
    for _ in range(retries):
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            retry_after = float(resp.json().get("retry_after", 1))
            log(f"  Rate limited. Waiting {retry_after:.1f}s ...")
            time.sleep(retry_after + 0.1)
            continue
        if resp.status_code == 403:
            log(f"  403 Forbidden on {path}, skipping.")
            return None
        if resp.status_code == 404:
            log(f"  404 Not Found: {path}")
            return None
        log(f"  Unexpected {resp.status_code} on {path}: {resp.text[:200]}")
        time.sleep(1)
    log(f"  Giving up on {path} after {retries} attempts.")
    return None


def fetch_messages_since(channel_id, after_snowflake):
    messages = []
    after = after_snowflake
    while True:
        batch = api_get(f"/channels/{channel_id}/messages", {"limit": 100, "after": after})
        if not batch:
            break
        batch.sort(key=lambda m: int(m["id"]))
        messages.extend(batch)
        after = batch[-1]["id"]
        if len(batch) < 100:
            break
    return messages


_DELETED = object()

def fetch_message(channel_id, message_id):
    url = f"{BASE}/channels/{channel_id}/messages/{message_id}"
    for _ in range(5):
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return _DELETED
        if resp.status_code == 429:
            retry_after = float(resp.json().get("retry_after", 1))
            log(f"  Rate limited. Waiting {retry_after:.1f}s ...")
            time.sleep(retry_after + 0.1)
            continue
        if resp.status_code == 403:
            return None
        time.sleep(1)
    return None


def fetch_active_threads(guild_id):
    data = api_get(f"/guilds/{guild_id}/threads/active")
    if not data:
        return []
    return [t for t in data.get("threads", []) if t.get("parent_id") == CHANNEL_ID]


def parse_event_tags(text):
    match = TAG_RE.search(text)
    if not match:
        return None

    raw_date, raw_country, raw_location, raw_tag = match.groups()

    try:
        datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return None

    country_stripped = raw_country.strip()
    if re.fullmatch(r"[A-Z]{2,3}", country_stripped):
        country     = country_stripped
        country_raw = None
    else:
        country     = None
        country_raw = country_stripped

    tag = None
    if raw_tag:
        tag = VALID_TAGS.get(raw_tag.strip().lower())

    # Strip the matched bracket header so it doesn't appear in the snippet/body.
    # Snippets are user-facing; the bracket noise hides the actual title.
    cleaned = (text[:match.start()] + text[match.end():]).strip()

    return {
        "date":       raw_date,
        "country":    country,
        "countryRaw": country_raw,
        "province":   raw_location.strip(),
        "tag":        tag,
        "cleaned":    cleaned,
    }


def build_event(msg, parsed, thread_title=None):
    # Use cleaned content (bracket header removed) for snippet/fullText so the
    # tags don't show up as title-fallback text in the viewer.
    content  = parsed.get("cleaned") or msg.get("content", "")
    author   = msg.get("author", {})
    username = author.get("global_name") or author.get("username", "unknown")
    images = [
        {
            "url":      att["url"],
            "filename": att["filename"],
            "width":    att.get("width"),
            "height":   att.get("height"),
        }
        for att in msg.get("attachments", [])
        if (att.get("content_type") or "").startswith("image/")
    ]
    event = {
        "id":         msg["id"],
        "date":       parsed["date"],
        "country":    parsed["country"],
        "countryRaw": parsed["countryRaw"],
        "province":   parsed["province"],
        "tag":        parsed["tag"],
        "author":     username,
        "snippet":    content[:150],
        "fullText":   content,
        "images":     images,
    }
    # If the message lives in a named thread, the thread name is almost always
    # the post's title. The viewer reads `title` first before falling back to
    # markdown-heading / bold-line heuristics.
    if thread_title:
        event["title"] = thread_title
    return event


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_edits_and_deletions(events_data, event_meta, now_utc, thread_names=None):
    thread_names = thread_names or {}
    cutoff      = now_utc - timedelta(days=EDIT_LOOKBACK_DAYS)
    updated     = False
    to_delete   = []

    for i, event in enumerate(events_data["events"]):
        event_id  = event["id"]
        posted_at = snowflake_to_dt(event_id)
        if posted_at < cutoff:
            continue

        meta = event_meta.get(event_id)
        if not meta:
            continue

        channel_id    = meta["channel_id"]
        stored_edited = meta.get("edited_timestamp")

        msg = fetch_message(channel_id, event_id)

        if msg is _DELETED:
            log(f"  x {event_id} was deleted — removing from events.json")
            to_delete.append(i)
            event_meta.pop(event_id, None)
            updated = True
            continue

        if not msg:
            continue

        current_edited = msg.get("edited_timestamp")
        if current_edited == stored_edited:
            continue

        log(f"  ~ {event_id} was edited (was: {stored_edited}, now: {current_edited})")
        meta["edited_timestamp"] = current_edited

        parsed = parse_event_tags(msg.get("content", ""))
        if parsed:
            thread_title = thread_names.get(channel_id) if channel_id != CHANNEL_ID else None
            events_data["events"][i] = build_event(msg, parsed, thread_title=thread_title)
            log(f"    updated: [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            log(f"    tags removed after edit — keeping old entry unchanged")

        updated = True

    for i in reversed(to_delete):
        events_data["events"].pop(i)

    return updated


def parse_since(s):
    """Parse `--since` value. Accepts YYYY-MM-DD or full ISO 8601."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # Bare date → UTC midnight.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    # Full ISO; tolerate trailing Z.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser(description="Discord Events Sync")
    ap.add_argument("--since", default=os.environ.get("SYNC_SINCE", ""),
                    help="Override the cursor and backfill from this date "
                         "(YYYY-MM-DD or full ISO). One-shot — the cursor "
                         "moves forward after the run as normal.")
    args = ap.parse_args()

    now_utc   = datetime.now(tz=timezone.utc)
    today_utc = now_utc.date()
    today_str = today_utc.isoformat()

    cache           = load_json(CACHE_FILE, {})
    processed_today = set(cache.get(today_str, []))
    # event_meta: {msg_id: {channel_id, edited_timestamp}}
    event_meta      = cache.get("event_meta", {})
    events_data     = load_json(EVENTS_FILE, {"events": []})
    existing_ids    = {e["id"] for e in events_data.get("events", [])}

    # Resolve where to start fetching from. Priority:
    #   1. --since arg / SYNC_SINCE env  → one-shot backfill
    #   2. cache["last_snowflake"]       → resume from last run
    #   3. today midnight UTC            → first-run fallback
    since_dt = parse_since(args.since)
    if since_dt is not None:
        after_snowflake = date_to_snowflake(since_dt)
        log(f"=== Discord Events Sync — BACKFILL from {since_dt.isoformat()} ===")
    elif cache.get("last_snowflake"):
        after_snowflake = cache["last_snowflake"]
        cursor_dt = snowflake_to_dt(after_snowflake)
        log(f"=== Discord Events Sync — resume after {cursor_dt.isoformat()} ===")
    else:
        today_midnight = datetime.combine(today_utc, datetime.min.time()).replace(tzinfo=timezone.utc)
        after_snowflake = date_to_snowflake(today_midnight)
        log(f"=== Discord Events Sync — first run, starting at today midnight ({today_str} UTC) ===")

    channel_info = api_get(f"/channels/{CHANNEL_ID}")
    if not channel_info:
        log("ERROR: Could not fetch channel info.")
        sys.exit(1)
    guild_id = channel_info.get("guild_id")
    log(f"Guild ID: {guild_id}")

    log("Fetching active threads ...")
    active_threads = fetch_active_threads(guild_id)
    log(f"  {len(active_threads)} active thread(s).")

    # Map channel/thread ID -> thread name. Used as the event title when the
    # author posts inside a named thread (Discord threads carry the title even
    # if the message body has no `# Heading` or `**Bold**` line).
    thread_names = {t["id"]: t.get("name", "") for t in active_threads if t.get("name")}

    channels_to_scan = [CHANNEL_ID] + [t["id"] for t in active_threads]

    # Collect messages tagged with which channel they came from
    all_messages = []
    for ch_id in channels_to_scan:
        label = "main channel" if ch_id == CHANNEL_ID else f"thread {ch_id}"
        msgs  = fetch_messages_since(ch_id, after_snowflake)
        log(f"  {len(msgs)} message(s) from {label}")
        for msg in msgs:
            all_messages.append((msg, ch_id))

    log(f"Total messages to evaluate: {len(all_messages)}")

    # Advance the cursor to the highest message ID we saw, regardless of whether
    # each became a new event. This guarantees forward progress even on quiet days.
    if all_messages:
        highest_seen = max(int(m[0]["id"]) for m in all_messages)
        prev_cursor = int(cache.get("last_snowflake") or 0)
        cache["last_snowflake"] = str(max(prev_cursor, highest_seen))

    new_events = []
    seen       = set()

    for msg, ch_id in all_messages:
        msg_id = msg["id"]
        if msg_id in processed_today or msg_id in existing_ids or msg_id in seen:
            continue
        seen.add(msg_id)
        processed_today.add(msg_id)

        parsed = parse_event_tags(msg.get("content", ""))
        if parsed:
            thread_title = thread_names.get(ch_id) if ch_id != CHANNEL_ID else None
            new_events.append(build_event(msg, parsed, thread_title=thread_title))
            event_meta[msg_id] = {
                "channel_id":       ch_id,
                "edited_timestamp": msg.get("edited_timestamp"),
            }
            log(f"  + {msg_id} [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            log(f"  - {msg_id} (no valid tags) | {repr(msg.get('content', '')[:80])}")

    # ── Edit check ────────────────────────────────────────────────────────────
    log("Checking recent events for edits/deletions ...")
    edits_found = check_edits_and_deletions(events_data, event_meta, now_utc, thread_names)
    if not edits_found:
        log("  No changes found.")

    # ── Persist ───────────────────────────────────────────────────────────────
    had_changes = bool(new_events) or edits_found

    if new_events:
        events_data["events"].extend(new_events)

    if had_changes:
        save_json(EVENTS_FILE, events_data)
        log(f"Saved events.json ({len(new_events)} new, edits={edits_found}).")
    else:
        log("No changes this run.")

    # Prune event_meta entries older than EDIT_LOOKBACK_DAYS
    cutoff_dt = now_utc - timedelta(days=EDIT_LOOKBACK_DAYS)
    event_meta = {
        k: v for k, v in event_meta.items()
        if snowflake_to_dt(k) >= cutoff_dt
    }

    cache[today_str]   = list(processed_today)
    cache["event_meta"] = event_meta

    cutoff_date = (today_utc - timedelta(days=7)).isoformat()
    cache = {
        k: v for k, v in cache.items()
        if k == "event_meta" or k >= cutoff_date
    }
    save_json(CACHE_FILE, cache)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")

    return had_changes


if __name__ == "__main__":
    main()
