# Paradox Game Data — Attribution and Provenance

The files under `assets/reference/eu4/`, `assets/reference/eu5/` and
`assets/reference/hoi4/` are
**extracted from Paradox Interactive's published game data**, not authored
by this project. They are reproduced here in unchanged form to power the
viewer's country-name resolution, province-coordinate lookups, and
country-tag mapping.

## Files and their game-install origins

### EU5 (`assets/reference/eu5/`)

| File | Source in EU5 install |
|---|---|
| `country_names_l_english.yml` | `localization/english/country_names_l_english.yml` |
| `location_names/location_names_l_english.yml` | `localization/english/location_names/location_names_l_english.yml` |
| `location_names/location_names_*_l_english.yml` | `localization/english/location_names/location_names_<culture>_l_english.yml` — 66 culture-specific endonym files (French calls Nicosia "Nicosie", Portuguese calls Lisbon "Lisboa", German calls Munich "München", etc.). Each contains `location_id.<culture>_language: "Endonym"` entries that `parse_eu5_reference.py` flattens into the location's alias list, so a Discord poster can write `[Location:Nicosie]` and the viewer still pins the right spot. |
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

### HoI4 (`assets/reference/hoi4/`)

| File | Source in HoI4 install |
|---|---|
| `00_countries.txt` | `common/country_tags/00_countries.txt` — also fetched by the viewer at runtime |
| `definition.csv` | `map/definition.csv` (province id → RGB colour) |
| `localisation/countries_l_english.yml` | `localisation/english/countries_l_english.yml` |
| `localisation/state_names_l_english.yml` | `localisation/english/state_names_l_english.yml` |
| `localisation/victory_points_l_english.yml` | `localisation/english/victory_points_l_english.yml` |

| File | Derived by | Description |
|---|---|---|
| `tags.json` | `tools/parse_hoi4_reference.py` | `{tag: {name, aliases}}` country lookup |
| `provinces.json` | `tools/parse_hoi4_reference.py` | `{display_name: {id, coords, aliases}}` covering both states and victory-point cities |
| `country_aliases.json` | hand-curated | Everyday shorthand the game files never contain (`USSR`, `UK`, `Nazi Germany`, `Manchukuo`); merged into `tags.json` on rebuild |
| `location_aliases.json` | hand-curated | Alternative place spellings (`Pearl Harbor` → Honolulu, `Volgograd` → Stalingrad); merged into `provinces.json` on rebuild |

**`map/provinces.bmp` is deliberately NOT committed** (34 MB). Unlike EU4 and
EU5, HoI4 ships **no usable coordinates** — its `map/positions.txt` is zero
bytes — so `parse_hoi4_reference.py` derives every pin by rasterising that
bitmap: pixels are mapped colour → province id via `definition.csv`, then
averaged into province centroids and unioned into state centroids. Centroids
that would land outside a concave or multi-part shape are snapped onto the
nearest pixel that genuinely belongs to that state, so pins never drift into
the sea or a neighbour. The BMP is therefore only needed when regenerating
after a patch, and regeneration requires a local HoI4 install.

Coordinates come out in HoI4's native **5632×2048** map space — the same
space EU4 uses, which is a useful cross-check: HoI4's derived Stockholm lands
at `[3086, 323]` against EU4's shipped `[3085, 325]`.

Two HoI4-specific naming wrinkles the parser handles:

- **Countries are named per ideology.** `GER` is "Germany", but also "German
  Reich" (fascism), "German Republic" (democratic) and "Socialist Republic of
  Germany" (communism). All variants become aliases, so `[Country:German
  Reich]` resolves. Where several tags share a name — "Germany" is claimed by
  `GER`, `WGR` and `DDR`; "China" by `CHI`, `PRC`, `MAN` and `RNG` — the name
  is awarded first-come-first-served in `00_countries.txt` order (the 1936
  majors head that file), and the losers fall back to their own ruling-party
  name from `history/countries/` ("Chinese Soviet Republic" for communist
  `PRC`). Without this the lookup's last-write-wins would hand
  `[Country:Germany]` to post-war `WGR`.
- **Period place names are canonical.** The game is set in 1936, so
  `provinces.json` keys are "Danzig", "Stalingrad", "Bombay" — not Gdansk,
  Volgograd, Mumbai. The modern names live in `location_aliases.json`.

## Licensing

Europa Universalis IV, Europa Universalis V and Hearts of Iron IV are
copyright Paradox Interactive. The game data redistributed in this folder is included
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

When a patch changes the source files:

1. Re-extract from your local game install (don't commit the install — only the listed files).
2. For EU5, run `python tools/parse_eu5_reference.py` to regenerate `tags.json` + `provinces.json`.
3. For EU4, run `python tools/parse_positions.py` to regenerate `provinces.json`.
4. For HoI4, run `python tools/parse_hoi4_reference.py` (needs numpy + Pillow and
   a local install — it reads `map/provinces.bmp`, which isn't committed).
   Add `--check` to validate without writing, or `--game-dir <path>` if the
   install isn't in a standard Steam location.
5. Commit only the files listed in this README; everything else stays in the game install.

## Per-campaign extension point

The shared data here is read-only at runtime. Per-campaign customisation
(corrections, narrative tweaks) lives at:

- `<campaign>/data/reference/<game>/aliases.json` — interactive lookup cache from `tools/preprocess.py` for unknown country names that humans resolved during a Discord-export run.
- `<campaign>/data/coords.json` — pin positions, overrides the derived ones from `provinces.json`.
- `<campaign>/data/overrides.json` — per-event post-processing tweaks.
