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

TAG_ALIASES = {
    "WarDec":    ["wardec", "war", "warD", "declaration", "wardeclaration", "declare", "declarewar"],
    "Battle":    ["battle", "fight", "siege", "combat", "engagement", "war battle"],
    "Character": ["character", "person", "leader", "ruler", "monarch", "king", "queen",
                  "death", "birth", "succession", "heir", "marriage"],
    "Trade":     ["trade", "commerce", "merchant", "goods", "trade goods", "tradegoods"],
    "Economy":   ["economy", "economic", "money", "income", "tax", "taxation",
                  "debt", "finance", "wealth", "treasury", "coin"],
    "Discover":  ["discover", "discovery", "exploration", "explore", "colony",
                  "colonise", "colonize", "colonisation", "colonization", "expedition", "ship"],
    "Treaty":    ["treaty", "peace", "alliance", "agreement", "pact", "royalmarriage"],
    "Meeting":   ["meeting", "diplomacy", "diplo", "council", "conference", "summit",
                  "negotiation", "talks"],
    "History":   ["history", "historical", "background", "lore", "narration",
                  "general", "intro", "introduction", "context"],
    "Religion":  ["religion", "religious", "faith", "prayer", "worship", "spiritual"],
    "Catholic":  ["catholic", "christian", "christianity", "catholicism"],
    "Muslim":    ["muslim", "islam", "islamic", "sunni", "shia", "shiite", "sufi"],
    "Jewish":    ["jewish", "judaism", "jew", "hebrew"],
    "Taoism":    ["taoism", "tao", "taoist", "yinyang", "yin yang", "yin-yang",
                  "confucianism", "confucian"],
    "Orthodox":  ["orthodox", "orthodoxy", "eastern orthodox"],
    "Hindu":     ["hindu", "hinduism", "vedic", "vedanta"],
    "Buddhism":  ["dharma", "dharmic", "buddhism", "buddhist", "zen",
                  "theravada", "mahayana"],
}
TAG_LOOKUP = {}
for _canonical, _aliases in TAG_ALIASES.items():
    TAG_LOOKUP[_canonical.lower()] = _canonical
    for _alias in _aliases:
        TAG_LOOKUP[_alias.lower()] = _canonical

BRACKET_FIELD_RE = re.compile(r"\[(\w+)\s*:\s*([^\]]+)\]")

# Heuristics for "this looks like a new event attempt, even if the formal header parser failed."
COLON_KEYWORD_RE = re.compile(r"^\s*(date|country|location|tag)\s*[:=]", re.IGNORECASE)
INLINE_BRACKET_KEYWORD_RE = re.compile(r"\[(date|country|location|tag)\s*:", re.IGNORECASE)


def looks_like_new_event(content):
    """True when the post visibly attempts an event header (brackets, key:value, or markdown heading)."""
    for line in content.split("\n"):
        s = line.strip()
        if not s:
            continue
        if INLINE_BRACKET_KEYWORD_RE.search(s):
            return True
        if COLON_KEYWORD_RE.match(s):
            return True
        if s.startswith("#"):
            return True
        return False  # first non-empty line decides
    return False


def parse_date(s):
    """Multi-format historical date parser. Returns ISO 'YYYY-MM-DD' or None."""
    s = s.strip().lower()
    # 1492-04-26 / 1492.04.26 / 1492/04/26 (Y-M-D, bounds-checked)
    m = re.fullmatch(r"(\d{3,4})[./\-](\d{1,2})[./\-](\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    # 11_Nov_1444 / 4 Mar 1453 / 11-Nov-1444 / 1.Jan.1337
    m = re.fullmatch(r"(\d{1,2})[_\s./\-]+([a-z]+)[_\s./\-]+(\d{3,4})", s)
    if m and m[2] in MONTH_NAMES:
        return f"{int(m[3]):04d}-{MONTH_NAMES[m[2]]:02d}-{int(m[1]):02d}"
    # 26_4_1492 / 26.4.1492 / 26/4/1492 / 26-4-1492 (D-M-Y numeric)
    m = re.fullmatch(r"(\d{1,2})[_\s./\-]+(\d{1,2})[_\s./\-]+(\d{3,4})", s)
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


def parse_bracket_header(content):
    """Canonical format: `[Date:YYYY-MM-DD][Country:Name][Location:Name][Tag:Name]`.
    Brackets may span one line or multiple. Returns (fields_dict, body) or (None, None)."""
    lines = content.split("\n")
    fields = {}
    consumed = 0
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            if fields:
                consumed = i + 1
            continue
        matches = BRACKET_FIELD_RE.findall(line)
        leftover = BRACKET_FIELD_RE.sub("", line).strip()
        if matches and not leftover:
            for k, v in matches:
                fields[k.lower()] = v.strip()
            consumed = i + 1
        elif matches and leftover:
            for k, v in matches:
                fields[k.lower()] = v.strip()
            lines[i] = leftover
            consumed = i
            break
        else:
            break
    if "date" not in fields:
        return None, None
    body = "\n".join(lines[consumed:]).strip()
    return fields, body


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
    ap.add_argument("inputs", type=Path, nargs="+",
                    help="One or more DCE exports, or a directory containing them. "
                         "Messages from all sources are merged and sorted by timestamp.")
    ap.add_argument("--out", type=Path, default=Path("data/events.json"))
    ap.add_argument("--tags", type=Path, default=Path("data/reference/eu4/tags.json"))
    ap.add_argument("--raw-tags", type=Path, default=None,
                    help="EU4 common/country_tags/00_countries.txt (optional; layered under --tags)")
    ap.add_argument("--aliases", type=Path, default=Path("data/reference/eu4/aliases.json"))
    ap.add_argument("--untagged-log", type=Path, default=Path("data/untagged.log"))
    ap.add_argument("--non-interactive", action="store_true",
                    help="Skip prompts for unresolved countries (mark as null instead)")
    args = ap.parse_args()

    # Expand inputs: directories → all *.html / *.json children. Files → themselves.
    input_files = []
    for p in args.inputs:
        if p.is_dir():
            for sub in sorted(p.iterdir()):
                if sub.suffix.lower() in (".html", ".json") and not sub.name.startswith("."):
                    input_files.append(sub)
        else:
            input_files.append(p)
    if not input_files:
        raise SystemExit("No HTML or JSON inputs found.")

    messages = []
    for inp in input_files:
        if inp.suffix.lower() == ".html":
            messages.extend(extract_messages_from_html(inp))
        elif inp.suffix.lower() == ".json":
            messages.extend(extract_messages_from_json(inp))
        else:
            print(f"Skipping unsupported file: {inp}")
    # Merge order: chronological. Discord IDs / ISO timestamps both sort safely as strings.
    messages.sort(key=lambda m: m.get("timestamp") or "")

    # Rewrite relative attachment URLs (DiscordChatExporter --media mode) so the viewer
    # can resolve them from the campaign root (parent of data/).
    # All inputs are expected to share the same discord/ directory; take the first as base.
    first = input_files[0].resolve()
    input_dir = first if first.is_dir() else first.parent
    viewer_dir = args.out.parent.parent.resolve()
    def to_viewer_url(url):
        if not url:
            return url
        if url.startswith(("http://", "https://", "data:", "//")):
            return url
        try:
            return (input_dir / url).resolve().relative_to(viewer_dir).as_posix()
        except (ValueError, OSError):
            return url

    tags = load_country_tags(args.tags, args.raw_tags)
    lookup = build_country_lookup(tags)
    alias_cache = (
        json.loads(args.aliases.read_text(encoding="utf-8"))
        if args.aliases.exists() else {}
    )

    events = []
    untagged = []
    # Keyed by (channel_id, author_id) so a same-author follow-up in one channel never
    # merges into an event posted in a different channel within the continuation window.
    last_event_by_author = {}

    for msg in messages:
        content = (msg.get("content") or "").strip()
        msg_images = []
        for a in (msg.get("attachments") or []):
            ct = (a.get("content_type") or "").lower()
            if ct.startswith("image/"):
                msg_images.append({
                    "url": to_viewer_url(a.get("url")),
                    "filename": a.get("filename"),
                    "width": a.get("width"),
                    "height": a.get("height"),
                })
        if not content and not msg_images:
            continue
        author = msg.get("author") or {}
        author_name = author.get("global_name") or author.get("username") or "unknown"
        author_id = author.get("id") or author_name
        channel_id = msg.get("channel_id") or ""
        cont_key = (channel_id, author_id)
        msg_id = msg.get("id") or hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
        ts_str = msg.get("timestamp")
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else None
        except ValueError:
            ts = None

        # Try the canonical [Date:…][Country:…][Location:…][Tag:…] format first.
        bracket_fields, bracket_body = parse_bracket_header(content)
        header_attempted = bracket_fields is not None
        invalid_reason = None
        if bracket_fields is not None:
            raw_date = bracket_fields["date"]
            date = parse_date(raw_date)
            candidate = bracket_fields.get("country")
            province_raw = bracket_fields.get("location") or bracket_fields.get("province")
            tag_raw = (bracket_fields.get("tag") or "").strip()
            tag = TAG_LOOKUP.get(tag_raw.lower()) if tag_raw else None
            body = bracket_body
            if date is None:
                invalid_reason = f"bracket header present but date {raw_date!r} couldn't be parsed"
        else:
            date, candidate, body = parse_header(content)
            province_raw = None
            tag = None
            if date is None and looks_like_new_event(content):
                header_attempted = True
                invalid_reason = "looks like a new event (heading or key:value) but no valid date"

        if date is not None:
            country_tag = None
            if candidate:
                country_tag = resolve_country(
                    candidate, lookup, tags, alias_cache,
                    interactive=not args.non_interactive,
                )
                if country_tag is None and bracket_fields is None:
                    # Loose-format fallback: line wasn't recognised as a country, so it's body.
                    body = (candidate + ("\n\n" + body if body else "")).strip()
            event = {
                "id": msg_id,
                "date": date,
                "country": country_tag,
                "countryRaw": candidate if country_tag else None,
                "province": province_raw,
                "tag": tag,
                "author": author_name,
                "snippet": (body[:120] if body else ""),
                "fullText": body,
                "images": msg_images,
            }
            events.append(event)
            if ts:
                last_event_by_author[cont_key] = (event, ts)
        elif header_attempted:
            # Tried to start a new event but format was broken — surface in untagged with a reason.
            # Never merge a header-attempted post into the previous event's narrative.
            untagged.append({
                "id": msg_id,
                "author": author_name,
                "timestamp": ts_str,
                "preview": content[:160],
                "content": content,
                "images": msg_images,
                "reason": invalid_reason or "header attempted but unparseable",
            })
        else:
            prev = last_event_by_author.get(cont_key)
            if prev and ts and (ts - prev[1]) <= CONTINUATION_WINDOW:
                ev, _ = prev
                if content:
                    ev["fullText"] = (ev["fullText"] + "\n\n" + content).strip() if ev["fullText"] else content
                    ev["snippet"] = ev["fullText"][:120]
                if msg_images:
                    ev["images"] = (ev.get("images") or []) + msg_images
                last_event_by_author[cont_key] = (ev, ts)
            else:
                untagged.append({
                    "id": msg_id,
                    "author": author_name,
                    "timestamp": ts_str,
                    "preview": content[:160],
                    "content": content,
                    "images": msg_images,
                    "reason": "no header detected",
                })

    # Apply manual overrides from data/overrides.json (written by events.html editor).
    overrides_path = args.out.parent / "overrides.json"
    if overrides_path.exists():
        try:
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            overrides = {}
    else:
        overrides = {}

    if overrides:
        # Merge overrides onto matching parsed events (per-field, non-empty wins).
        for ev in events:
            ov = overrides.get(ev["id"])
            if not ov:
                continue
            for k, v in ov.items():
                if v is None or v == "":
                    continue
                ev[k] = v
            if "fullText" in ov:
                ev["snippet"] = (ov.get("fullText") or "")[:120]

        # Promote untagged messages that have an override with a valid date into real events.
        kept_untagged = []
        promoted_ids = set()
        existing_ids = {ev["id"] for ev in events}
        for u in untagged:
            ov = overrides.get(u["id"])
            if ov and ov.get("date") and u["id"] not in existing_ids:
                full = ov.get("fullText") or u.get("content", "")
                events.append({
                    "id": u["id"],
                    "date": ov["date"],
                    "country": ov.get("country"),
                    "countryRaw": ov.get("countryRaw") or ov.get("country"),
                    "province": ov.get("province"),
                    "tag": ov.get("tag"),
                    "author": ov.get("author") or u["author"],
                    "title": ov.get("title"),
                    "snippet": (full or "")[:120],
                    "fullText": full,
                    "images": ov.get("images", u.get("images", [])),
                })
                promoted_ids.add(u["id"])
            else:
                kept_untagged.append(u)
        untagged = kept_untagged
        if promoted_ids:
            print(f"  promoted {len(promoted_ids)} untagged message(s) via overrides.json")
        events.sort(key=lambda e: e["date"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"events": events}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Structured untagged.json for the events.html editor (alongside untagged.log).
    untagged_json_path = args.untagged_log.with_suffix(".json")
    untagged_json_path.parent.mkdir(parents=True, exist_ok=True)
    untagged_json_path.write_text(
        json.dumps(untagged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    args.aliases.parent.mkdir(parents=True, exist_ok=True)
    args.aliases.write_text(
        json.dumps(alias_cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if untagged:
        args.untagged_log.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for u in untagged:
            lines.append(f"[{u['timestamp']}] {u['author']} ({u['id']})")
            lines.append(f"  reason: {u.get('reason', 'unknown')}")
            preview = (u.get('preview') or '').replace('\n', ' / ')
            lines.append(f"  preview: {preview}")
            lines.append("")
        args.untagged_log.write_text("\n".join(lines), encoding="utf-8")

    print(f"Parsed {len(events)} events from {len(messages)} messages.")
    print(f"  events  -> {args.out}")
    print(f"  aliases -> {args.aliases}")
    if untagged:
        print(f"  {len(untagged)} unparseable message(s) -> {args.untagged_log}")


if __name__ == "__main__":
    main()
