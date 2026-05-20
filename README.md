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

**Canonical format** — please use this going forward:

```
[Date:1337-04-01][Country:France][Location:Paris][Tag:History]
# Title
…body…
```

Fields:

- `Date` (required) — `YYYY-MM-DD` in in-game time. Many other formats are also accepted by the parser, but ISO is preferred.
- `Country` (optional) — full game name (`France`, `Ottomans`, `Aragon`).
- `Location` (optional) — province/city name. Maps to its pixel coords via the game's reference data.
- `Tag` (optional) — categorises the event; renders a themed icon on the map and in the events table. Choose from:

  | Tag | Icon | Use for |
  | --- | --- | --- |
  | `WarDec` | 🎺 | War declarations |
  | `Battle` | ⚔️ | Battles, sieges, military engagements |
  | `Character` | 👤 | Births, deaths, succession, personal stories |
  | `Trade` | 📦 | Trade goods, merchants, trade routes |
  | `Economy` | 💰 | Income, debt, taxation, currency |
  | `Discover` | 🚢 | Exploration, colonisation, new lands |
  | `Treaty` | 📜 | Peace deals, alliances, royal marriages |
  | `Meeting` | 🤝 | Diplomacy, councils, conferences |
  | `History` | ⏳ | General historical narration / background |
  | `Religion` | 🙏 | Generic faith / religious event |
  | `Catholic` | ✝️ | Catholic / Christian (alias: `Christian`) |
  | `Orthodox` | ☦️ | Eastern / Russian / Greek Orthodox |
  | `Muslim` | ☪️ | Islam (aliases: `Sunni`, `Shia`, `Sufi`) |
  | `Jewish` | ✡️ | Judaism (alias: `Hebrew`) |
  | `Hindu` | 🕉️ | Hinduism (alias: `Vedic`) |
  | `Buddhism` | ☸️ | Buddhism (aliases: `Dharma`, `Dharmic`, `Zen`) |
  | `Taoism` | ☯️ | Chinese folk religions (aliases: `YinYang`, `Confucianism`) |

Brackets may all sit on one line, or be split across several lines. Free text after the last bracket becomes the body.

Events without a `Tag` show the default red dot.

**Fallback format** — for backwards compatibility, the parser also accepts the older loose convention (date on line 1, country on line 2, body follows):

```
11 Nov 1444
Aragon

# The King of Barcelona
…body…
```

Bare follow-up posts from the same author within 5 minutes are merged in as continuation text for both formats.

## Image attachments

Image attachments on Discord posts (screenshots, fan art, etc.) are extracted by the preprocessor and rendered in the event detail panel. Discord's CDN URLs **expire after ~24 hours**, so for archival-quality campaigns export the channel with DiscordChatExporter's `--media` flag, which downloads attachments into a sibling `..._Files/` folder. The preprocessor rewrites those relative paths so the viewer can serve them from the campaign folder. Without `--media` the URLs still work, just only for a day after the export.

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
