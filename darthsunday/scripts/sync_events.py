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


def fetch_active_threads(guild_id):
    data = api_get(f"/guilds/{guild_id}/threads/active")
    if not data:
        return []
    return [t for t in data.get("threads", []) if t.get("parent_id") == CHANNEL_ID]


def fetch_starter_message(thread_id):
    return api_get(f"/channels/{thread_id}/messages/{thread_id}")


def fetch_main_channel_messages(after_snowflake):
    messages = []
    after = after_snowflake
    while True:
        batch = api_get(f"/channels/{CHANNEL_ID}/messages", {"limit": 100, "after": after})
        if not batch:
            break
        batch.sort(key=lambda m: int(m["id"]))
        messages.extend(batch)
        after = batch[-1]["id"]
        if len(batch) < 100:
            break
    return messages


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

    return {
        "date":       raw_date,
        "country":    country,
        "countryRaw": country_raw,
        "province":   raw_location.strip(),
        "tag":        tag,
    }


def extract_images(msg):
    return [
        {
            "url":      att["url"],
            "filename": att["filename"],
            "width":    att.get("width"),
            "height":   att.get("height"),
        }
        for att in (msg or {}).get("attachments", [])
        if (att.get("content_type") or "").startswith("image/")
    ]


def build_event(event_id, parsed, msg):
    content  = (msg or {}).get("content", "")
    author   = (msg or {}).get("author", {})
    username = author.get("global_name") or author.get("username", "unknown")
    return {
        "id":         event_id,
        "date":       parsed["date"],
        "country":    parsed["country"],
        "countryRaw": parsed["countryRaw"],
        "province":   parsed["province"],
        "tag":        parsed["tag"],
        "author":     username,
        "snippet":    content[:150],
        "fullText":   content,
        "images":     extract_images(msg),
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
    today_utc       = datetime.now(tz=timezone.utc).date()
    today_str       = today_utc.isoformat()
    today_midnight  = datetime.combine(today_utc, datetime.min.time()).replace(tzinfo=timezone.utc)
    after_snowflake = date_to_snowflake(today_midnight)

    log(f"=== Discord Events Sync ({today_str} UTC) ===")

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

    log("Fetching active threads ...")
    active_threads = fetch_active_threads(guild_id)
    log(f"  {len(active_threads)} active thread(s).")

    new_events  = []
    seen        = set()

    # ── Forum threads: tags live in the thread name (post title) ─────────────
    for thread in active_threads:
        thread_id   = thread["id"]
        thread_name = thread.get("name", "")
        created_dt  = snowflake_to_dt(thread_id)

        if thread_id in processed_today or thread_id in existing_ids or thread_id in seen:
            continue
        seen.add(thread_id)

        if created_dt.date() != today_utc:
            log(f"  ~ thread {thread_id} not from today, skipping")
            continue

        parsed = parse_event_tags(thread_name)
        if parsed:
            starter = fetch_starter_message(thread_id)
            event   = build_event(thread_id, parsed, starter)
            new_events.append(event)
            log(f"  + thread {thread_id} [{parsed['date']}] {parsed['province']} ({parsed['tag']}) | title: {repr(thread_name[:80])}")
        else:
            log(f"  - thread {thread_id} (no valid tags) | title: {repr(thread_name[:80])}")

        processed_today.add(thread_id)

    # ── Main channel: tags in message content (non-forum fallback) ───────────
    main_msgs = fetch_main_channel_messages(after_snowflake)
    log(f"  {len(main_msgs)} message(s) from main channel")

    for msg in main_msgs:
        msg_id = msg["id"]
        if msg_id in processed_today or msg_id in existing_ids or msg_id in seen:
            continue
        seen.add(msg_id)
        processed_today.add(msg_id)

        parsed = parse_event_tags(msg.get("content", ""))
        if parsed:
            new_events.append(build_event(msg_id, parsed, msg))
            log(f"  + msg {msg_id} [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            log(f"  - msg {msg_id} (no valid tags) | {repr(msg.get('content','')[:80])}")

    log(f"Total new events: {len(new_events)}")

    had_changes = bool(new_events)
    if had_changes:
        events_data["events"].extend(new_events)
        save_json(EVENTS_FILE, events_data)
        log(f"Saved {len(new_events)} new event(s) to {EVENTS_FILE}.")
    else:
        log("No new events this run.")

    cache[today_str] = list(processed_today)
    cutoff = (today_utc - timedelta(days=7)).isoformat()
    cache  = {k: v for k, v in cache.items() if k >= cutoff}
    save_json(CACHE_FILE, cache)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")

    return had_changes


if __name__ == "__main__":
    main()