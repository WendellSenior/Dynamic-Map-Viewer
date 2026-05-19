"""EU4 positions.txt -> provinces.json. Uses the first (city) position pair; y inverted to top-left origin."""

import argparse
import json
import re
from pathlib import Path


def parse_positions(text, map_height):
    out = {}
    name = None
    pid = None
    depth = 0
    in_position = False
    buffer = []

    for raw in text.splitlines():
        line = raw.strip()

        if depth == 0:
            if line.startswith("#"):
                name = line.lstrip("#").strip()
                continue
            m = re.match(r"^(\d+)\s*=\s*\{", line)
            if m:
                pid = int(m.group(1))
                depth = 1
                continue
        elif in_position:
            close = line.find("}")
            payload = line if close == -1 else line[:close]
            buffer.extend(payload.split())
            if close != -1:
                in_position = False
                _emit(out, name, pid, buffer, map_height)
                buffer = []
        else:
            ps = line.find("position=")
            if ps != -1:
                rest = line[ps + len("position="):].lstrip()
                if rest.startswith("{"):
                    rest = rest[1:].lstrip()
                close = rest.find("}")
                if close != -1:
                    nums = rest[:close].split()
                    _emit(out, name, pid, nums, map_height)
                else:
                    in_position = True
                    buffer.extend(rest.split())

        depth += line.count("{")
        depth -= line.count("}")
        if depth <= 0:
            depth = 0
            name = None
            pid = None
            in_position = False
            buffer = []

    return out


def _emit(out, name, pid, nums, map_height):
    if not name or pid is None or len(nums) < 2:
        return
    try:
        x = float(nums[0])
        y = float(nums[1])
    except ValueError:
        return
    out[name] = {"id": pid, "coords": [round(x), round(map_height - y)]}


def main():
    ap = argparse.ArgumentParser(description="EU4 positions.txt -> provinces.json")
    ap.add_argument("--input", type=Path, default=Path("data/reference/eu4/positions.txt"))
    ap.add_argument("--snapshots", type=Path, default=Path("data/snapshots.json"))
    ap.add_argument("--out", type=Path, default=Path("data/reference/eu4/provinces.json"))
    args = ap.parse_args()

    snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
    map_height = snapshots.get("config", {}).get("height", 2048)

    # EU4 ships text files in Windows-1252 (Latin-1 superset).
    text = args.input.read_text(encoding="cp1252", errors="replace")
    provinces = parse_positions(text, map_height)
    args.out.write_text(
        json.dumps(provinces, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Parsed {len(provinces)} provinces from {args.input}")
    print(f"Map height: {map_height}px (y inverted to top-left origin)")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
