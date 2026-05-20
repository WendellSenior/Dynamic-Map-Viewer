#!/usr/bin/env python3

import os
import re
import json
import time
import sys
from datetime import date, datetime, timedelta, timezone

import requests

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID    = "1434628920371581079"
EVENTS_FILE   = "darthsunday/data/events.json"
CACHE_FILE    = "darthsunday/data/processed_ids.json"

DISCORD_EPOCH = 1_420_070_400_000

BASE = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

VALID_TAGS = {
    "wardec":    "WarDec",
    "battle":    "Battle",
    "character": "Character",
    "trade":     "Trade",
    "economy":   "Economy",
    "discover":  "Discover",
    "treaty":    "Treaty",
    "meeting":   "Meeting",
    "history":   "History",
    "religion":  "Religion",
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


def api_get(path, params=None, retries=5):
    url = f"{BASE}{path}"
    for _ in range(retries):
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            retry_after = float(resp.json().get("retry_after", 1))
            log(f"  Rate limited on {path}. Waiting {retry_after:.1f}s ...")
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


def fetch_active_threads(guild_id):
    data = api_get(f"/guilds/{guild_id}/threads/active")
    if not data:
        return []
    return [t for t in data.get("threads", []) if t.get("parent_id") == CHANNEL_ID]


def fetch_archived_public_threads():
    threads = []
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        data = api_get(f"/channels/{CHANNEL_ID}/threads/archived/public", params)
        if not data:
            break
        batch = data.get("threads", [])
        threads.extend(batch)
        if not data.get("has_more") or not batch:
            break
        before = min(
            t.get("thread_metadata", {}).get("archive_timestamp", "")
            for t in batch
        )
        if not before:
            break
    return threads


def parse_event_tags(content):
    match = TAG_RE.search(content)
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

    return {
        "date":       raw_date,
        "country":    country,
        "countryRaw": country_raw,
        "province":   raw_location.strip(),
        "tag":        tag,
    }


def message_to_event(msg, parsed):
    content  = msg.get("content", "")
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

    return {
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


def main():
    today_str = date.today().isoformat()
    log(f"=== Discord Events Sync ({today_str} UTC) ===")

    today_midnight  = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    after_snowflake = date_to_snowflake(today_midnight)

    cache           = load_json(CACHE_FILE, {})
    processed_today = set(cache.get(today_str, []))
    events_data     = load_json(EVENTS_FILE, {"events": []})
    existing_ids    = {e["id"] for e in events_data.get("events", [])}

    channel_info = api_get(f"/channels/{CHANNEL_ID}")
    if not channel_info:
        log("ERROR: Could not fetch channel info.")
        sys.exit(1)
    guild_id = channel_info.get("guild_id")
    log(f"Guild ID: {guild_id}")

    channels_to_scan = [CHANNEL_ID]

    log("Fetching active threads ...")
    active = fetch_active_threads(guild_id)
    log(f"  {len(active)} active thread(s).")
    channels_to_scan += [t["id"] for t in active]

    log("Fetching archived public threads ...")
    archived = fetch_archived_public_threads()
    log(f"  {len(archived)} archived thread(s).")
    channels_to_scan += [t["id"] for t in archived]

    all_messages = []
    for ch_id in channels_to_scan:
        label = "main channel" if ch_id == CHANNEL_ID else f"thread {ch_id}"
        msgs  = fetch_messages_since(ch_id, after_snowflake)
        log(f"  {len(msgs)} message(s) from {label}")
        all_messages.extend(msgs)

    log(f"Total messages to evaluate: {len(all_messages)}")

    new_events    = []
    seen_this_run = set()

    for msg in all_messages:
        msg_id = msg["id"]
        if msg_id in processed_today or msg_id in existing_ids or msg_id in seen_this_run:
            continue
        seen_this_run.add(msg_id)

        parsed = parse_event_tags(msg.get("content", ""))
        if parsed:
            new_events.append(message_to_event(msg, parsed))
            log(f"  + {msg_id}  [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            snippet = repr(msg.get("content", "")[:120])
            log(f"  - {msg_id} (no valid tags) | {snippet}")

    had_changes = bool(new_events)
    if had_changes:
        events_data["events"].extend(new_events)
        save_json(EVENTS_FILE, events_data)
        log(f"Added {len(new_events)} new event(s).")
    else:
        log("No new events this run.")

    cache[today_str] = list(processed_today | {m["id"] for m in all_messages})
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    cache  = {k: v for k, v in cache.items() if k >= cutoff}
    save_json(CACHE_FILE, cache)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")

    return had_changes


if __name__ == "__main__":
    main()