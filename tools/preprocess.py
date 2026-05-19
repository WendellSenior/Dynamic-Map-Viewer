"""Discord export -> data/events.json. Tolerant header parser + interactive country resolution."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from difflib import get_close_matches
from pathlib import Path

MONTH_NAMES = {
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

CONTINUATION_WINDOW = timedelta(minutes=5)
COUNTRY_CANDIDATE_RE = re.compile(r"^[A-Za-z][A-Za-z\s\-'’]{0,30}$")


def parse_date(s):
    """Multi-format historical date parser. Returns ISO 'YYYY-MM-DD' or None."""
    s = s.strip().lower()
    # 1492-04-26 (canonical)
    m = re.fullmatch(r"(\d{3,4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}"
    # 11_Nov_1444 or 4 Mar 1453
    m = re.fullmatch(r"(\d{1,2})[_\s]+([a-z]+)[_\s]+(\d{3,4})", s)
    if m and m[2] in MONTH_NAMES:
        return f"{int(m[3]):04d}-{MONTH_NAMES[m[2]]:02d}-{int(m[1]):02d}"
    # 26_4_1492
    m = re.fullmatch(r"(\d{1,2})[_\s]+(\d{1,2})[_\s]+(\d{3,4})", s)
    if m and 1 <= int(m[2]) <= 12:
        return f"{int(m[3]):04d}-{int(m[2]):02d}-{int(m[1]):02d}"
    # 13th December 1513
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{3,4})", s)
    if m and m[2] in MONTH_NAMES:
        return f"{int(m[3]):04d}-{MONTH_NAMES[m[2]]:02d}-{int(m[1]):02d}"
    # 1673 May 10
    m = re.fullmatch(r"(\d{3,4})\s+([a-z]+)\s+(\d{1,2})", s)
    if m and m[2] in MONTH_NAMES:
        return f"{int(m[1]):04d}-{MONTH_NAMES[m[2]]:02d}-{int(m[3]):02d}"
    # 1563 (year only) — sane historical range only
    m = re.fullmatch(r"(\d{3,4})", s)
    if m:
        y = int(m[1])
        if 1000 <= y <= 2999:
            return f"{y:04d}-01-01"
    return None


def parse_header(content):
    """Extract date + raw country candidate from message head. Returns (date, candidate, body)."""
    lines = content.split("\n")
    first_idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first_idx is None:
        return None, None, ""

    date = parse_date(lines[first_idx].strip())
    if not date:
        return None, None, content.strip()

    consumed = first_idx + 1
    candidate = None
    for i in range(consumed, min(consumed + 3, len(lines))):
        line = lines[i].strip()
        if not line:
            consumed = i + 1
            continue
        if line.startswith("#"):
            break
        if COUNTRY_CANDIDATE_RE.fullmatch(line):
            candidate = line
            consumed = i + 1
        break

    body = "\n".join(lines[consumed:]).strip()
    return date, candidate, body


def extract_messages_from_html(path):
    """Pull the embedded `const messages = [...]` array out of DCE-style HTML."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"const\s+messages\s*=\s*(\[.*?\]);", text, re.DOTALL)
    if not m:
        raise SystemExit(f"Could not find embedded messages array in {path}")
    return json.loads(m.group(1))


def extract_messages_from_json(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("messages", [])
    return data


RAW_TAG_RE = re.compile(r'^(?P<tag>[A-Z][A-Z0-9]{1,3})\s*=\s*"countries/(?P<filename>[^"]+?)\.txt"\s*$')


def _camelcase_split(s):
    return re.sub(r"(?<!^)(?=[A-Z])", " ", s).strip()


def load_raw_eu4_tags(path):
    """Parse EU4's `00_countries.txt`: TAG = "countries/Name.txt". Returns {tag: {name, aliases}}."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = RAW_TAG_RE.match(line)
        if not m:
            continue
        tag = m["tag"]
        filename = m["filename"].strip()
        spaced = _camelcase_split(filename)
        aliases = []
        if spaced != filename:
            aliases.append(filename)
        out[tag] = {"name": spaced, "aliases": aliases}
    return out


def load_country_tags(json_path, raw_path=None):
    """Raw EU4 tag file is the base; JSON file layers curated overrides + extra aliases on top."""
    base = load_raw_eu4_tags(raw_path) if raw_path else {}
    if json_path and json_path.exists():
        overrides = json.loads(json_path.read_text(encoding="utf-8"))
        for tag, info in overrides.items():
            if isinstance(info, str):
                info = {"name": info, "aliases": []}
            existing = base.get(tag, {"name": info["name"], "aliases": []})
            merged_aliases = list(dict.fromkeys(
                [*existing["aliases"], *info.get("aliases", [])]
            ))
            base[tag] = {
                "name": info.get("name") or existing["name"],
                "aliases": merged_aliases,
            }
    return base


def build_country_lookup(tags):
    lookup = {}
    for tag, info in tags.items():
        lookup[info["name"].lower()] = tag
        for alias in info["aliases"]:
            lookup[alias.lower()] = tag
        lookup[tag.lower()] = tag
    return lookup


def resolve_country(raw_name, lookup, tags, alias_cache, interactive):
    key = raw_name.strip().lower()
    if key in lookup:
        return lookup[key]
    if key in alias_cache:
        return alias_cache[key]
    if not interactive:
        return None
    candidates = get_close_matches(key, list(lookup.keys()), n=5, cutoff=0.6)
    print(f"\n? Unknown country: {raw_name!r}")
    if candidates:
        for i, c in enumerate(candidates, 1):
            print(f"   {i}. {c.title()}  ({lookup[c]})")
    print("   s. skip (leave as unknown)")
    print("   m. enter tag manually")
    while True:
        choice = input("   choice> ").strip().lower()
        if choice in ("", "s"):
            alias_cache[key] = None
            return None
        if choice == "m":
            entered = input("   tag (e.g. FRA)> ").strip().upper()
            if entered in tags:
                alias_cache[key] = entered
                return entered
            print("   not in tags file; try again or 's' to skip")
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            picked = candidates[int(choice) - 1]
            alias_cache[key] = lookup[picked]
            return lookup[picked]
        print("   ?")


def main():
    ap = argparse.ArgumentParser(description="Discord export -> events.json")
    ap.add_argument("input", type=Path, help="DiscordChatExporter HTML or JSON export")
    ap.add_argument("--out", type=Path, default=Path("data/events.json"))
    ap.add_argument("--tags", type=Path, default=Path("data/reference/eu4/tags.json"))
    ap.add_argument("--raw-tags", type=Path,
                    default=Path("data/reference/eu4/00_countries.txt"),
                    help="EU4 common/country_tags/00_countries.txt — primary tag source")
    ap.add_argument("--aliases", type=Path, default=Path("data/reference/eu4/aliases.json"))
    ap.add_argument("--untagged-log", type=Path, default=Path("data/untagged.log"))
    ap.add_argument("--non-interactive", action="store_true",
                    help="Skip prompts for unresolved countries (mark as null instead)")
    args = ap.parse_args()

    if args.input.suffix.lower() == ".html":
        messages = extract_messages_from_html(args.input)
    elif args.input.suffix.lower() == ".json":
        messages = extract_messages_from_json(args.input)
    else:
        raise SystemExit(f"Unsupported input format: {args.input.suffix}")

    tags = load_country_tags(args.tags, args.raw_tags)
    lookup = build_country_lookup(tags)
    alias_cache = (
        json.loads(args.aliases.read_text(encoding="utf-8"))
        if args.aliases.exists() else {}
    )

    events = []
    untagged = []
    last_event_by_author = {}  # author_id -> (event_ref, last_ts)

    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        author = msg.get("author") or {}
        author_name = author.get("global_name") or author.get("username") or "unknown"
        author_id = author.get("id") or author_name
        msg_id = msg.get("id") or hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
        ts_str = msg.get("timestamp")
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else None
        except ValueError:
            ts = None

        date, candidate, body = parse_header(content)

        if date is not None:
            country_tag = None
            if candidate:
                country_tag = resolve_country(
                    candidate, lookup, tags, alias_cache,
                    interactive=not args.non_interactive,
                )
                if country_tag is None:
                    # Couldn't resolve — put the line back into the body so it's not lost.
                    body = (candidate + ("\n\n" + body if body else "")).strip()
            event = {
                "id": msg_id,
                "date": date,
                "country": country_tag,
                "countryRaw": candidate if country_tag else None,
                "province": None,
                "author": author_name,
                "snippet": (body[:120] if body else ""),
                "fullText": body,
            }
            events.append(event)
            if ts:
                last_event_by_author[author_id] = (event, ts)
        else:
            prev = last_event_by_author.get(author_id)
            if prev and ts and (ts - prev[1]) <= CONTINUATION_WINDOW:
                ev, _ = prev
                ev["fullText"] = (ev["fullText"] + "\n\n" + content).strip() if ev["fullText"] else content
                ev["snippet"] = ev["fullText"][:120]
                last_event_by_author[author_id] = (ev, ts)
            else:
                untagged.append({
                    "id": msg_id,
                    "author": author_name,
                    "timestamp": ts_str,
                    "preview": content[:80],
                })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"events": events}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    args.aliases.parent.mkdir(parents=True, exist_ok=True)
    args.aliases.write_text(
        json.dumps(alias_cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if untagged:
        args.untagged_log.parent.mkdir(parents=True, exist_ok=True)
        args.untagged_log.write_text(
            "\n".join(
                f"[{u['timestamp']}] {u['author']}: {u['preview']}"
                for u in untagged
            ),
            encoding="utf-8",
        )

    print(f"Parsed {len(events)} events from {len(messages)} messages.")
    print(f"  events  -> {args.out}")
    print(f"  aliases -> {args.aliases}")
    if untagged:
        print(f"  {len(untagged)} unparseable message(s) -> {args.untagged_log}")


if __name__ == "__main__":
    main()
