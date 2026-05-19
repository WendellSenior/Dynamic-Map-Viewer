"""Discord export -> data/events.json. Tag format: [COUNTRY][YYYY-MM-DD] [PROV:Name]? body"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG_RE = re.compile(
    r"^\s*\[(?P<country>[A-Za-z]{2,4})\]\s*"
    r"\[(?P<date>\d{1,4}-\d{2}-\d{2})\]\s*"
    r"(?:\[PROV:(?P<province>[^\]]+)\]\s*)?"
    r"(?P<body>.*)$",
    re.DOTALL,
)


def parse_tagged_message(content, author, msg_id):
    m = TAG_RE.match(content)
    if not m:
        return None
    body = m.group("body").strip()
    return {
        "id": msg_id,
        "date": m.group("date"),
        "country": m.group("country").upper(),
        "province": m.group("province"),
        "author": author,
        "snippet": body[:120],
        "fullText": body,
    }


def parse_json_export(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for msg in data.get("messages", []):
        content = msg.get("content", "") or ""
        author = (msg.get("author") or {}).get("name", "unknown")
        msg_id = msg.get("id") or hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
        parsed = parse_tagged_message(content, author, msg_id)
        if parsed:
            events.append(parsed)
    return events


def main():
    ap = argparse.ArgumentParser(description="Discord export -> events.json")
    ap.add_argument("input", type=Path, help="DiscordChatExporter JSON export")
    ap.add_argument("--out", type=Path, default=Path("data/events.json"))
    args = ap.parse_args()

    if args.input.suffix.lower() != ".json":
        # HTML parsing path not implemented yet — DiscordChatExporter supports JSON export.
        print("Only .json exports are supported right now.", file=sys.stderr)
        sys.exit(2)

    events = parse_json_export(args.input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"events": events}, indent=2), encoding="utf-8")
    print(f"Wrote {len(events)} events -> {args.out}")


if __name__ == "__main__":
    main()
