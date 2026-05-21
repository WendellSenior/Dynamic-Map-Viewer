# Paradox Game Data — Attribution and Provenance

The files under `assets/reference/eu4/` and `assets/reference/eu5/` are
**extracted from Paradox Interactive's published game data**, not authored
by this project. They are reproduced here in unchanged form to power the
viewer's country-name resolution, province-coordinate lookups, and
country-tag mapping.

## Files and their game-install origins

### EU5 (`assets/reference/eu5/`)

| File | Source in EU5 install |
|---|---|
| `country_names_l_english.yml` | `localization/english/country_names_l_english.yml` |
| `location_names_l_english.yml` | `localization/english/location_names_l_english.yml` |
| `country_capitals.txt` | `common/country_capitals.txt` (or similar) |
| `definitions.txt` | `map_data/definitions.txt` |
| `default.map` | `map_data/default.map` |
| `locations_to_colors.txt` | generated from map data |
| `locators_city.txt` | `map_data/locators/locators_city.txt` |

Project-generated indexes derived from the above (committed for runtime efficiency):

| File | Derived by | Description |
|---|---|---|
| `tags.json` | `tools/parse_eu5_reference.py` | `{tag: {name, aliases}}` country lookup |
| `provinces.json` | `tools/parse_eu5_reference.py` | `{location_id: [x, y]}` and display-name index |
| `country_aliases.json` | hand-curated | Per-tag list of extra player-spoken names (e.g. `MAM: ["Mamluks"]`); merged into `tags.json` on rebuild |

### EU4 (`assets/reference/eu4/`)

| File | Source in EU4 install |
|---|---|
| `00_countries.txt` | `common/country_tags/00_countries.txt` |
| `positions.txt` | `map/positions.txt` |

| File | Derived by | Description |
|---|---|---|
| `tags.json` | hand-curated overlay | Layered on top of `00_countries.txt` parsing |
| `provinces.json` | `tools/parse_positions.py` | `{province_id: [x, y]}` (y inverted from game's bottom-left to image top-left origin) |

## Licensing

Europa Universalis IV and Europa Universalis V are copyright Paradox
Interactive. The game data redistributed in this folder is included
solely to make the viewer functional out-of-the-box and is **not** placed
under this project's license.

If you fork or redistribute this repository:
- Anyone reusing the project is expected to own the corresponding Paradox
  game(s).
- If Paradox issues a takedown or asks for removal, delete this folder.
  The viewer is designed to fall back gracefully — country cells render
  as raw `countryRaw` text and province coordinates can be redefined per
  campaign via `data/coords.json`.

## Updating after a game patch

When EU5 or EU4 patches change the source files:

1. Re-extract from your local game install (don't commit the install — only the listed files).
2. For EU5, run `python tools/parse_eu5_reference.py` to regenerate `tags.json` + `provinces.json`.
3. For EU4, run `python tools/parse_positions.py` to regenerate `provinces.json`.
4. Commit only the files listed in this README; everything else stays in the game install.

## Per-campaign extension point

The shared data here is read-only at runtime. Per-campaign customisation
(corrections, narrative tweaks) lives at:

- `<campaign>/data/reference/<game>/aliases.json` — interactive lookup cache from `tools/preprocess.py` for unknown country names that humans resolved during a Discord-export run.
- `<campaign>/data/coords.json` — pin positions, overrides the derived ones from `provinces.json`.
- `<campaign>/data/overrides.json` — per-event post-processing tweaks.
