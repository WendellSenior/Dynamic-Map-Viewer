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


# Two key shapes inside the l_english YAMLs:
#   1) `location_id: "Display"`               → canonical English display name
#   2) `location_id.culture_language: "Name"` → culture-specific endonym (e.g. Nicosie, Lisboa)
# We split rather than alternate regexes because culture suffixes themselves contain
# letters/numbers/underscores and could otherwise collide.
LOC_NAME_RE = re.compile(r'^\s*([a-z][a-z0-9_.]*)\s*:\s*"([^"]+)"')


def parse_location_names(text):
    """Yield (location_id, name, is_culture_variant) tuples from a location_names YAML.

    Handles both the plain English file (`location_id: "Name"`) and the
    culture-specific files (`location_id.culture_language: "Endonym"`).
    Empty endonyms (`""`) and `$placeholder$` references are skipped."""
    for line in text.splitlines():
        m = LOC_NAME_RE.match(line)
        if not m:
            continue
        key, name = m.group(1), m.group(2)
        if not name or (name.startswith("$") and name.endswith("$")):
            continue
        if "." in key:
            loc_id = key.split(".", 1)[0]
            yield loc_id, name, True
        else:
            yield key, name, False


# Definitions parser: walks the hierarchical `name = { ... }` Clausewitz format and
# records the first leaf location ID reachable under each named group. Players who
# type "Holland" (an area name) or "Brittany" (a region name) get mapped to a real
# location in that group.
def parse_definitions(text):
    """Returns {group_name: first_location_id} for every named group in definitions.txt."""
    aliases = {}
    pos = 0
    n = len(text)

    def skip_ws(p):
        while p < n:
            c = text[p]
            if c in " \t\n\r":
                p += 1
            elif c == "#":
                while p < n and text[p] != "\n":
                    p += 1
            else:
                break
        return p

    ident_re = re.compile(r"[\w\-]+")

    def parse_value(p):
        """Parse the right-hand side of `name =`. Returns (first_loc, new_pos)."""
        p = skip_ws(p)
        if p >= n:
            return None, p
        if text[p] == "{":
            p += 1
            first_loc = None
            while True:
                p = skip_ws(p)
                if p >= n:
                    break
                if text[p] == "}":
                    return first_loc, p + 1
                m = ident_re.match(text, p)
                if not m:
                    p += 1
                    continue
                token = m.group(0)
                after = skip_ws(m.end())
                if after < n and text[after] == "=":
                    sub_first, p = parse_value(after + 1)
                    if sub_first:
                        aliases[token] = sub_first
                        if first_loc is None:
                            first_loc = sub_first
                else:
                    # Bare identifier — it's a leaf location.
                    if first_loc is None:
                        first_loc = token
                    p = m.end()
            return first_loc, p
        # Bare identifier value
        m = ident_re.match(text, p)
        if m:
            return m.group(0), m.end()
        return None, p

    while pos < n:
        pos = skip_ws(pos)
        if pos >= n:
            break
        m = ident_re.match(text, pos)
        if not m:
            pos += 1
            continue
        token = m.group(0)
        after = skip_ws(m.end())
        if after < n and text[after] == "=":
            first_loc, pos = parse_value(after + 1)
            if first_loc:
                aliases[token] = first_loc
        else:
            pos = m.end()
    return aliases


def strip_group_suffix(name):
    """Strip common Paradox suffixes so 'holland_area' becomes 'holland'."""
    for suf in ("_area", "_province", "_region", "_subcontinent", "_continent"):
        if name.endswith(suf):
            return name[:-len(suf)]
    return name


TAG_BLOCK_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{1,5})\s*=\s*\{")
CAPITAL_LINE_RE = re.compile(r"^\s*capital\s*=\s*(\w+)\s*(?:#.*)?$")


def parse_country_capitals(text):
    """Scan 10_countries.txt (or equivalent) for `TAG = { ... capital = loc ... }` blocks.
    Returns {tag: capital_location_id}. Only matches `capital = X` at the country's top
    level (depth 1 within the TAG block), so nested sub-block fields aren't confused."""
    out = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = TAG_BLOCK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        tag = m.group(1)
        depth = 1
        j = i + 1
        while j < len(lines) and depth > 0:
            sub = lines[j]
            cm = CAPITAL_LINE_RE.match(sub)
            if cm and depth == 1:
                out[tag] = cm.group(1)
            depth += sub.count("{") - sub.count("}")
            j += 1
        i = j
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
    ap.add_argument("--ref-dir", type=Path,
                    default=Path("assets/reference/eu5"),
                    help="Directory containing locators_city.txt + country_names_l_english.yml + "
                         "location_names_l_english.yml + definitions.txt. Defaults to the shared "
                         "reference dir; outputs (provinces.json + tags.json) are written there too.")
    ap.add_argument("--snapshots", type=Path,
                    default=Path("eu5-tbd/data/snapshots.json"),
                    help="Used only to read map height for y-flipping locator coords. Snapshots stay "
                         "per-campaign so this defaults to whichever campaign you're calibrating.")
    args = ap.parse_args()

    ref = args.ref_dir
    snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
    map_height = snapshots.get("config", {}).get("height", 8192)

    locators_text = (ref / "locators_city.txt").read_text(encoding="utf-8-sig", errors="replace")
    names_text = (ref / "country_names_l_english.yml").read_text(encoding="utf-8-sig", errors="replace")

    provinces = parse_locators(locators_text, map_height)
    tags = parse_country_names(names_text)

    # Manual country aliases overlay (e.g. MAM: ["Mamluks"]). Curated, committed.
    aliases_path = ref / "country_aliases.json"
    manual_alias_count = 0
    if aliases_path.exists():
        try:
            raw = json.loads(aliases_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        for tag, alist in raw.items():
            if not isinstance(alist, list) or tag not in tags:
                continue
            existing = set(a.lower() for a in tags[tag].get("aliases", []))
            for a in alist:
                if a and a.lower() not in existing:
                    tags[tag].setdefault("aliases", []).append(a)
                    existing.add(a.lower())
                    manual_alias_count += 1

    # Build a reverse index from canonical location id -> the provinces.json display key.
    id_to_key = {info["id"]: key for key, info in provinces.items()}

    # Location display-name aliases. Scans every `location_names_*_l_english.yml`
    # in <ref>/location_names/. The unsuffixed file (`location_names_l_english.yml`)
    # is the canonical English name; the per-culture files supply endonyms
    # (Nicosie, Lisboa, München, etc.) so a Discord poster typing the local name
    # still resolves to the right pin.
    loc_names_dir = ref / "location_names"
    name_aliases = 0
    culture_aliases = 0
    if loc_names_dir.is_dir():
        # Glob deliberately uses `*_l_english.yml` (no leading `_`) so it matches
        # both the default `location_names_l_english.yml` and the culture variants
        # `location_names_<culture>_l_english.yml`. With `_*_` the default file
        # would be silently skipped — discovered when Wien wasn't appearing as
        # a Vienna alias even though it's the English-file canonical name.
        for yml in sorted(loc_names_dir.glob("location_names*_l_english.yml")):
            yml_text = yml.read_text(encoding="utf-8-sig", errors="replace")
            for loc_id, name, is_culture in parse_location_names(yml_text):
                key = id_to_key.get(loc_id)
                if not key:
                    continue
                if name == key:
                    continue  # No new info; canonical English already matches.
                provinces[key].setdefault("aliases", set()).add(name)
                if is_culture:
                    culture_aliases += 1
                else:
                    name_aliases += 1

    # Definitions.txt: area/province/region/etc names -> first contained location id.
    def_path = ref / "definitions.txt"
    group_aliases = 0
    if def_path.exists():
        def_text = def_path.read_text(encoding="utf-8-sig", errors="replace")
        for group_name, first_loc in parse_definitions(def_text).items():
            key = id_to_key.get(first_loc)
            if not key:
                continue
            stub = strip_group_suffix(group_name)
            for variant in (group_name, stub, stub.replace("_", " ").title()):
                if variant and variant != key:
                    provinces[key].setdefault("aliases", set()).add(variant)
                    group_aliases += 1

    # Country capitals: TAG / country name / curated aliases all become aliases on the
    # capital location. So `[Location:Muscovy]` resolves to Moscow, `[Location:MOS]` too.
    cap_path = ref / "country_capitals.txt"
    country_aliases = 0
    if cap_path.exists():
        cap_text = cap_path.read_text(encoding="utf-8-sig", errors="replace")
        for tag, cap_loc in parse_country_capitals(cap_text).items():
            key = id_to_key.get(cap_loc)
            if not key:
                continue
            info = tags.get(tag, {})
            for alias in [tag, info.get("name", ""), *info.get("aliases", [])]:
                if alias and alias != key:
                    provinces[key].setdefault("aliases", set()).add(alias)
                    country_aliases += 1

    # Sort + dedupe alias sets into lists for JSON.
    for key, info in provinces.items():
        if "aliases" in info:
            info["aliases"] = sorted(info["aliases"])

    (ref / "provinces.json").write_text(
        json.dumps(provinces, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (ref / "tags.json").write_text(
        json.dumps(tags, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Parsed {len(provinces)} locations -> {ref}/provinces.json")
    print(f"Parsed {len(tags)} country tags -> {ref}/tags.json")
    print(f"  Added {name_aliases} English display-name aliases from location_names/*.yml")
    print(f"  Added {culture_aliases} culture-endonym aliases (e.g. Nicosie, Lisboa)")
    print(f"  Added {group_aliases} group-name aliases from definitions.txt")
    print(f"  Added {country_aliases} country-name aliases from country_capitals.txt")
    if manual_alias_count:
        print(f"  Added {manual_alias_count} manual aliases from country_aliases.json")
    print(f"Map height: {map_height}px (z inverted to top-left origin)")


if __name__ == "__main__":
    main()
