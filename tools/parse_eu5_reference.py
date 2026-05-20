"""EU5 reference parser: locators_city.txt + country_names_l_english.yml -> provinces.json + tags.json"""

import argparse
import json
import re
from pathlib import Path


# EU5 city locator instance: { id=name position={ X Y Z } ... }
LOCATOR_RE = re.compile(
    r"\{\s*id\s*=\s*(\S+)\s+position\s*=\s*\{\s*([-\d.]+)\s+[-\d.]+\s+([-\d.]+)\s*\}"
)

# YAML line: ` TAG: "Display name"`
YAML_NAME_RE = re.compile(r'^\s*([A-Z][A-Z0-9_]+)\s*:\s*"([^"]*)"')

# Variant suffixes we don't want as base tag entries.
VARIANT_SUFFIXES = ("_ADJ", "_LONG", "_THE", "_abbreviation", "_PREFIX")
# Culture-specific variants use _<lowercase_word> pattern (e.g. SMI_scandinavian).
CULTURE_SUFFIX_RE = re.compile(r"_[a-z]")


def parse_locators(text, map_height):
    out = {}
    for id_, x_str, z_str in LOCATOR_RE.findall(text):
        x = float(x_str)
        z = float(z_str)
        display = id_.replace("_", " ").title()
        out[display] = {
            "id": id_,
            "coords": [round(x), round(map_height - z)],
        }
    return out


def parse_country_names(text):
    out = {}
    for line in text.splitlines():
        m = YAML_NAME_RE.match(line)
        if not m:
            continue
        key, name = m.group(1), m.group(2)
        if key.endswith(VARIANT_SUFFIXES):
            continue
        if CULTURE_SUFFIX_RE.search(key):
            continue
        if name.startswith("$") and name.endswith("$"):
            continue
        if key in out:
            continue
        out[key] = {"name": name, "aliases": []}
    return out


def main():
    ap = argparse.ArgumentParser(description="EU5 reference -> provinces.json + tags.json")
    ap.add_argument("--locators", type=Path,
                    default=Path("eu5-tbd/data/reference/eu5/locators_city.txt"))
    ap.add_argument("--country-names", type=Path,
                    default=Path("eu5-tbd/data/reference/eu5/country_names_l_english.yml"))
    ap.add_argument("--snapshots", type=Path,
                    default=Path("eu5-tbd/data/snapshots.json"))
    ap.add_argument("--out-provinces", type=Path,
                    default=Path("eu5-tbd/data/reference/eu5/provinces.json"))
    ap.add_argument("--out-tags", type=Path,
                    default=Path("eu5-tbd/data/reference/eu5/tags.json"))
    args = ap.parse_args()

    snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
    map_height = snapshots.get("config", {}).get("height", 8192)

    locators_text = args.locators.read_text(encoding="utf-8-sig", errors="replace")
    names_text = args.country_names.read_text(encoding="utf-8-sig", errors="replace")

    provinces = parse_locators(locators_text, map_height)
    tags = parse_country_names(names_text)

    args.out_provinces.write_text(
        json.dumps(provinces, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    args.out_tags.write_text(
        json.dumps(tags, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Parsed {len(provinces)} locations -> {args.out_provinces}")
    print(f"Parsed {len(tags)} country tags -> {args.out_tags}")
    print(f"Map height: {map_height}px (z inverted to top-left origin)")


if __name__ == "__main__":
    main()
