# Dynamic Map Viewer

Interactive timeline + map for reviewing Paradox multiplayer campaigns after the fact. Loads a Discord export, places event dots on snapshot maps, and lets you scrub through dates. Hosted as a static site on GitHub Pages.

## Layout

```
index.html               campaign hub — lists each campaign
assets/style.css         shared viewer styles
assets/app.js            shared viewer logic (vanilla JS, no build step)
tools/preprocess.py      Discord export → events.json (per-campaign)
tools/parse_positions.py EU4 positions.txt → provinces.json (per-game)
<game>-<campaign>/       one folder per campaign
    view.html            viewer page; declares window.CAMPAIGN_GAME
    init.bat             local launcher (Windows)
    data/events.json     tagged events (preprocessor output)
    data/snapshots.json  map snapshots + map dimensions
    data/coords.json     country / province → [x, y] manual lookup
    data/sessions.json   campaign session date ranges
    data/maps/           snapshot images
    data/discord/        raw Discord exports (gitignored)
    data/reference/<g>/  per-game reference: tags, positions, provinces
```

Per-campaign local tools (`calibrate.html` / `calibrate.bat` for picking coords, `sessions.html` / `sessions.bat` for editing session ranges) are gitignored — they live alongside `view.html` in a campaign folder when in use.

## Tag format used in Discord

Players head a post with a date line, optionally a country line:

```
11 Nov 1444
Aragon

# The King of Barcelona
…body…
```

Many date formats accepted (`11_Nov_1444`, `4_Mar_1453`, `26_4_1492`, `13th December 1513`, `1563`, `1673 May 10`). Country line is the full game name (`Aragon`, `Ottomans`, …). Bare follow-up posts from the same author within 5 minutes are merged in as continuation text.

## Workflow for a new campaign

1. Create `<game>-<short-name>/` next to the existing campaign folders.
2. Copy `view.html` from an existing campaign (e.g. `eu4-paradox-in/view.html`), then update its `<title>` and the `window.CAMPAIGN_GAME` value.
3. Drop snapshot images into `<campaign>/data/maps/` and register them in `<campaign>/data/snapshots.json`.
4. Place per-game reference files in `<campaign>/data/reference/<game>/` (for EU4: `00_countries.txt`, `positions.txt`; run `tools/parse_positions.py` to derive `provinces.json`).
5. Run `tools/preprocess.py <discord-export.html>` from the campaign folder to produce `data/events.json`.
6. Use the local `calibrate.html` (via `calibrate.bat`) to pin country capitals into `data/coords.json`.
7. Use `sessions.html` to set campaign session date ranges.
8. Add the campaign to the root `index.html` hub.

## Local preview

```
init.bat                      (root) opens the hub
<campaign>/init.bat           opens a specific campaign
```

Or manually: `python -m http.server 8000` from the repo root, then visit `http://localhost:8000/`.
