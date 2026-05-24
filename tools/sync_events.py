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
EDIT_LOOKBACK_DAYS = 7

# Same-author follow-up messages within this window get merged into the parent
# event's body + images. Mirrors `preprocess.py`'s CONTINUATION_WINDOW so a
# Discord post split into 3-4 chat messages (because of length limits, image
# uploads, or thinking pauses) shows up as one coherent event in the viewer.
CONTINUATION_WINDOW = timedelta(minutes=5)

# Per-campaign config is loaded from campaigns.json at run time (--campaign arg
# selects which entry). These are filled in by load_campaign_config().
CHANNEL_ID    = None  # str, the Discord channel id
EVENTS_FILE   = None  # str, path to <campaign>/data/events.json
CACHE_FILE    = None  # str, path to <campaign>/data/processed_ids.json
REFERENCE_DIR = None  # str, path to assets/reference/<game>/ (shared, read-only)

# Per-run cap on promotion-check API calls. Once `rejected_meta` grows past
# this (a few hundred for a long-running campaign), the check rotates through
# entries in *least-recently-checked* order — every entry still gets revisited,
# just spread over multiple cron runs. With cap=100 and ~500 rejections, each
# entry is rechecked every ~5 hours instead of every hour. Set to None to
# disable the cap.
MAX_PROMOTION_CHECK_PER_RUN = 100

# Populated once at startup by load_country_lookup(); maps lowercase
# name/alias/tag -> canonical TAG. Empty dict if reference data is missing.
COUNTRY_LOOKUP = {}

DISCORD_EPOCH = 1_420_070_400_000

BASE = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

def _load_event_tags_registry():
    """Read the shared assets/event-tags.json registry and return a flat
    alias→canonical map (lowercase keys). Canonical names are also added as
    their own aliases so case-insensitive matches still resolve.

    Single source of truth for tag definitions across the project — keeps the
    sync's strict parser in lockstep with the viewer, preprocess.py, and the
    bracket-generator dropdown."""
    here = os.path.dirname(os.path.abspath(__file__))
    registry_path = os.path.join(os.path.dirname(here), "assets", "event-tags.json")
    try:
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for canonical, info in (data.get("tags") or {}).items():
        if not isinstance(info, dict):
            continue
        out[canonical.lower()] = canonical
        for alias in (info.get("aliases") or []):
            if alias:
                out[alias.lower()] = canonical
    return out


VALID_TAGS = _load_event_tags_registry()
if not VALID_TAGS:
    sys.exit("ERROR: failed to load assets/event-tags.json — tag parsing would silently reject every post.")


TAG_RE = re.compile(
    # The Date bracket is captured loosely — the strict ISO-only regex used to
    # be inline here but rejected perfectly reasonable forms like
    # `[Date:1 October 1338]`. Validation/parsing happens in _parse_flexible_date
    # below; the bracket header is only accepted if that returns a valid ISO date.
    r"\[Date:\s*([^\]]+?)\s*\]"
    r"\s*"
    r"\[Country:\s*([^\]]+?)\s*\]"
    r"\s*"
    r"\[Location:\s*([^\]]+?)\s*\]"
    r"\s*"
    r"(?:\[Tag:\s*([^\]]+?)\s*\])?",
    re.IGNORECASE,
)

# Month-name → month-number lookup for the flexible date parser. Mirrors
# tools/preprocess.py's table so both pipelines accept the same forms.
_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _parse_flexible_date(s):
    """Accept multiple historical-date forms and return canonical ISO
    'YYYY-MM-DD', or None if unparseable. Mirrors preprocess.py.parse_date so
    `[Date:1 October 1338]`, `[Date:1st April 1337]`, `[Date:1338-10-01]`,
    `[Date:11 Nov 1444]`, etc. all map to the same stored representation."""
    if not s:
        return None
    s = s.strip().lower()
    # 1338-10-01 / 1338.10.01 / 1338/10/01 (ISO-ish, Y-M-D)
    m = re.fullmatch(r"(\d{3,4})[./\-](\d{1,2})[./\-](\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return None
    # 1 October 1338 / 1st April 1337 / 11 Nov 1444 / 13th December 1513
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[_\s./\-]+([a-z]+),?[_\s./\-]+(\d{3,4})", s)
    if m and m[2] in _MONTH_NAMES:
        d, y = int(m[1]), int(m[3])
        if 1 <= d <= 31:
            return f"{y:04d}-{_MONTH_NAMES[m[2]]:02d}-{d:02d}"
        return None
    # October 1, 1338 / Oct 1 1338 / Sept 21st, 1500
    m = re.fullmatch(r"([a-z]+)[_\s./\-]+(\d{1,2})(?:st|nd|rd|th)?,?[_\s./\-]+(\d{3,4})", s)
    if m and m[1] in _MONTH_NAMES:
        d, y = int(m[2]), int(m[3])
        if 1 <= d <= 31:
            return f"{y:04d}-{_MONTH_NAMES[m[1]]:02d}-{d:02d}"
        return None
    # 1338 May 1 (year-first, name-style)
    m = re.fullmatch(r"(\d{3,4})[_\s./\-]+([a-z]+)[_\s./\-]+(\d{1,2})", s)
    if m and m[2] in _MONTH_NAMES:
        d, y = int(m[3]), int(m[1])
        if 1 <= d <= 31:
            return f"{y:04d}-{_MONTH_NAMES[m[2]]:02d}-{d:02d}"
        return None
    return None


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


def fetch_messages_since(channel_id, after_snowflake, before_snowflake=None):
    """Paginate /channels/{id}/messages from `after_snowflake` forward.

    If `before_snowflake` is given, drop messages with id > before_snowflake
    and stop paginating once we've reached it. Discord's API forbids mixing
    `after=` + `before=` in one request, so the upper bound is enforced
    client-side after each batch. Used for chunked backfills."""
    messages = []
    after = after_snowflake
    upper = int(before_snowflake) if before_snowflake is not None else None
    while True:
        batch = api_get(f"/channels/{channel_id}/messages", {"limit": 100, "after": after})
        if not batch:
            break
        batch.sort(key=lambda m: int(m["id"]))
        if upper is not None:
            batch = [m for m in batch if int(m["id"]) <= upper]
        if not batch:
            break
        messages.extend(batch)
        after = batch[-1]["id"]
        if upper is not None and int(after) >= upper:
            break
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


def fetch_archived_public_threads(channel_id, max_pages=10):
    """Paginate archived public threads for the channel, newest-archived first.

    Discord's active-threads endpoint excludes auto-archived threads, so battle/
    diplomacy posts whose threads have gone quiet vanish from the sync's scan.
    This pulls them back in. Cap on pages so a channel with thousands of old
    threads doesn't make the workflow run forever — `max_pages=10` × 100/page
    = up to 1000 most-recently-archived threads."""
    out = []
    before = None
    for _ in range(max_pages):
        params = {"limit": 100}
        if before is not None:
            params["before"] = before
        data = api_get(f"/channels/{channel_id}/threads/archived/public", params)
        if not data:
            break
        threads = data.get("threads", [])
        out.extend(threads)
        if not data.get("has_more") or not threads:
            break
        # Discord paginates archived threads by `archive_timestamp`. The next
        # page starts before the oldest entry on this page.
        last_meta = threads[-1].get("thread_metadata") or {}
        before = last_meta.get("archive_timestamp")
        if not before:
            break
    return out


def parse_event_tags(text):
    match = TAG_RE.search(text)
    if not match:
        return None

    raw_date, raw_country, raw_location, raw_tag = match.groups()

    # Accept any of YYYY-MM-DD / 1 October 1338 / 1st April 1337 / 11 Nov 1444
    # / Oct 1, 1338 / 1338 May 1. Normalised to ISO for storage so downstream
    # code (timeline scrubber, sorting, comparison) stays simple.
    iso_date = _parse_flexible_date(raw_date)
    if not iso_date:
        return None

    country_stripped = raw_country.strip()
    if re.fullmatch(r"[A-Z]{2,3}", country_stripped):
        # Already a tag — trust it as-is.
        country     = country_stripped
        country_raw = None
    else:
        # Free-form name. Try the lookup; fall back to countryRaw-only if unknown.
        resolved    = resolve_country(country_stripped)
        country     = resolved
        country_raw = country_stripped

    tag = None
    if raw_tag:
        tag = VALID_TAGS.get(raw_tag.strip().lower())

    # Strip the matched bracket header so it doesn't appear in the snippet/body.
    # Snippets are user-facing; the bracket noise hides the actual title.
    cleaned = (text[:match.start()] + text[match.end():]).strip()

    return {
        "date":       iso_date,
        "country":    country,
        "countryRaw": country_raw,
        "province":   raw_location.strip(),
        "tag":        tag,
        "cleaned":    cleaned,
    }


# Lines that the viewer's extractTitle() will treat as a natural post title.
# Markdown heading (# / ## / ###) or a bold-only line (**Title** / *** Title ***).
_HEADING_LINE_RE   = re.compile(r"^\s*#{1,3}\s+\S")
_BOLD_ONLY_LINE_RE = re.compile(r"^\s*\*{2,3}\s*[^*\n]+?\s*\*{2,3}\s*$")
# Spoiler-wrap stripper: handles balanced `||...||` and unclosed leading `||`.
# Players wrap a whole headline in spoiler markers (e.g. `||### A letter from
# France||`) — we should still recognise it as a natural title.
_LEADING_SPOILER_RE  = re.compile(r"^(\s*)\|\|(.*)$")
_TRAILING_SPOILER_RE = re.compile(r"^(.*)\|\|(\s*)$")


def _strip_spoiler_wrap(line):
    lead = _LEADING_SPOILER_RE.match(line)
    if not lead:
        return line
    inner = lead.group(1) + lead.group(2)
    trail = _TRAILING_SPOILER_RE.match(inner)
    if trail:
        inner = trail.group(1) + trail.group(2)
    return inner


def _content_has_natural_title(text):
    """True if any line in `text` would be picked up as a title by the viewer's
    extractTitle() — markdown heading or bold-only line, optionally wrapped
    in `||...||` spoiler markers. Used to decide whether to stamp the thread
    name onto event['title'].

    Without this check, every post in a named thread inherits the thread name,
    which masks actual post titles like 'La Battaglia di Ferrara' with the
    parent thread label ('Central Europe and North Africa Diplo'). The thread
    name is only useful as a fallback when the body has no obvious title."""
    if not text:
        return False
    for line in text.split("\n"):
        if _HEADING_LINE_RE.match(line) or _BOLD_ONLY_LINE_RE.match(line):
            return True
        # Retry after stripping `||...||` spoiler wrap.
        unwrapped = _strip_spoiler_wrap(line)
        if unwrapped is not line and (
            _HEADING_LINE_RE.match(unwrapped) or _BOLD_ONLY_LINE_RE.match(unwrapped)
        ):
            return True
    return False


def build_event(msg, parsed, thread_title=None, channel_id=None):
    # Use cleaned content (bracket header removed) for snippet/fullText so the
    # tags don't show up as title-fallback text in the viewer.
    content  = parsed.get("cleaned") or msg.get("content", "")
    author   = msg.get("author", {})
    username = author.get("global_name") or author.get("username", "unknown")
    # Prefer the caller-supplied ch_id (ground truth from the scan loop /
    # edit-detection) and fall back to the message's own field. Threads and the
    # parent channel use different ids, so the wrong value would break the
    # Discord URL even for a valid message.
    ch_id = channel_id or msg.get("channel_id") or ""
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
        # channel_id lets the viewer build a `https://discord.com/channels/.../...`
        # link back to the original post. Threads have their own id (distinct
        # from the parent channel); the scan loop passes the right one in.
        "channel_id": ch_id,
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
    # Thread name as title — but only when the body has no natural title of
    # its own. If the post starts with "# Heading" or "**Bold Title**", the
    # viewer's extractTitle() will surface it; stamping the thread name on
    # top of that would mask the real title (see _content_has_natural_title
    # comment above). Falls back to thread name when the body is just prose.
    if thread_title and not _content_has_natural_title(content):
        event["title"] = thread_title
    return event


def _msg_images(msg):
    return [
        {
            "url":      att["url"],
            "filename": att["filename"],
            "width":    att.get("width"),
            "height":   att.get("height"),
        }
        for att in msg.get("attachments", [])
        if (att.get("content_type") or "").startswith("image/")
    ]


def _event_channel_id(ev, event_meta):
    """Channel id for an event. Prefers the event's own field (added in the
    channel_id refactor); falls back to event_meta cache for older events
    backfilled or synced before that."""
    if ev.get("channel_id"):
        return ev["channel_id"]
    meta = event_meta.get(ev.get("id"))
    return (meta or {}).get("channel_id")


def _has_plausible_parent(msg_id, channel_id, events_data, event_meta):
    """Local-only pre-filter for the reconcile path: does an event exist
    in the same channel posted within CONTINUATION_WINDOW BEFORE this
    rejected message? If not, no point fetching from Discord — there's
    nothing the message could merge into.

    Pure O(events) scan, no API. We don't have the rejected message's
    author here (would require a fetch), so we can't fully validate the
    match — just that the temporal+channel candidate exists. The actual
    fetch + author check happens later for the surviving candidates."""
    if not channel_id:
        return False
    try:
        msg_ts = snowflake_to_dt(msg_id)
    except (ValueError, TypeError):
        return False
    cutoff = msg_ts - CONTINUATION_WINDOW
    for ev in events_data.get("events", []):
        if _event_channel_id(ev, event_meta) != channel_id:
            continue
        try:
            ev_ts = snowflake_to_dt(ev["id"])
        except (ValueError, TypeError):
            continue
        if cutoff <= ev_ts < msg_ts:
            return True
    return False


def _find_recent_event_by_author(msg, ch_id, new_events, events_data, event_meta,
                                  *, extra_match=None):
    """Walk new_events (newest first) then events.json (newest first) looking
    for an event by the same author in the same channel posted **before**
    `msg` and within CONTINUATION_WINDOW of it. Optionally also requires
    `extra_match(ev)` to return True. Returns the matched event dict, or
    None.

    Note both bounds: the candidate must satisfy `cutoff <= ev_ts < msg_ts`.
    The upper bound matters in reconcile/backfill paths where events_data
    holds events from across all time; without it, a same-author event
    posted hours/days AFTER the rejected message could spuriously match
    (since reversed(events_data) walks newest-first). The scan-loop path
    happened to work without the upper bound because messages are processed
    chronologically, so future events simply weren't in new_events yet.

    Shared by both merge paths: continuation (header-less follow-up) and
    repeat-header (length-split posts where the author re-pasted the brackets
    on each chunk)."""
    try:
        msg_ts = snowflake_to_dt(msg["id"])
    except (KeyError, ValueError, TypeError):
        return None

    author = msg.get("author") or {}
    author_username = author.get("global_name") or author.get("username", "unknown")
    cutoff = msg_ts - CONTINUATION_WINDOW

    def _matches(ev):
        if ev.get("author") != author_username:
            return False
        if _event_channel_id(ev, event_meta) != ch_id:
            return False
        try:
            ev_ts = snowflake_to_dt(ev["id"])
        except (ValueError, TypeError):
            return False
        # Must be in the [msg_ts - 5 min, msg_ts) window — strict upper bound.
        if ev_ts >= msg_ts or ev_ts < cutoff:
            return False
        return extra_match(ev) if extra_match else True

    for ev in reversed(new_events):
        if _matches(ev):
            return ev
    for ev in reversed(events_data.get("events", [])):
        if _matches(ev):
            return ev
    return None


def _merge_into(candidate, content, images):
    """Append `content` (paragraph-separated) + `images` (extended) into
    `candidate` and refresh its snippet. Returns True if anything was added."""
    if not content and not images:
        return False
    if content:
        existing = candidate.get("fullText") or ""
        candidate["fullText"] = (existing + "\n\n" + content).strip() if existing else content
        candidate["snippet"] = candidate["fullText"][:150]
    if images:
        candidate["images"] = (candidate.get("images") or []) + images
    return True


def _rebuild_parent_from_followers(parent_event, parent_msg, followers_msgs_in_order):
    """Rebuild a parent event's fullText + images by concatenating the parent
    message's cleaned body with each follower's body, in chronological order.

    `parent_msg`             - the freshly-fetched Discord message for the parent
    `followers_msgs_in_order`- list of (msg, info) pairs, sorted by snowflake

    Used by check_merged_followers_for_edits when a follower has been edited
    or deleted on Discord — we can't easily diff the old vs new content
    inside the parent's existing fullText (it's a concatenation), so we
    rebuild from source of truth."""
    parsed = parse_event_tags(parent_msg.get("content", ""))
    if not parsed:
        return False  # parent no longer has a valid bracket header
    body = parsed.get("cleaned") or parent_msg.get("content", "")
    images = _msg_images(parent_msg)

    for f_msg, _info in followers_msgs_in_order:
        if not f_msg or f_msg is _DELETED:
            continue  # the deleted follower simply drops out of the rebuild
        f_content = (f_msg.get("content") or "").strip()
        f_images = _msg_images(f_msg)
        if f_content:
            body = (body + "\n\n" + f_content).strip() if body else f_content
        if f_images:
            images.extend(f_images)

    parent_event["fullText"] = body
    parent_event["snippet"]  = body[:150]
    parent_event["images"]   = images
    return True


def check_merged_followers_for_edits(merged_meta, event_meta, events_data,
                                      now_utc, max_per_run=100):
    """Walk merged_meta entries within the 7-day window. Refetch each
    follower message; if its edited_timestamp has changed or the message
    has been deleted, rebuild the parent event by refetching parent + all
    followers and concatenating their bodies.

    Without this pass, an edit on a continuation message (the 2nd, 3rd, …
    message of a length-split post) would never propagate into the parent
    event — only edits on the parent's own bracketed message triggered a
    refresh (via check_edits_and_deletions). The author would have to edit
    the parent to force a re-sync, which is a behavioural workaround we
    explicitly didn't want to require.

    Capped per run (default 100) using the same least-recently-checked
    rotation as check_rejected_for_promotions; entries gain a `last_checked`
    timestamp the first time they're refetched.
    """
    cutoff = now_utc - timedelta(days=EDIT_LOOKBACK_DAYS)
    now_iso = now_utc.isoformat()

    # ── Pass 1: pick candidates (in-window, well-formed, with channel_id). ──
    candidates = []
    for fid, info in merged_meta.items():
        if not isinstance(info, dict):
            continue  # legacy schema entry — startup migration will fix; skip this run
        if not info.get("channel_id") or not info.get("parent_id"):
            continue
        try:
            if snowflake_to_dt(fid) < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        candidates.append((fid, info))

    if not candidates:
        return False, 0

    candidates.sort(key=lambda kv: (kv[1].get("last_checked") is not None,
                                    kv[1].get("last_checked") or ""))
    if max_per_run is not None and len(candidates) > max_per_run:
        log(f"  merged_meta has {len(candidates)} in-window candidate(s); "
            f"capping check at {max_per_run} (least-recently-checked first).")
        candidates = candidates[:max_per_run]

    # ── Pass 2: refetch each candidate, accumulate per-parent change sets. ──
    # Group fetched messages by parent so we only rebuild each parent once.
    by_parent = {}                  # parent_id -> list of (msg_or_None, info, follower_id)
    parents_to_rebuild = set()
    to_drop = []
    any_changes = False

    for fid, info in candidates:
        msg = fetch_message(info["channel_id"], fid)
        if not msg:
            continue  # rate-limited; try again next run
        info["last_checked"] = now_iso
        any_changes = True

        if msg is _DELETED:
            log(f"  x merged follower {fid} was deleted — removing from rebuild")
            parents_to_rebuild.add(info["parent_id"])
            to_drop.append(fid)
            continue

        current_edited = msg.get("edited_timestamp")
        if current_edited == info.get("edited_timestamp"):
            continue  # no change, nothing to do
        log(f"  ~ merged follower {fid} edited (was: {info.get('edited_timestamp')}, "
            f"now: {current_edited}) — parent {info['parent_id']} will rebuild")
        info["edited_timestamp"] = current_edited
        parents_to_rebuild.add(info["parent_id"])
        by_parent.setdefault(info["parent_id"], []).append((msg, info, fid))

    for fid in to_drop:
        merged_meta.pop(fid, None)

    if not parents_to_rebuild:
        return any_changes, 0

    # ── Pass 3: for each affected parent, fetch parent + ALL its followers
    # (in chronological order) and rebuild from scratch. We refetch followers
    # we just fetched too — keeps the rebuild logic uniform and the API cost
    # is bounded by how many parents we touch (typically 0–3 per run).
    rebuilt = 0
    parent_by_id = {e["id"]: e for e in events_data.get("events", [])}
    for parent_id in parents_to_rebuild:
        parent_event = parent_by_id.get(parent_id)
        if not parent_event:
            log(f"  parent {parent_id} not in events.json — skipping rebuild")
            continue
        parent_channel = parent_event.get("channel_id") or \
                         (event_meta.get(parent_id) or {}).get("channel_id")
        if not parent_channel:
            log(f"  parent {parent_id} has no channel_id — skipping rebuild")
            continue

        parent_msg = fetch_message(parent_channel, parent_id)
        if not parent_msg or parent_msg is _DELETED:
            log(f"  parent {parent_id} unfetchable — skipping rebuild")
            continue

        # Gather all known followers of this parent.
        follower_entries = []
        for fid, info in merged_meta.items():
            if isinstance(info, dict) and info.get("parent_id") == parent_id and info.get("channel_id"):
                follower_entries.append((fid, info))
        follower_entries.sort(key=lambda kv: int(kv[0]))

        followers_msgs = []
        for fid, info in follower_entries:
            # Reuse already-fetched messages from the change-detection pass
            # to avoid duplicate API calls.
            existing = next((m for m, i, k in by_parent.get(parent_id, []) if k == fid), None)
            if existing:
                f_msg = existing
            else:
                f_msg = fetch_message(info["channel_id"], fid)
            followers_msgs.append((f_msg, info))

        if _rebuild_parent_from_followers(parent_event, parent_msg, followers_msgs):
            rebuilt += 1
            log(f"    rebuilt parent {parent_id}: "
                f"{len(parent_event.get('fullText') or '')}c, "
                f"{len(parent_event.get('images') or [])} images")

    return any_changes, rebuilt


def try_continuation_merge(msg, ch_id, new_events, events_data, event_meta):
    """If `msg` is a no-header follow-up posted within CONTINUATION_WINDOW of
    a recent event from the same author in the same channel, merge its content
    and images into that event.

    Returns the **merged-into event dict** on success (so callers can record
    the correct parent id in merged_meta without a separate lookup — the
    earlier "walk reversed(events_data) and grab the first same-author-same-
    channel match" approach didn't have a temporal upper bound and stamped
    the WRONG id when reconciling across long time spans).

    Returns None if no parent found or nothing useful to merge.

    Search order: this-run's `new_events` first (most recent in-flight), then
    events.json (older committed).
    """
    candidate = _find_recent_event_by_author(msg, ch_id, new_events, events_data, event_meta)
    if not candidate:
        return None
    content = (msg.get("content") or "").strip()
    images = _msg_images(msg)
    if not content and not images:
        return None
    if _merge_into(candidate, content, images):
        return candidate
    return None


def try_repeat_header_merge(msg, parsed, ch_id, new_events, events_data, event_meta):
    """If `msg` has a bracket header IDENTICAL (date + country + province +
    tag) to a recent same-author + same-channel event's header, treat it as a
    continuation rather than a new event. Mirrors player behaviour of
    repeating the bracket header on every chunk of a length-split post.

    Returns the parent event id when a merge happened, else None.

    Discriminator strength: identical-everything within 5 minutes is an
    extremely strong "this is the same logical post" signal — two genuinely-
    separate events with all four metadata fields identical, in the same
    channel, by the same author, within 5 min, is implausible in practice.
    The current parser was creating split events at this signature (Dods'
    Cologne, tintock's Trier, DerNette's Nancy, etc.); this closes the gap.
    """
    def _same_metadata(ev):
        return (
            ev.get("date")       == parsed["date"]       and
            ev.get("country")    == parsed["country"]    and
            (ev.get("countryRaw") or "") == (parsed["countryRaw"] or "") and
            ev.get("province")   == parsed["province"]   and
            ev.get("tag")        == parsed["tag"]
        )

    candidate = _find_recent_event_by_author(
        msg, ch_id, new_events, events_data, event_meta,
        extra_match=_same_metadata,
    )
    if not candidate:
        return None

    # Use cleaned body (header stripped) — matches what build_event does.
    content = (parsed.get("cleaned") or msg.get("content") or "").strip()
    images = _msg_images(msg)
    if not _merge_into(candidate, content, images):
        # Empty follow-up — still record it as a merge so we don't recreate
        # the bracket-header event on the next run.
        pass
    return candidate["id"]


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


def load_country_lookup(reference_dir):
    """Build {name|alias|tag (lowercase) -> TAG} from tags.json + country_aliases.json.

    Lets us resolve `[Country:Muscovy]` to the canonical tag `MOS` so the
    viewer doesn't grey out the country cell. Non-interactive: unknown names
    just stay unresolved (countryRaw only). Missing reference files are
    tolerated — returns whatever was loaded so the sync still runs.

    Mirrors the resolution chain in tools/preprocess.py (build_country_lookup)
    minus the interactive fuzzy-match prompts."""
    lookup = {}

    tags_path = os.path.join(reference_dir, "tags.json")
    tags = load_json(tags_path, {})
    if not tags:
        log(f"  (no country reference at {tags_path} — strict tag-only mode)")
        return lookup

    for tag, info in tags.items():
        if isinstance(info, str):
            name, aliases = info, []
        else:
            name    = info.get("name", "")
            aliases = info.get("aliases", []) or []
        if name:
            lookup[name.lower()] = tag
        for alias in aliases:
            if alias:
                lookup[alias.lower()] = tag
        lookup[tag.lower()] = tag

    # country_aliases.json is the source-of-truth that gets baked into tags.json
    # by parse_eu5_reference.py, but layering it again here means hand-edits to
    # country_aliases.json take effect on the next sync run without rebuilding.
    extras_path = os.path.join(reference_dir, "country_aliases.json")
    extras = load_json(extras_path, {})
    for tag, aliases in extras.items():
        if tag.startswith("_"):  # skip "_comment"
            continue
        for alias in (aliases or []):
            if alias:
                lookup[alias.lower()] = tag

    log(f"  Country lookup: {len(lookup)} name/alias entries loaded.")
    return lookup


def resolve_country(raw_name):
    """Return canonical tag for a raw country name, or None. Case-insensitive."""
    if not raw_name:
        return None
    return COUNTRY_LOOKUP.get(raw_name.strip().lower())


def apply_event_overrides(events_data, overrides_path):
    """Apply per-event manual overrides from event_overrides.json.

    This is the escape hatch for fixing posts whose authors aren't going to
    retro-edit them on Discord — most commonly mis-applied `tag` values.
    Overrides ALWAYS win, including over the rebuild that fires when a Discord
    post is edited within the 7-day lookback window, so a single edit on the
    source post can't accidentally undo a correction.

    File schema:
        {
          "overrides": {
            "<message_id>": { "tag": "Death", "_note": "optional comment", ... },
            ...
          }
        }
    Keys starting with `_` (including `_comment`, `_was`, `_note`) are ignored.
    Tag values are validated against assets/event-tags.json's canonical set —
    invalid tags log a warning and are skipped so a typo can't quietly corrupt
    events.json.

    Returns True iff at least one event was actually mutated. Missing file is
    a no-op (returns False). Unknown message IDs log a warning and are skipped
    rather than failing the run — old overrides for since-deleted events stay
    harmless."""
    raw = load_json(overrides_path, None)
    if not raw:
        return False
    overrides = raw.get("overrides") or {}
    if not overrides:
        return False

    by_id = {e["id"]: e for e in events_data.get("events", [])}
    mutated = 0
    skipped_unknown = 0
    skipped_invalid_tag = 0

    for ev_id, patch in overrides.items():
        if not isinstance(patch, dict):
            continue
        target = by_id.get(ev_id)
        if not target:
            skipped_unknown += 1
            continue
        for field, value in patch.items():
            if field.startswith("_"):
                continue  # comment/audit fields — never copied to events.json
            if field == "tag" and value is not None:
                # VALID_TAGS is an alias→canonical map (lowercase keys). Look
                # the override value up the same way the parser does so users
                # can write either the canonical name or any registered alias,
                # case-insensitively. The CANONICAL form gets stored.
                canonical = VALID_TAGS.get(str(value).strip().lower())
                if not canonical:
                    log(f"  ! override for {ev_id}: tag {value!r} not in canonical "
                        f"tag list — skipping (fix assets/event-tags.json or "
                        f"the override).")
                    skipped_invalid_tag += 1
                    continue
                value = canonical
            if target.get(field) != value:
                target[field] = value
                mutated += 1

    if mutated or skipped_unknown or skipped_invalid_tag:
        msg = f"Overrides: {mutated} field change(s) applied"
        if skipped_unknown:
            msg += f", {skipped_unknown} unknown id(s)"
        if skipped_invalid_tag:
            msg += f", {skipped_invalid_tag} invalid tag(s)"
        log(msg + ".")
    return mutated > 0


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
            events_data["events"][i] = build_event(msg, parsed, thread_title=thread_title, channel_id=channel_id)
            log(f"    updated: [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            log(f"    tags removed after edit — keeping old entry unchanged")

        updated = True

    for i in reversed(to_delete):
        events_data["events"].pop(i)

    return updated


def check_rejected_for_promotions(rejected_meta, event_meta, now_utc, thread_names=None, skip_ids=None,
                                   events_data=None, merged_meta=None):
    """Re-fetch each rejected message inside the 7-day window; promote any that
    now parse cleanly into events.

    Catches the case where someone posts an event without tags, then edits the
    brackets in later. Without this pass, the main scan loop would skip the
    edited message via dedup (it's in rejected_meta with the old edited_timestamp),
    AND check_edits_and_deletions wouldn't see it either (only iterates events
    already in events.json). This closes that hole.

    `skip_ids` is the set of message IDs the scan loop already processed in
    this run. Re-fetching them would double the API cost on backfills (the
    cursor scan covers them, the promotion check would re-cover them) — they
    already have the latest edited_timestamp recorded fresh, so the promotion
    check is a no-op for them anyway.

    Returns (new_events, any_changes) — any_changes covers prunes & status
    updates even when no promotions actually happened, so the cache gets saved.
    """
    thread_names = thread_names or {}
    skip_ids     = skip_ids or set()
    cutoff       = now_utc - timedelta(days=EDIT_LOOKBACK_DAYS)
    now_iso      = now_utc.isoformat()
    new_events   = []
    to_drop      = []
    any_changes  = False

    # ── Pass 1: prune stale entries (no API calls). ───────────────────────────
    for msg_id, meta in rejected_meta.items():
        try:
            posted_at = snowflake_to_dt(msg_id)
        except (ValueError, TypeError):
            to_drop.append(msg_id)
            any_changes = True
            continue
        if posted_at < cutoff:
            to_drop.append(msg_id)
            any_changes = True
            continue
        if not meta.get("channel_id"):
            to_drop.append(msg_id)
            any_changes = True
            continue
    for msg_id in to_drop:
        rejected_meta.pop(msg_id, None)
    to_drop = []

    # ── Pass 2: pick candidates, cap to N least-recently-checked. ─────────────
    # Entries with no `last_checked` (just-rejected or pre-cap-feature) sort
    # first, then by oldest-checked ascending. Skip anything the scan loop
    # already touched this run — its stored edited_timestamp is already fresh.
    def _last_checked_key(item):
        meta = item[1]
        lc = meta.get("last_checked")
        # (has_been_checked?, last_checked_iso) — never-checked sorts before
        # any checked entries; checked entries sort by oldest-first.
        return (lc is not None, lc or "")

    candidates = sorted(
        ((mid, m) for mid, m in rejected_meta.items() if mid not in skip_ids),
        key=_last_checked_key,
    )
    # RECONCILE_REJECTIONS=true lifts the per-run cap. Used for a one-shot
    # backfill of the existing rejected-meta backlog after we add new
    # reconciliation logic (e.g. missed-continuation detection) and want to
    # apply it to all historical entries in a single workflow run instead of
    # waiting many cron cycles for the rotation to cover them.
    cap = MAX_PROMOTION_CHECK_PER_RUN
    reconcile_mode = os.environ.get("RECONCILE_REJECTIONS") == "true"
    if reconcile_mode:
        cap = None
        log("  RECONCILE_REJECTIONS=true — promotion-check cap lifted for this run.")

    # In reconcile mode we pre-filter to only fetch entries that COULD
    # plausibly merge as a continuation — most rejected messages are random
    # chatter nowhere near an event, and at 672 entries × Discord rate limits
    # the workflow runs out of time before getting to the real candidates.
    # The pre-filter is purely local (no API) and uses (channel + timing) —
    # if no event exists in the same channel within CONTINUATION_WINDOW
    # before this rejected message, there's nothing to merge into.
    if reconcile_mode and events_data is not None:
        before = len(candidates)
        candidates = [
            (mid, m) for mid, m in candidates
            if _has_plausible_parent(mid, m.get("channel_id"), events_data, event_meta)
        ]
        log(f"  Pre-filter: {before} → {len(candidates)} candidate(s) with a "
            f"plausible same-channel parent within {int(CONTINUATION_WINDOW.total_seconds())}s.")

    if cap is not None and len(candidates) > cap:
        log(f"  Rejected_meta has {len(candidates)} candidate(s); capping check at "
            f"{cap} (least-recently-checked first).")
        candidates = candidates[:cap]

    # ── Pass 3: refetch + promote/update edited_timestamp. ───────────────────
    for msg_id, meta in candidates:
        channel_id    = meta["channel_id"]
        stored_edited = meta.get("edited_timestamp")

        msg = fetch_message(channel_id, msg_id)

        if msg is _DELETED:
            log(f"  x rejected {msg_id} was deleted — removing from rejected_meta")
            to_drop.append(msg_id)
            any_changes = True
            continue
        if not msg:
            # Fetch failed (rate-limited / timeout); leave last_checked alone
            # so this entry stays at the front of the rotation next run.
            continue

        # Successful API call — mark the rotation timestamp regardless of
        # outcome, so we move on to the next entry on subsequent runs.
        meta["last_checked"] = now_iso
        any_changes = True

        current_edited = msg.get("edited_timestamp")
        parsed = parse_event_tags(msg.get("content", ""))

        # ── Missed-continuation reconciliation ──────────────────────────────
        # A rejected message that has NO bracket header MIGHT actually be a
        # follow-up of a recent same-author + same-channel event. The scan
        # loop normally catches this via try_continuation_merge, but messages
        # rejected before that logic existed (or in any other ordering edge
        # case) end up stuck in rejected_meta. Re-evaluate them here using the
        # full message we just fetched.
        #
        # No extra API cost — we already paid the fetch above.
        if not parsed and events_data is not None and merged_meta is not None:
            merged_parent = try_continuation_merge(msg, channel_id, new_events, events_data, event_meta)
            if merged_parent is not None:
                # try_continuation_merge returns the merge-target dict.
                # Store the dict shape so the follower-edit-detection pass
                # can later refetch this follower.
                merged_meta[msg_id] = {
                    "parent_id":        merged_parent["id"],
                    "channel_id":       channel_id,
                    "edited_timestamp": current_edited,
                }
                to_drop.append(msg_id)
                log(f"  ~ reconciled rejected {msg_id} as continuation of {merged_parent['id']}")
                continue

        # ── Edit-detection promotion ────────────────────────────────────────
        # If edited_timestamp hasn't changed since last evaluation, no point
        # re-parsing — the content is unchanged.
        if current_edited == stored_edited:
            continue

        log(f"  ~ rejected {msg_id} was edited (was: {stored_edited}, now: {current_edited})")

        if parsed:
            thread_title = thread_names.get(channel_id) if channel_id != CHANNEL_ID else None
            event = build_event(msg, parsed, thread_title=thread_title, channel_id=channel_id)
            new_events.append(event)
            event_meta[msg_id] = {
                "channel_id":       channel_id,
                "edited_timestamp": current_edited,
            }
            to_drop.append(msg_id)
            log(f"    PROMOTED to event: [{parsed['date']}] {parsed['province']} ({parsed['tag']})")
        else:
            # Still no valid tags, but record the new edited_timestamp so we
            # don't keep flagging this as an edit on every subsequent run.
            meta["edited_timestamp"] = current_edited
            log(f"    still no valid tags after edit")

    for msg_id in to_drop:
        rejected_meta.pop(msg_id, None)

    return new_events, any_changes


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


def load_campaign_config(folder):
    """Look up the campaign entry in <repo-root>/campaigns.json by folder name
    and validate it has `discord_sync.enabled: true` + a `channel_id`."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    campaigns_path = os.path.join(repo_root, "campaigns.json")
    if not os.path.exists(campaigns_path):
        sys.exit(f"ERROR: campaigns.json not found at {campaigns_path}")
    with open(campaigns_path, encoding="utf-8") as f:
        manifest = json.load(f)
    for entry in manifest.get("campaigns", []):
        if entry.get("folder") == folder:
            sync_cfg = entry.get("discord_sync") or {}
            if not sync_cfg.get("enabled"):
                sys.exit(f"ERROR: campaign {folder!r} has no discord_sync.enabled in campaigns.json")
            if not sync_cfg.get("channel_id"):
                sys.exit(f"ERROR: campaign {folder!r} missing discord_sync.channel_id")
            game = sync_cfg.get("reference_game") or entry.get("game")
            return {
                "folder":     folder,
                "channel_id": sync_cfg["channel_id"],
                "game":       game,
                "repo_root":  repo_root,
            }
    sys.exit(f"ERROR: campaign {folder!r} not found in campaigns.json")


def main():
    ap = argparse.ArgumentParser(description="Discord Events Sync")
    ap.add_argument("--campaign", default=os.environ.get("SYNC_CAMPAIGN", ""),
                    help="Campaign folder name (matched against campaigns.json). "
                         "Required. Resolves channel_id + per-campaign paths.")
    ap.add_argument("--since", default=os.environ.get("SYNC_SINCE", ""),
                    help="Override the cursor and backfill from this date "
                         "(YYYY-MM-DD or full ISO). One-shot — the cursor "
                         "moves forward after the run as normal.")
    ap.add_argument("--until", default=os.environ.get("SYNC_UNTIL", ""),
                    help="Upper bound for this run's scan window (YYYY-MM-DD "
                         "or full ISO). Messages newer than --until are ignored "
                         "and the cursor stops there. Used by the workflow's "
                         "chunked-backfill mode to break a big window into "
                         "smaller, rate-limit-friendly pieces.")
    ap.add_argument("--debug-fetch", default=os.environ.get("DEBUG_FETCH", ""),
                    help="One-shot diagnostic: fetch a specific message by "
                         "`channel_id:message_id` (or full Discord URL) and "
                         "print its raw content + regex match details to the "
                         "workflow log. Useful when a post 'should' parse but "
                         "the strict tag regex is rejecting it. Sync still "
                         "runs normally afterwards.")
    args = ap.parse_args()

    if not args.campaign:
        sys.exit("ERROR: --campaign is required (or set SYNC_CAMPAIGN env). "
                 "Example: --campaign darthsunday")

    # Resolve campaign config and populate the module-level path constants.
    cfg = load_campaign_config(args.campaign)
    global CHANNEL_ID, EVENTS_FILE, CACHE_FILE, REFERENCE_DIR, OVERRIDES_FILE
    CHANNEL_ID     = cfg["channel_id"]
    EVENTS_FILE    = f"{cfg['folder']}/data/events.json"
    CACHE_FILE     = f"{cfg['folder']}/data/processed_ids.json"
    OVERRIDES_FILE = f"{cfg['folder']}/data/event_overrides.json"
    REFERENCE_DIR  = f"assets/reference/{cfg['game']}"
    log(f"=== Campaign: {cfg['folder']} (channel={CHANNEL_ID}, game={cfg['game']}) ===")

    # ── Debug fetch ───────────────────────────────────────────────────────────
    # Lets us inspect what the API actually returns for a problem message,
    # since the normal "no valid tags" log line truncates content to 80 chars.
    if args.debug_fetch:
        spec = args.debug_fetch.strip()
        # Accept full Discord message URLs: .../channels/{guild}/{channel}/{msg}
        if "/channels/" in spec:
            parts = spec.rstrip("/").split("/")
            ch_id, msg_id = parts[-2], parts[-1]
        elif ":" in spec:
            ch_id, msg_id = spec.split(":", 1)
        else:
            log(f"  DEBUG_FETCH format error: expected `channel:message` or URL, got {spec!r}")
            ch_id = msg_id = None

        if ch_id and msg_id:
            log(f"=== DEBUG FETCH: channel={ch_id} message={msg_id} ===")
            msg = fetch_message(ch_id, msg_id)
            if msg is None:
                log("  (fetch failed — 403/timeout)")
            elif msg is _DELETED:
                log("  (message deleted)")
            else:
                content = msg.get("content", "")
                log(f"  author: {msg.get('author', {}).get('username')!r} "
                    f"(global_name={msg.get('author', {}).get('global_name')!r})")
                log(f"  edited: {msg.get('edited_timestamp')}")
                log(f"  content length: {len(content)} chars")
                log(f"  attachments: {len(msg.get('attachments') or [])}")
                log("  --- RAW CONTENT (repr, so escapes are visible) ---")
                # Print in chunks; workflow log lines can get long but repr() is fine.
                log(f"  {content!r}")
                log("  --- END RAW CONTENT ---")
                match = TAG_RE.search(content)
                if match:
                    log(f"  REGEX MATCH at offset {match.start()}-{match.end()}: {match.group(0)!r}")
                    log(f"    groups: {match.groups()!r}")
                else:
                    log("  REGEX MATCH: NONE — content does not contain "
                        "`[Date:YYYY-MM-DD][Country:...][Location:...]` in the strict format.")
                    # Show what bracket-like content is present, if any.
                    found = re.findall(r"\[[^\]]{1,60}\]", content)
                    if found:
                        log(f"  Bracket-like substrings found ({len(found)}):")
                        for b in found[:20]:
                            log(f"    {b!r}")
            log("=== END DEBUG FETCH ===")

    now_utc   = datetime.now(tz=timezone.utc)
    today_utc = now_utc.date()
    today_str = today_utc.isoformat()

    # Load the country-name -> tag lookup once. Failure is non-fatal: the sync
    # then falls back to strict tag-or-raw (countryRaw only for free-form names).
    global COUNTRY_LOOKUP
    COUNTRY_LOOKUP = load_country_lookup(REFERENCE_DIR)

    cache           = load_json(CACHE_FILE, {})
    # event_meta:    {msg_id: {channel_id, edited_timestamp}} for messages that
    #                parsed cleanly and are in events.json. Used by edit-detection.
    # rejected_meta: same shape, for messages that failed parsing. Used by the
    #                edit-detection-equivalent pass that promotes posts after
    #                their author edits valid tags in. Both pruned to 7 days.
    event_meta      = cache.get("event_meta", {})
    rejected_meta   = cache.get("rejected_meta", {})
    # merged_meta:   {follow_up_msg_id: {parent_id, channel_id, edited_timestamp}}
    #                for messages whose content was merged into a same-author
    #                event via the continuation window. The channel_id +
    #                edited_timestamp let us re-fetch each follower in
    #                check_merged_followers_for_edits, so edits or deletions
    #                of a merged follow-up propagate back into the parent
    #                event without requiring the author to edit the parent.
    merged_meta     = cache.get("merged_meta", {})
    events_data     = load_json(EVENTS_FILE, {"events": []})
    existing_ids    = {e["id"] for e in events_data.get("events", [])}

    # Schema migration: legacy entries stored only the parent_id string. Pad
    # them to the dict shape so the new edit-check pass can refetch them. The
    # follower lives in the same channel as its parent (that's the match
    # rule), so we lift channel_id off the parent event. edited_timestamp is
    # unknown for legacy entries — set None; first refetch records the real
    # value.
    migrated_meta = 0
    parent_by_id = {e["id"]: e for e in events_data.get("events", [])}
    for follow_id, info in list(merged_meta.items()):
        if isinstance(info, dict) and "parent_id" in info:
            continue
        parent_id = info if isinstance(info, str) else ""
        parent_event = parent_by_id.get(parent_id)
        channel_id = ""
        if parent_event:
            channel_id = parent_event.get("channel_id") or \
                         (event_meta.get(parent_id) or {}).get("channel_id") or ""
        merged_meta[follow_id] = {
            "parent_id":        parent_id,
            "channel_id":       channel_id,
            "edited_timestamp": None,
        }
        migrated_meta += 1
    if migrated_meta:
        log(f"Migrated {migrated_meta} merged_meta entry(ies) to the dict schema.")

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

    # --until upper bound for chunked backfills. Cursor will be pinned here at
    # the end of the run so the next chunk picks up exactly where we left off.
    until_dt = parse_since(args.until)
    until_snowflake = None
    if until_dt is not None:
        until_snowflake = date_to_snowflake(until_dt)
        log(f"  Chunk upper bound (--until): {until_dt.isoformat()}")

    channel_info = api_get(f"/channels/{CHANNEL_ID}")
    if not channel_info:
        log("ERROR: Could not fetch channel info.")
        sys.exit(1)
    guild_id = channel_info.get("guild_id")
    log(f"Guild ID: {guild_id}")

    log("Fetching active threads ...")
    active_threads = fetch_active_threads(guild_id)
    log(f"  {len(active_threads)} active thread(s).")

    log("Fetching archived public threads ...")
    archived_threads = fetch_archived_public_threads(CHANNEL_ID)
    log(f"  {len(archived_threads)} archived thread(s).")

    # Dedupe by id in case a thread appears in both lists (defensive — they
    # shouldn't overlap per Discord's API contract, but cost is one set op).
    seen_thread_ids = set()
    all_threads = []
    for t in active_threads + archived_threads:
        tid = t.get("id")
        if tid and tid not in seen_thread_ids:
            seen_thread_ids.add(tid)
            all_threads.append(t)

    # Map channel/thread ID -> thread name. Used as the event title when the
    # author posts inside a named thread (Discord threads carry the title even
    # if the message body has no `# Heading` or `**Bold**` line). Built before
    # filtering so promotion-check can still look up names for old threads.
    thread_names = {t["id"]: t.get("name", "") for t in all_threads if t.get("name")}

    # Filter threads with no messages newer than the cursor — saves an empty
    # `/messages?after=...` API call per dead thread. Each archived thread
    # listing includes `last_message_id`; threads whose newest message predates
    # the cursor have nothing to give us this run. (`None` last_message_id =
    # empty thread, skip; missing field = unknown, keep to be safe.)
    cursor_int = int(after_snowflake)
    def _has_new_messages(thread):
        if "last_message_id" not in thread:
            return True  # API didn't tell us; scan to find out.
        lmi = thread.get("last_message_id")
        if lmi is None:
            return False  # empty thread
        try:
            return int(lmi) > cursor_int
        except (TypeError, ValueError):
            return True  # malformed; be permissive
    skipped = sum(1 for t in all_threads if not _has_new_messages(t))
    if skipped:
        log(f"  Skipping {skipped} thread(s) with no messages newer than cursor.")
    threads_to_scan = [t for t in all_threads if _has_new_messages(t)]

    channels_to_scan = [CHANNEL_ID] + [t["id"] for t in threads_to_scan]

    # Collect messages tagged with which channel they came from
    all_messages = []
    for ch_id in channels_to_scan:
        label = "main channel" if ch_id == CHANNEL_ID else f"thread {ch_id}"
        msgs  = fetch_messages_since(ch_id, after_snowflake, before_snowflake=until_snowflake)
        log(f"  {len(msgs)} message(s) from {label}")
        for msg in msgs:
            all_messages.append((msg, ch_id))

    log(f"Total messages to evaluate: {len(all_messages)}")

    # Advance the cursor to the highest message ID we saw — guarantees forward
    # progress even on quiet days. For chunked backfills, also pin the cursor
    # to --until's snowflake so the *next* chunk starts there even when this
    # chunk happened to contain no messages.
    prev_cursor = int(cache.get("last_snowflake") or 0)
    highest_seen = max((int(m[0]["id"]) for m in all_messages), default=0)
    new_cursor = max(prev_cursor, highest_seen)
    if until_snowflake is not None and int(until_snowflake) > new_cursor:
        new_cursor = int(until_snowflake)
    if new_cursor > prev_cursor:
        cache["last_snowflake"] = str(new_cursor)

    new_events = []
    seen       = set()
    merges_found = False

    for msg, ch_id in all_messages:
        msg_id = msg["id"]
        if msg_id in seen:
            continue
        seen.add(msg_id)

        # Events-already-in-events.json get edit-handled by check_edits_and_deletions.
        if msg_id in existing_ids:
            continue

        # Already merged into another event as a continuation follow-up;
        # don't re-merge (would duplicate content on backfills).
        if msg_id in merged_meta:
            continue

        current_edited = msg.get("edited_timestamp")

        # If we've already evaluated this message AND its edited_timestamp
        # hasn't changed since, skip silently. If it HAS changed (or the
        # message is new to us), fall through and re-parse — this is what
        # lets late-added tag edits get promoted to events.
        prior = rejected_meta.get(msg_id)
        if prior and prior.get("edited_timestamp") == current_edited:
            continue

        parsed = parse_event_tags(msg.get("content", ""))
        if parsed:
            # Before treating this as a new event, check if it's actually a
            # repeat-header continuation — same author + channel + identical
            # (date, country, province, tag) within CONTINUATION_WINDOW of a
            # recent event. Players who length-split a post often repeat the
            # bracket header on every chunk, which would otherwise create N
            # phantom events.
            repeat_parent = try_repeat_header_merge(
                msg, parsed, ch_id, new_events, events_data, event_meta,
            )
            if repeat_parent:
                merged_meta[msg_id] = {
                    "parent_id":        repeat_parent,
                    "channel_id":       ch_id,
                    "edited_timestamp": current_edited,
                }
                rejected_meta.pop(msg_id, None)
                merges_found = True
                log(f"  ~ {msg_id} merged as repeat-header continuation of {repeat_parent} "
                    f"[{parsed['date']}] {parsed['province']} ({parsed['tag']})")
                continue

            thread_title = thread_names.get(ch_id) if ch_id != CHANNEL_ID else None
            new_events.append(build_event(msg, parsed, thread_title=thread_title, channel_id=ch_id))
            event_meta[msg_id] = {
                "channel_id":       ch_id,
                "edited_timestamp": current_edited,
            }
            # If this message was previously rejected, graduate it out of the
            # rejected pool so subsequent runs use the event_meta entry.
            promoted = rejected_meta.pop(msg_id, None) is not None
            log(f"  {'^' if promoted else '+'} {msg_id} "
                f"[{parsed['date']}] {parsed['province']} ({parsed['tag']})"
                f"{' — promoted after edit' if promoted else ''}")
        else:
            # No bracket header — try to merge as a continuation of a same-author
            # event in the same channel posted within CONTINUATION_WINDOW. This
            # is how multi-message posts (length splits, image follow-ups, etc.)
            # get glued back into one event.
            merged_parent = try_continuation_merge(msg, ch_id, new_events, events_data, event_meta)
            if merged_parent is not None:
                merged_meta[msg_id] = {
                    "parent_id":        merged_parent["id"],
                    "channel_id":       ch_id,
                    "edited_timestamp": current_edited,
                }
                rejected_meta.pop(msg_id, None)  # not rejected — it was absorbed
                merges_found = True
                log(f"  ~ {msg_id} merged as continuation of {merged_parent['id']} "
                    f"({(msg.get('content') or '')[:60]!r})")
            else:
                rejected_meta[msg_id] = {
                    "channel_id":       ch_id,
                    "edited_timestamp": current_edited,
                }
                log(f"  - {msg_id} (no valid tags) | {repr(msg.get('content', '')[:80])}")

    # ── Edit check ────────────────────────────────────────────────────────────
    log("Checking recent events for edits/deletions ...")
    edits_found = check_edits_and_deletions(events_data, event_meta, now_utc, thread_names)
    if not edits_found:
        log("  No event changes found.")

    # ── Follower-edit check ──────────────────────────────────────────────────
    # Detect edits/deletions on continuation messages (length-split follow-ups
    # that were merged into a parent event). Without this, an edit on chunk 2
    # of a 3-message post would be invisible.
    log("Checking merged followers for edits/deletions ...")
    follower_changes, parents_rebuilt = check_merged_followers_for_edits(
        merged_meta, event_meta, events_data, now_utc,
    )
    if parents_rebuilt:
        log(f"  Rebuilt {parents_rebuilt} parent event(s) after follower edits.")
    elif not follower_changes:
        log("  No follower changes found.")

    # ── Promotion check: rejected messages whose edits added valid tags ──────
    # This is the symmetric pass to edit-detection: it covers the case where a
    # post had no brackets when first scanned, then was edited later to add
    # them. Without it, those edits would be invisible because the main scan
    # loop only sees messages newer than the cursor.
    log("Checking recent rejections for late-added tags + missed continuations ...")
    promoted_events, promotions_found = check_rejected_for_promotions(
        rejected_meta, event_meta, now_utc, thread_names, skip_ids=seen,
        events_data=events_data, merged_meta=merged_meta,
    )
    if promoted_events:
        new_events.extend(promoted_events)
        log(f"  Promoted {len(promoted_events)} rejected post(s) to events.")
    elif not promotions_found:
        log("  No rejection changes found.")
    # merges_found wasn't true at scan-loop time but the reconciliation pass
    # may have just mutated events_data — set the flag so the cache + events
    # file gets saved.
    if promotions_found:
        merges_found = True

    # ── Persist ───────────────────────────────────────────────────────────────
    # `merges_found` covers continuations merged into events.json events — those
    # mutations don't show up in new_events but still need a commit.
    if new_events:
        events_data["events"].extend(new_events)

    # Apply manual overrides AFTER new events are merged in so they cover both
    # freshly-built and pre-existing events in the same pass. They also re-fire
    # every run, which means if check_edits_and_deletions just rebuilt an
    # overridden event from a fresh Discord fetch (clobbering the override),
    # this restores it. Idempotent: returns True only if something actually
    # changed.
    log("Applying manual event overrides ...")
    overrides_changed = apply_event_overrides(events_data, OVERRIDES_FILE)

    had_changes = (bool(new_events) or edits_found or promotions_found or
                   merges_found or follower_changes or overrides_changed)

    if had_changes:
        save_json(EVENTS_FILE, events_data)
        log(f"Saved events.json ({len(new_events)} new, edits={edits_found}, "
            f"promotions={bool(promoted_events)}, merges={merges_found}, "
            f"overrides={overrides_changed}).")
    else:
        log("No changes this run.")

    # Prune all three metas to the 7-day window — older entries are out of the
    # edit/continuation detection range, so re-fetching them is wasted work.
    cutoff_dt = now_utc - timedelta(days=EDIT_LOOKBACK_DAYS)

    def _prune_dict(meta):
        out = {}
        for k, v in meta.items():
            try:
                if snowflake_to_dt(k) >= cutoff_dt:
                    out[k] = v
            except (ValueError, TypeError):
                pass  # skip garbage entries
        return out

    event_meta    = _prune_dict(event_meta)
    rejected_meta = _prune_dict(rejected_meta)
    merged_meta   = _prune_dict(merged_meta)

    cache["event_meta"]    = event_meta
    cache["rejected_meta"] = rejected_meta
    cache["merged_meta"]   = merged_meta

    # Keep only the canonical top-level keys so the cache file doesn't drift in
    # shape over time as we add or rename internal state.
    cache = {
        k: v for k, v in cache.items()
        if k in ("event_meta", "rejected_meta", "merged_meta", "last_snowflake")
    }
    save_json(CACHE_FILE, cache)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"had_changes={'true' if had_changes else 'false'}\n")

    return had_changes


if __name__ == "__main__":
    main()
