"""Build assets/reference/hoi4/{tags,provinces}.json from a HoI4 install.

HoI4 is the third game supported by the viewer, and the first whose map data
ships with NO usable coordinates: `map/positions.txt` exists but is zero
bytes, unlike EU4 where `tools/parse_positions.py` can just read pin
positions straight out of the file. So this parser derives coordinates
itself, by rasterising the province bitmap:

    map/definition.csv   province_id;r;g;b;type;coastal;terrain;continent
    map/provinces.bmp    5632x2048 24-bit, one flat RGB colour per province

Every pixel is mapped colour -> province_id, and each province's centroid is
the mean of its pixel coordinates. State centroids then come from the union
of their member provinces' pixels (so they are area-weighted — a state's pin
lands in its bulk, not the midpoint of its bounding box).

Because a centroid can fall outside a concave or split shape (Norway's
coastline, island states, anything horseshoe-shaped), every centroid is
snapped to the nearest pixel that genuinely belongs to that province/state.
Pins therefore always land on their own territory.

Coordinates are emitted in the game's native 5632x2048 map space, which is
the contract the viewer expects: each campaign's data/snapshots.json declares
`config: {width, height}` in that same space and app.js scales to whatever
the uploaded snapshot images are. NOTE: no y-flip is applied here. BMP stores
rows bottom-up, but Pillow already normalises to a top-left origin, so the
array is in image space as-is. (parse_positions.py DOES flip, because EU4's
positions.txt is in the game's bottom-left coordinate space — different
source, different convention.)

What ends up in provinces.json (both are "locations" the viewer can pin):

  - States  — the primary unit players talk about, named via
              history/states/*.txt `name="STATE_n"` joined to
              localisation/english/state_names_l_english.yml.
  - Cities  — victory points, i.e. the named settlements on the map, from
              localisation/english/victory_points_l_english.yml keyed by the
              VP's province id. Tag-prefixed variants (GER_VICTORY_POINTS_692
              = "München" vs the default "Munich") become aliases, so a
              poster writing [Location:München] still pins correctly. Same
              trick as the EU5 culture-endonym alias flattening.

Country names come from common/country_tags/00_countries.txt (identical
format to EU4's) joined to localisation/english/countries_l_english.yml.
HoI4 names a country differently per ideology — GER is "Germany", but also
"German Reich" (fascism), "German Republic" (democratic), "Socialist
Republic of Germany" (communism). All variants are folded in as aliases so
[Country:German Reich] resolves to GER.

Usage:
    python tools/parse_hoi4_reference.py                  # auto-detect install
    python tools/parse_hoi4_reference.py --game-dir "D:/SteamLibrary/..."
    python tools/parse_hoi4_reference.py --check          # validate, write nothing
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

GAME = "hoi4"

# Common Steam locations, tried in order when --game-dir isn't given.
DEFAULT_INSTALL_GUESSES = [
    r"C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV",
    r"C:/Program Files/Steam/steamapps/common/Hearts of Iron IV",
    r"D:/SteamLibrary/steamapps/common/Hearts of Iron IV",
    r"E:/SteamLibrary/steamapps/common/Hearts of Iron IV",
]

# Steam Workshop ids for mods we support out of the box, so --mod kaiserreich
# works without pasting a path. HoI4's Steam appid is 394360.
WORKSHOP_ROOTS = [
    r"C:/Program Files (x86)/Steam/steamapps/workshop/content/394360",
    r"C:/Program Files/Steam/steamapps/workshop/content/394360",
    r"D:/SteamLibrary/steamapps/workshop/content/394360",
    r"E:/SteamLibrary/steamapps/workshop/content/394360",
]
KNOWN_MODS = {
    # name -> (workshop id, default output game id)
    "kaiserreich": ("1521695605", "hoi4-kr"),
}

# Sanity probes per output game — a rebuild that loses these has gone wrong.
# Mods rename the world (KR has no Soviet Union; Russia is RUS), so the set
# is per-game rather than universal.
VALIDATION_PROBES = {
    "hoi4":    {"tags": ("GER", "FRA", "ENG", "SOV", "USA"),
                "locations": ("Berlin", "Paris", "London", "Moscow", "Rome")},
    "hoi4-kr": {"tags": ("GER", "FRA", "ENG", "RUS", "USA"),
                "locations": ("Berlin", "Paris", "London", "Moscow", "Rome")},
}

# Paradox .yml localisation: ` KEY:0 "Value"` — the :0 version suffix is
# optional and the leading space is conventional but not guaranteed. Values
# can contain escaped quotes. Not real YAML, so it gets a regex, not a parser.
LOC_RE = re.compile(r'^\s*([A-Za-z0-9_.]+):\d*\s*"(.*)"\s*$')

# Paradox colour codes (§Y ... §!) inside display strings.
LOC_COLOUR_RE = re.compile(r"§.")
# $SOMETHING$ — either a runtime variable ($NAME$) or, more usefully, a
# reference to another localisation key. Kaiserreich names many states after
# their main city that way: STATE_797 = "$VICTORY_POINTS_9671$".
LOC_REF_RE = re.compile(r"\$([A-Za-z0-9_.]+)\$")
# Any leftover $...$ once references have been resolved — a genuine runtime
# variable we can't fill in, so it gets dropped from display text.
LOC_LEFTOVER_RE = re.compile(r"\$[^$]*\$")

# `TAG = "countries/Name.txt"` — same shape as EU4's 00_countries.txt.
COUNTRY_TAG_RE = re.compile(r'^\s*([A-Z]{3})\s*=\s*"([^"]+)"')

# Vanilla HoI4 ideologies, used to recognise `GER_fascism`-style country-name
# keys. Only a fallback: the real list is read from common/ideologies at run
# time, because total-conversion mods replace it wholesale — Kaiserreich's are
# totalist / syndicalist / radical_socialist / social_democrat / ... and none
# of vanilla's four survive. Reading them matters: `GER_totalist` is
# "German Empire", and with the wrong list KR's countries all fall back to
# their bland vanilla names.
IDEOLOGY_SUFFIXES = ("fascism", "democratic", "neutrality", "communism")


def log(msg):
    print(msg, flush=True)


class GameFiles:
    """Resolves data paths across a base install and an optional mod, the way
    HoI4 itself does.

    Two different override rules, and getting them the wrong way round
    silently corrupts the output:

      - `replace_path="history/states"` in descriptor.mod means the mod owns
        that directory outright; base files there are ignored even when the
        mod has no file of the same name. Kaiserreich relies on this — its
        1125 state files share ZERO filenames with the base game's 1081, so
        without honouring replace_path we would merge two incompatible maps
        and emit states whose province ids don't exist in KR's definition.csv.
      - Everywhere else it's per-file: a mod file shadows the base file at the
        same relative path, and base files the mod doesn't mention still load.
        That's how KR swaps map/definition.csv + map/provinces.bmp while
        inheriting the rest of map/.

    Localisation is deliberately NOT a replace_path in KR, so mod names layer
    on top of base names key-by-key — see load_localisation."""

    def __init__(self, base_dir, mod_dir=None):
        self.base = Path(base_dir)
        self.mod = Path(mod_dir) if mod_dir else None
        self.replace_paths = set()
        if self.mod:
            descriptor = self.mod / "descriptor.mod"
            if descriptor.exists():
                self.replace_paths = {
                    m.strip().strip("/")
                    for m in re.findall(r'replace_path\s*=\s*"([^"]+)"',
                                        descriptor.read_text(encoding="utf-8-sig",
                                                             errors="replace"))
                }

    def _is_replaced(self, rel):
        rel = rel.strip("/")
        return any(rel == rp or rel.startswith(rp + "/") for rp in self.replace_paths)

    def file(self, rel):
        """Single file — the mod's copy wins when it exists."""
        if self.mod:
            candidate = self.mod / rel
            if candidate.exists():
                return candidate
        return self.base / rel

    def dir_files(self, rel, pattern="*.txt"):
        """Every file that would load from `rel`, honouring replace_path.

        Within a directory the mod shadows base files of the same NAME (not
        full path), matching how Paradox resolves loose files."""
        results = {}
        if not (self.mod and self._is_replaced(rel)):
            base_dir = self.base / rel
            if base_dir.is_dir():
                for p in sorted(base_dir.glob(pattern)):
                    results[p.name] = p
        if self.mod:
            mod_dir = self.mod / rel
            if mod_dir.is_dir():
                for p in sorted(mod_dir.glob(pattern)):
                    results[p.name] = p
        return [results[k] for k in sorted(results)]

    def localisation_layers(self, mod_only=False):
        """Localisation dirs in increasing precedence.

        `localisation/replace/` is HoI4's explicit "override anything" folder,
        so it sorts last.

        `mod_only` drops the base layer, for strings keyed by a numeric id the
        mod has taken ownership of. STATE_<n> / VICTORY_POINTS_<n> names are
        only meaningful alongside the history/states and map data they came
        from; when a mod replaces those, a base name surviving for an id the
        mod reused describes a completely different place."""
        layers = []
        if not mod_only and not (self.mod and self._is_replaced("localisation")):
            layers.append(self.base / "localisation" / "english")
        if self.mod:
            layers.append(self.mod / "localisation" / "english")
            layers.append(self.mod / "localisation" / "replace")
        return [d for d in layers if d.is_dir()]

    def owns_map_data(self):
        """True when the mod supplies its own states AND province ids, making
        base STATE_/VICTORY_POINTS_ names unsafe to inherit."""
        return bool(self.mod and self._is_replaced("history/states")
                    and (self.mod / "map" / "definition.csv").exists())


def load_localisation(layers):
    """Merge every .yml across `layers`, later layers winning per key.

    Scans recursively by key pattern rather than reading known filenames,
    because mods scatter their strings wherever they like: Kaiserreich's
    countries_l_english.yml is an empty 3-byte stub and the real names live in
    'KR_common/00 Map States l_english.yml' and 40-odd
    'KR_country_specific/XXX - Name l_english.yml' files."""
    out = {}
    for layer in layers:
        for path in sorted(layer.rglob("*.yml")):
            out.update(read_loc_file(path))
    return out


def clean_loc(value):
    """Strip Paradox colour codes. $...$ is deliberately preserved here so
    resolve_references can follow key references after the merge; whatever is
    still unresolved gets dropped by finalise_localisation."""
    return LOC_COLOUR_RE.sub("", value).strip()


def resolve_references(loc, max_depth=5):
    """Expand `$OTHER_KEY$` references in localisation values.

    Kaiserreich names a state after its capital by pointing at that city's key
    (STATE_797 = "$VICTORY_POINTS_9671$"). Dropping those leaves the entry
    empty, and the base game's unrelated name for the same numeric id wins the
    merge instead — which is how KR's state 797 ended up displaying as
    "Istanbul" at coordinates in the Americas. Resolving them keeps the mod's
    own name and, more importantly, keeps the wrong one out.

    Iterative with a depth cap so reference chains resolve without a malformed
    or cyclic pair looping forever."""
    for _ in range(max_depth):
        changed = False
        for key, value in loc.items():
            if "$" not in value:
                continue
            def _sub(m):
                target = loc.get(m[1])
                # Leave it alone if it doesn't resolve or would self-reference.
                return target if target is not None and m[1] != key else m[0]
            new = LOC_REF_RE.sub(_sub, value)
            if new != value:
                loc[key] = new
                changed = True
        if not changed:
            break
    return loc


def finalise_localisation(loc):
    """Drop unresolved runtime variables, then discard now-empty entries."""
    out = {}
    for key, value in loc.items():
        cleaned = LOC_LEFTOVER_RE.sub("", value).strip()
        if cleaned:
            out[key] = cleaned
    return out


def read_loc_file(path):
    """Parse a Paradox localisation .yml into {key: cleaned_value}."""
    if not path.exists():
        log(f"  WARNING: missing localisation file {path.name}")
        return {}
    out = {}
    # utf-8-sig strips the BOM these files always carry.
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("l_"):
            continue
        m = LOC_RE.match(line)
        if not m:
            continue
        value = clean_loc(m[2])
        if value:
            out[m[1]] = value
    return out


def parse_definitions(path):
    """map/definition.csv -> ({(r,g,b): province_id}, {province_id: type}).

    Sea/lake provinces are kept in the colour map (they still occupy pixels
    and must not be mis-attributed to a neighbouring land province) but are
    tracked by type so they can be excluded from pin candidates."""
    colour_to_id = {}
    kinds = {}
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(";")
        if len(parts) < 5:
            continue
        try:
            pid, r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
        except ValueError:
            continue  # header or malformed row
        colour_to_id[(r, g, b)] = pid
        kinds[pid] = parts[4].strip().lower()
    return colour_to_id, kinds


def parse_state_files(state_paths):
    """history/states/*.txt -> [{id, name_key, provinces[], victory_points[]}].

    Hand-rolled field extraction rather than a full Clausewitz parser: we only
    need four fields and the surrounding structure (buildings blocks, nested
    province-keyed sub-blocks) is irrelevant here. `provinces={...}` is matched
    specifically so the province-id block inside `buildings={ 3838 = {...} }`
    can't be mistaken for it."""
    states = []
    for path in state_paths:
        text = path.read_text(encoding="utf-8-sig", errors="replace")

        m_id = re.search(r"\bid\s*=\s*(\d+)", text)
        if not m_id:
            continue

        m_name = re.search(r'\bname\s*=\s*"([^"]+)"', text)

        m_prov = re.search(r"\bprovinces\s*=\s*\{([^}]*)\}", text)
        provinces = [int(x) for x in re.findall(r"\d+", m_prov[1])] if m_prov else []

        # victory_points = { <province_id> <value> } — may appear more than
        # once per state (one block per VP).
        victory_points = []
        for block in re.findall(r"\bvictory_points\s*=\s*\{([^}]*)\}", text):
            nums = [int(x) for x in re.findall(r"\d+", block)]
            if len(nums) >= 2:
                victory_points.append((nums[0], nums[1]))  # (province_id, points)

        states.append({
            "id":             int(m_id[1]),
            "name_key":       m_name[1] if m_name else None,
            "provinces":      provinces,
            "victory_points": victory_points,
            "file":           path.name,
        })
    return states


def compute_province_pixels(bmp_path, colour_to_id):
    """provinces.bmp -> (labels HxW int32 array of province ids, {pid: count}).

    Colours are packed to a single int ((r<<16)|(g<<8)|b) so the whole image
    can be translated to province ids with one vectorised lookup rather than
    11.5M dict hits."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        sys.exit("ERROR: this parser needs numpy + Pillow (pip install numpy Pillow).")

    Image.MAX_IMAGE_PIXELS = None  # 5632x2048 is fine, but the guard is noisy
    with Image.open(bmp_path) as img:
        # Pillow yields a top-left origin regardless of BMP's bottom-up row
        # storage, which is exactly the image space the viewer wants.
        arr = np.asarray(img.convert("RGB"), dtype=np.uint32)

    packed = (arr[:, :, 0] << 16) | (arr[:, :, 1] << 8) | arr[:, :, 2]

    # Dense lookup table over the packed 24-bit colour space (16M entries,
    # ~67 MB as int32) — a straight index is far faster than any dict/searchsorted
    # path and keeps this script to a couple of seconds.
    lut = np.zeros(1 << 24, dtype=np.int32)
    for (r, g, b), pid in colour_to_id.items():
        lut[(r << 16) | (g << 8) | b] = pid

    labels = lut[packed]
    unique, counts = np.unique(labels, return_counts=True)
    return labels, dict(zip(unique.tolist(), counts.tolist()))


def centroids_for_groups(labels, groups):
    """Area-weighted centroid per group, snapped onto the group's own pixels.

    `groups` maps group_key -> iterable of province ids. Returns
    {group_key: (x, y)}. Groups with no pixels on the map are omitted.

    The snap matters: a plain centroid of a concave or multi-part shape
    (fjorded coastlines, island chains) can land in the sea or in a
    neighbouring country. Snapping to the nearest member pixel guarantees the
    pin sits on the territory it names."""
    import numpy as np

    height, width = labels.shape
    ys, xs = np.indices((height, width), dtype=np.int32)

    max_pid = int(labels.max())
    # province_id -> group index, so the whole map can be relabelled at once.
    group_keys = list(groups)
    pid_to_group = np.full(max_pid + 1, -1, dtype=np.int32)
    for gi, key in enumerate(group_keys):
        for pid in groups[key]:
            if 0 <= pid <= max_pid:
                pid_to_group[pid] = gi

    gidx = pid_to_group[labels]
    valid = gidx >= 0

    flat_g = gidx[valid]
    flat_x = xs[valid]
    flat_y = ys[valid]

    n_groups = len(group_keys)
    counts = np.bincount(flat_g, minlength=n_groups)
    sum_x  = np.bincount(flat_g, weights=flat_x, minlength=n_groups)
    sum_y  = np.bincount(flat_g, weights=flat_y, minlength=n_groups)

    out = {}
    snapped = 0
    for gi, key in enumerate(group_keys):
        n = counts[gi]
        if n == 0:
            continue  # group owns no pixels (wasteland / cut content)
        cx = sum_x[gi] / n
        cy = sum_y[gi] / n
        ix, iy = int(round(cx)), int(round(cy))

        # Snap if the rounded centroid isn't actually on this group.
        if not (0 <= iy < height and 0 <= ix < width and gidx[iy, ix] == gi):
            member = np.argwhere(gidx == gi)          # (row, col) pairs
            d2 = (member[:, 1] - cx) ** 2 + (member[:, 0] - cy) ** 2
            best = member[int(np.argmin(d2))]
            iy, ix = int(best[0]), int(best[1])
            snapped += 1

        out[key] = (ix, iy)
    return out, snapped


def parse_ideologies(files):
    """common/ideologies/*.txt -> {ideology_name}.

    Country names are keyed `<TAG>_<ideology>`, and the `<TAG>_` namespace is
    also full of focus/decision strings (GER_abolish_death_penalty), so we
    need the real ideology list to tell names from noise rather than accepting
    any suffix. Brace depth is tracked instead of trusting indentation: the
    ideology names are the keys directly inside `ideologies = { ... }`."""
    names = set()
    for path in files.dir_files("common/ideologies"):
        text = re.sub(r"#[^\n]*", "",
                      path.read_text(encoding="utf-8-sig", errors="replace"))
        depth = 0
        for m in re.finditer(r"([A-Za-z_]\w*)\s*=\s*\{|\{|\}", text):
            if m.group(0) == "}":
                depth -= 1
            elif m.group(1):
                if depth == 1:
                    names.add(m.group(1))
                depth += 1
            else:
                depth += 1
    return names or set(IDEOLOGY_SUFFIXES)


def parse_ruling_parties(country_paths):
    """history/countries/<TAG> - <Name>.txt -> {TAG: ideology}.

    A country's name in HoI4 depends on who runs it, so knowing the 1936
    ruling party lets us pick the historically apt variant ("Chinese Soviet
    Republic" for communist PRC) instead of an arbitrary one."""
    out = {}
    for path in country_paths:
        tag = path.name.split(" ", 1)[0].strip().upper()
        if len(tag) != 3:
            continue
        m = re.search(r"\bruling_party\s*=\s*(\w+)",
                      path.read_text(encoding="utf-8-sig", errors="replace"))
        if m:
            out[tag] = m[1].lower()
    return out


def build_tags(files, loc, ref_dir):
    """{TAG: {name, aliases}} from country_tags + merged localisation."""
    tags_file = files.file("common/country_tags/00_countries.txt")
    ruling = parse_ruling_parties(files.dir_files("history/countries"))
    ideologies = parse_ideologies(files)

    # Group localisation by tag: base name + ideology variants + _DEF forms.
    by_tag = defaultdict(dict)
    for key, value in loc.items():
        m = re.fullmatch(r"([A-Z]{3})(?:_(.+))?", key)
        if not m:
            continue
        by_tag[m[1]][m[2] or ""] = value

    # Cosmetic-tag names: `AUS_empire_paternal_autocrat = "Austrian Empire"`.
    # A country renames itself on completing a focus, and in an alt-history mod
    # those are the names people actually use — Kaiserreich's Austria is
    # localised blandly as "Austria" but becomes the Austrian Empire in play.
    # Matched as <TAG>_<something>_<real ideology> so the vast `<TAG>_` focus
    # and decision namespace can't leak in, with a length cap because a country
    # name is short and focus/description strings are not.
    cosmetic = defaultdict(set)
    for key, value in loc.items():
        m = re.fullmatch(r"([A-Z]{3})_([a-z_]+)_(" + "|".join(map(re.escape, ideologies)) + r")", key)
        if m and 0 < len(value) <= 60:
            cosmetic[m[1]].add(value)

    # Collect candidates in 00_countries.txt order — that order is meaningful:
    # the 1936 majors head the file (GER line 1, CHI line 38) and the
    # derivative/alt-history tags come later (PRC 81, WGR 90, DDR 91, RNG 364).
    raw_lines = tags_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    rows = []
    for line in raw_lines:
        if line.strip().startswith("#"):
            continue
        m = COUNTRY_TAG_RE.match(line)
        if m:
            rows.append((m[1], m[2]))

    # Vanilla names one file per country ("countries/Germany.txt"), so the stem
    # doubles as a decent plain-English name. Kaiserreich instead points dozens
    # of tags at shared files ("countries/WestEur.txt"), where the stem is a
    # graphics grouping, not a country — using it would inject junk aliases
    # like "WestEur"/"Asian" and could even surface as a display name. So only
    # trust a stem that belongs to exactly one tag.
    stem_uses = defaultdict(int)
    for _tag, country_file in rows:
        stem_uses[Path(country_file).stem] += 1

    candidates = []
    for tag, country_file in rows:
        variants = by_tag.get(tag, {})

        raw_stem = Path(country_file).stem
        stem = ("" if stem_uses[raw_stem] > 1
                else re.sub(r"\s*\(.*\)$", "", raw_stem).strip())

        # Preferred display name: the bare `GER: "Germany"` key, else the stem.
        preferred = variants.get("") or stem or tag

        extras = set()
        for suffix, value in variants.items():
            if not value:
                continue
            # Keep ideology names ("German Reich") and their _DEF articles
            # ("the German Reich") — players write both. Skip everything else
            # (ADJ forms etc. are noise for country matching).
            base = suffix[:-4] if suffix.endswith("_DEF") else suffix
            if base in ideologies or base == "":
                extras.add(value)
        if stem:
            extras.add(stem)
        # Preferred fallback when the bare name is already claimed by an
        # earlier tag: this tag's name under its own 1936 ruling party.
        ideology_name = variants.get(ruling.get(tag, ""), "")
        candidates.append((tag, preferred, stem, extras, ideology_name,
                           set(cosmetic.get(tag, ()))))

    # Resolve name collisions: several tags legitimately share a localisation
    # name ("Germany" is GER, WGR and DDR; "China" is CHI, PRC, MAN and RNG).
    # preprocess.py's country lookup is last-write-wins, so leaving duplicates
    # in would silently hand [Country:Germany] to whichever tag sorted last —
    # WGR, a post-war tag, instead of GER. Claim names first-come-first-served
    # in file order, so the 1936 major keeps the bare name and the derivatives
    # fall back to their unique stems ("West Germany", "East Germany").
    claimed = {}
    out = {}
    for tag, preferred, stem, extras, ideology_name, _cosmetic in candidates:
        # Order matters only for collision losers: try the bare name, then the
        # ruling-party name (a real in-game name — "Chinese Soviet Republic"),
        # then other ideology variants, and only fall back to the raw
        # countries/<file>.txt stem ("ComChina") as a last resort.
        ordered = [preferred]
        if ideology_name and ideology_name != preferred:
            ordered.append(ideology_name)
        ordered += sorted(extras - {preferred, stem, ideology_name})
        if stem and stem not in ordered:
            ordered.append(stem)

        name = next((n for n in ordered if n.lower() not in claimed), None)
        if name is None:
            # Every candidate is taken by an earlier tag. Common in mods with
            # civil-war factions: Kaiserreich's ACC/APG are both localised
            # "America", already claimed by USA. Qualify the contested name
            # with the tag rather than showing a bare code — "America (ACC)"
            # reads as a place, "ACC" reads as a bug. Still derived from game
            # data, so no faction names are invented.
            contested = next((n for n in ordered if n), "")
            name = f"{contested} ({tag})" if contested else tag
        claimed[name.lower()] = tag

        aliases = set()
        for cand in ordered:
            if cand == name or not cand:
                continue
            if cand.lower() in claimed:
                continue  # belongs to an earlier tag; don't shadow it
            claimed[cand.lower()] = tag
            aliases.add(cand)

        out[tag] = {"name": name, "aliases": sorted(aliases)}

    # Cosmetic names are claimed only after every tag has taken its own
    # identity names. A cosmetic name is what a country may RENAME ITSELF to
    # after a focus, not who it is — and several tags can offer the same one.
    # Claiming them in the first pass let Kaiserreich's NFA (National France,
    # earlier in 00_countries.txt) take "Commune of France" out from under
    # FRA, whose actual ideology name it is.
    for tag, _preferred, _stem, _extras, _ideology, cosmetic_names in candidates:
        for cand in sorted(cosmetic_names):
            if not cand or cand.lower() in claimed:
                continue
            claimed[cand.lower()] = tag
            out[tag]["aliases"] = sorted(set(out[tag]["aliases"]) | {cand}
                                         - {out[tag]["name"]})

    # Hand-curated extras, same convention as EU5's country_aliases.json.
    alias_path = ref_dir / "country_aliases.json"
    if alias_path.exists():
        extra = json.loads(alias_path.read_text(encoding="utf-8"))
        merged_tags, unknown = 0, []
        for tag, names in extra.items():
            if tag.startswith("_"):
                continue  # "_comment" etc., same convention as event_overrides.json
            if tag not in out:
                unknown.append(tag)
                continue
            # Curated names win any earlier claim — they're the shorthand
            # players actually type, and a human decided which tag owns them.
            for other, info in out.items():
                if other == tag:
                    continue
                drop = {n for n in info["aliases"] if n.lower() in
                        {x.lower() for x in names}}
                if drop:
                    info["aliases"] = sorted(set(info["aliases"]) - drop)
            combined = set(out[tag]["aliases"]) | set(names)
            out[tag]["aliases"] = sorted(combined - {out[tag]["name"]})
            merged_tags += 1
        if unknown:
            log(f"  WARNING: country_aliases.json references {len(unknown)} unknown "
                f"tag(s), ignored: {', '.join(sorted(unknown)[:5])}")
        log(f"  merged hand-curated aliases for {merged_tags} tag(s)")

    return out


def build_locations(loc, states, prov_centroids, state_centroids):
    """{DisplayName: {id, coords, aliases}} covering states and VP cities."""
    state_names = {k: v for k, v in loc.items() if re.fullmatch(r"STATE_\d+", k)}

    # VICTORY_POINTS_<province_id> is the default name; <TAG>_VICTORY_POINTS_<id>
    # is that country's own name for the same place -> alias.
    vp_names   = {}
    vp_aliases = defaultdict(set)
    for key, value in loc.items():
        m = re.fullmatch(r"(?:([A-Z]{3})_)?VICTORY_POINTS_(\d+)", key)
        if not m:
            continue
        pid = int(m[2])
        if m[1]:
            vp_aliases[pid].add(value)
        else:
            vp_names[pid] = value

    # A handful of VPs ship only tag-specific names with no default — province
    # 9843 is "Peking" to Japan and nothing to anyone else. Without a display
    # name the city would be dropped entirely, taking its aliases with it, so
    # promote the most common variant (alphabetical tie-break for determinism).
    for pid, variants in vp_aliases.items():
        if pid in vp_names and vp_names[pid]:
            continue
        if variants:
            vp_names[pid] = sorted(variants)[0]  # deterministic pick

    out = {}
    collisions = []

    def add(name, entry, kind):
        """Insert, resolving name clashes between a city and a state."""
        if name not in out:
            out[name] = entry
            return
        existing = out[name]
        if existing.get("_kind") == kind:
            return  # genuine duplicate of the same kind — first wins
        # A city and a state share a name (e.g. Hamburg the city sits in
        # Hamburg the state). If the city lies inside that state they're the
        # same place to a reader, so keep the city's more precise pin and drop
        # the state. If they're far apart, keep both, disambiguating the state.
        city  = existing if existing["_kind"] == "city" else entry
        state = existing if existing["_kind"] == "state" else entry
        dx = city["coords"][0] - state["coords"][0]
        dy = city["coords"][1] - state["coords"][1]
        if (dx * dx + dy * dy) ** 0.5 <= 60:
            out[name] = city
        else:
            out[name] = city
            out[f"{name} (state)"] = state
            collisions.append(name)

    # States first, so a same-named city overwrites with the tighter pin.
    for st in states:
        coords = state_centroids.get(st["id"])
        if coords is None:
            continue
        name = state_names.get(st["name_key"] or "")
        if not name:
            continue
        add(name, {
            "id": st["id"], "coords": list(coords), "aliases": [], "_kind": "state",
        }, "state")

    for st in states:
        for pid, _points in st["victory_points"]:
            name = vp_names.get(pid)
            coords = prov_centroids.get(pid)
            if not name or coords is None:
                continue
            aliases = sorted(a for a in vp_aliases.get(pid, ()) if a != name)
            add(name, {
                "id": pid, "coords": list(coords), "aliases": aliases, "_kind": "city",
            }, "city")

    for entry in out.values():
        entry.pop("_kind", None)
    return out, collisions


def merge_location_aliases(locations, ref_dir):
    """Fold assets/reference/hoi4/location_aliases.json into the location index.

    HoI4 posters lean on wartime colloquialisms the game itself never uses —
    "Pearl Harbor" is Honolulu in-game, "Stalingrad" survives but "El Alamein"
    doesn't. This is the curated overlay for those, mirroring how
    country_aliases.json layers extra names onto tags.json. Shape:
    {"Honolulu": ["Pearl Harbor"], ...} — keys must be canonical display
    names already in provinces.json."""
    path = ref_dir / "location_aliases.json"
    if not path.exists():
        return 0
    extra = json.loads(path.read_text(encoding="utf-8"))
    merged = 0
    unknown = []
    for name, names in extra.items():
        if name.startswith("_"):
            continue  # "_comment" etc., same convention as event_overrides.json
        entry = locations.get(name)
        if entry is None:
            unknown.append(name)
            continue
        combined = set(entry.get("aliases", [])) | set(names)
        entry["aliases"] = sorted(combined - {name})
        merged += 1
    if unknown:
        log(f"  WARNING: location_aliases.json references {len(unknown)} unknown "
            f"location(s), ignored: {', '.join(sorted(unknown)[:5])}")
    return merged


def resolve_game_dir(explicit):
    if explicit:
        p = Path(explicit)
        if not p.is_dir():
            sys.exit(f"ERROR: --game-dir {p} is not a directory")
        return p
    for guess in DEFAULT_INSTALL_GUESSES:
        p = Path(guess)
        if (p / "map" / "definition.csv").exists():
            return p
    sys.exit("ERROR: couldn't find a HoI4 install; pass --game-dir explicitly.")


def resolve_mod_dir(explicit):
    """--mod accepts a known name ('kaiserreich'), a bare workshop id, or a
    path. Returns (path, default_game_id)."""
    if not explicit:
        return None, GAME
    key = explicit.strip().lower()
    if key in KNOWN_MODS:
        workshop_id, default_game = KNOWN_MODS[key]
        for root in WORKSHOP_ROOTS:
            candidate = Path(root) / workshop_id
            if candidate.is_dir():
                return candidate, default_game
        sys.exit(f"ERROR: {explicit} (workshop id {workshop_id}) not found in any "
                 f"known workshop folder; pass the path to --mod instead.")
    direct = Path(explicit)
    if direct.is_dir():
        return direct, GAME
    for root in WORKSHOP_ROOTS:  # bare workshop id
        candidate = Path(root) / explicit
        if candidate.is_dir():
            return candidate, GAME
    sys.exit(f"ERROR: --mod {explicit} is not a directory, known mod name, or "
             f"workshop id.")


def main():
    ap = argparse.ArgumentParser(description="Build HoI4 (or HoI4 mod) reference data")
    ap.add_argument("--game-dir", default="", help="path to the HoI4 install")
    ap.add_argument("--mod", default="",
                    help="mod to layer on top: a known name (kaiserreich), a "
                         "Steam Workshop id, or a path to the mod folder")
    ap.add_argument("--game", default="",
                    help="output game id under assets/reference/ "
                         "(default: hoi4, or the known mod's id e.g. hoi4-kr)")
    ap.add_argument("--check", action="store_true",
                    help="parse + validate but write nothing")
    args = ap.parse_args()

    game_dir = resolve_game_dir(args.game_dir)
    mod_dir, default_game = resolve_mod_dir(args.mod)
    game_id = args.game or default_game

    repo_root = Path(__file__).resolve().parent.parent
    ref_dir = repo_root / "assets" / "reference" / game_id
    ref_dir.mkdir(parents=True, exist_ok=True)

    files = GameFiles(game_dir, mod_dir)

    log(f"HoI4 install: {game_dir}")
    if mod_dir:
        log(f"Mod overlay : {mod_dir}")
        log(f"  {len(files.replace_paths)} replace_path directive(s) from descriptor.mod")
    log(f"Output game : {game_id}  ->  {ref_dir}")

    log("Loading localisation ...")
    layers = files.localisation_layers()
    loc = finalise_localisation(resolve_references(load_localisation(layers)))
    log(f"  {len(loc)} key(s) from {len(layers)} layer(s)")

    # Map-keyed strings come from the mod alone when it owns the map, so a
    # stale base name can't attach itself to a reused numeric id.
    if files.owns_map_data():
        map_layers = files.localisation_layers(mod_only=True)
        map_loc = finalise_localisation(
            resolve_references(load_localisation(map_layers)))
        log(f"  {len(map_loc)} map key(s) from {len(map_layers)} mod layer(s) "
            f"(mod owns history/states + definition.csv)")
    else:
        map_loc = loc

    log("Parsing definition.csv ...")
    definition_path = files.file("map/definition.csv")
    colour_to_id, kinds = parse_definitions(definition_path)
    land = sum(1 for k in kinds.values() if k == "land")
    log(f"  {len(colour_to_id)} province colour(s), {land} land")

    log("Parsing history/states/*.txt ...")
    state_paths = files.dir_files("history/states")
    states = parse_state_files(state_paths)
    total_vps = sum(len(s["victory_points"]) for s in states)
    log(f"  {len(states)} state(s) from {len(state_paths)} file(s), "
        f"{total_vps} victory point(s)")

    log("Rasterising provinces.bmp (this takes a few seconds) ...")
    labels, pixel_counts = compute_province_pixels(
        files.file("map/provinces.bmp"), colour_to_id)
    log(f"  {labels.shape[1]}x{labels.shape[0]}, "
        f"{len(pixel_counts)} province(s) present on the map")

    log("Computing province centroids ...")
    vp_pids = {pid for st in states for pid, _ in st["victory_points"]}
    prov_centroids, prov_snapped = centroids_for_groups(
        labels, {pid: (pid,) for pid in vp_pids})
    log(f"  {len(prov_centroids)} VP province centroid(s) ({prov_snapped} snapped)")

    log("Computing state centroids ...")
    state_centroids, state_snapped = centroids_for_groups(
        labels, {st["id"]: st["provinces"] for st in states if st["provinces"]})
    log(f"  {len(state_centroids)} state centroid(s) ({state_snapped} snapped)")

    log("Building tags.json ...")
    tags = build_tags(files, loc, ref_dir)
    log(f"  {len(tags)} country tag(s)")

    log("Building provinces.json ...")
    locations, collisions = build_locations(
        map_loc, states, prov_centroids, state_centroids)
    log(f"  {len(locations)} location(s)")
    aliased = merge_location_aliases(locations, ref_dir)
    if aliased:
        log(f"  merged hand-curated aliases onto {aliased} location(s)")
    if collisions:
        log(f"  {len(collisions)} name(s) disambiguated as '<name> (state)': "
            f"{', '.join(sorted(collisions)[:5])}"
            f"{' …' if len(collisions) > 5 else ''}")

    # ── Validation ────────────────────────────────────────────────────────────
    height, width = labels.shape
    problems = []
    for name, entry in locations.items():
        x, y = entry["coords"]
        if not (0 <= x < width and 0 <= y < height):
            problems.append(f"{name}: coords {entry['coords']} outside map")
    probes = VALIDATION_PROBES.get(game_id, VALIDATION_PROBES[GAME])
    for probe in probes["locations"]:
        if probe not in locations:
            problems.append(f"expected location {probe!r} missing")
    for probe in probes["tags"]:
        if probe not in tags:
            problems.append(f"expected tag {probe!r} missing")

    if problems:
        log("\nVALIDATION PROBLEMS:")
        for p in problems[:20]:
            log(f"  - {p}")
        sys.exit(1)
    log("Validation OK.")

    if args.check:
        log("\n--check: nothing written.")
        return

    (ref_dir / "tags.json").write_text(
        json.dumps(tags, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    (ref_dir / "provinces.json").write_text(
        json.dumps(locations, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    log(f"\nWrote {ref_dir/'tags.json'}")
    log(f"Wrote {ref_dir/'provinces.json'}")


if __name__ == "__main__":
    main()
