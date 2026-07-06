# Dynamic Map Viewer

Interactive timeline + map for reviewing Paradox multiplayer campaigns after the fact. Loads a Discord export, places event dots on snapshot maps, and lets you scrub through dates. Hosted as a static site on GitHub Pages.

## Layout

```
index.html                       campaign hub — lists each campaign
campaigns.json                   per-campaign manifest (label, game, discord_sync block, …)
assets/style.css                 shared viewer styles
assets/app.js                    shared viewer logic (vanilla JS, no build step)
assets/reference/<game>/         shared game data — read-only at runtime
                                 (tags, locations, country lists, positions)
                                 See assets/reference/PARADOX-GAME-DATA.md.
tools/preprocess.py              Discord export → events.json
tools/sync_events.py             GH-Actions Discord poller (--campaign <folder>)
tools/roll_session.py            auto-closes the open session + opens the next
                                 (runs at the end of the Sunday heartbeat)
tools/parse_positions.py         EU4 positions.txt → provinces.json
tools/parse_eu5_reference.py     EU5 locators + names → provinces.json + tags.json
tools/new_instance.py            campaign scaffolder
<game>-<campaign>/               one folder per campaign
    view.html                    viewer page; declares window.CAMPAIGN_GAME
    init.bat                     local launcher (Windows)
    data/events.json             tagged events (preprocessor / sync output)
    data/snapshots.json          map snapshots + map dimensions
    data/coords.json             country / province → [x, y] manual lookup
    data/sessions.json           campaign session date ranges
    data/maps/                   snapshot images
    data/discord/                raw Discord exports (gitignored)
    data/reference/<game>/       per-campaign overrides only —
                                 just aliases.json (interactive lookup cache).
                                 Bulk game data lives in assets/reference/ above.
.github/workflows/discord-sync.yml  hourly Discord Bot API → events.json
                                    (one-campaign-per-workflow currently;
                                     channel_id read from campaigns.json)
```

Per-campaign local tools (`calibrate.html` / `calibrate.bat` for picking coords, `sessions.html` / `sessions.bat` for editing session ranges, `events.html` / `events.bat` for per-event override edits) live alongside `view.html` in each campaign folder.

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

  | Group | Tag | Icon | Use for |
  | --- | --- | --- | --- |
  | General | `WarDec` | 🎺 | War declarations |
  | | `Battle` | ⚔️ | Battles, sieges, military engagements |
  | | `Character` | 👤 | Births, deaths, succession, personal stories |
  | | `Trade` | 📦 | Trade goods, merchants, trade routes |
  | | `Economy` | 💰 | Income, debt, taxation, currency |
  | | `Discover` | 🚢 | Exploration, colonisation, new lands |
  | | `Treaty` | 📜 | Peace deals, alliances, royal marriages |
  | | `Meeting` | 🤝 | Diplomacy, councils, conferences (aliases: `Diplomacy`, `Interaction`) |
  | | `History` | ⏳ | General historical narration / background |
  | Religion | `Religion` | 🙏 | Generic faith / religious event |
  | | `Catholic` | ✝️ | Catholic / Christian (alias: `Christian`) |
  | | `Orthodox` | ☦️ | Eastern / Russian / Greek Orthodox |
  | | `Muslim` | ☪️ | Islam (aliases: `Sunni`, `Shia`, `Sufi`) |
  | | `Jewish` | ✡️ | Judaism (alias: `Hebrew`) |
  | | `Hindu` | 🕉️ | Hinduism (alias: `Vedic`) |
  | | `Buddhism` | ☸️ | Buddhism (aliases: `Dharma`, `Dharmic`, `Zen`) |
  | | `Taoism` | ☯️ | Chinese folk religions (aliases: `YinYang`, `Confucianism`) |
  | Civic | `Chaos` | 💥 | Anarchy, riots, revolts, rebellions, unrest |
  | | `Judge` | ⚖️ | Justice, courts, trials, verdicts, legal rulings |
  | | `Surrender` | 🏳️ | Capitulation, defeat, yield, ceasefire, armistice |
  | Sport | `Duel` | 🤺 | Duels, fencing (single combat — use `Battle` for engagements) |
  | | `Joust` | 🏇 | Tournaments, jousts, tourneys |
  | Awards | `First` | 🥇 | 1st place, victory, gold, champion |
  | | `Second` | 🥈 | 2nd place, silver, runner-up |
  | | `Third` | 🥉 | 3rd place, bronze |
  | Arts & Knowledge | `Map` | 🗺️ | Cartography, atlas, survey, geography |
  | | `Architecture` | 🏛️ | Monuments, edifices, construction, buildings |
  | | `Culture` | 🎭 | Theatre, drama, performance, the arts |
  | | `Painting` | 🎨 | Painting, art, fresco, mural, portraits |
  | | `Literature` | 📚 | Books, novels, poetry, writing |
  | | `Text` | ✒️ | Manuscripts, edicts, decrees, charters, correspondence |
  | | `Science` | 🔬 | Research, invention, experiment, scholarship |
  | | `Medicine` | 💊 | Plague, disease, doctors, pandemic, health |
  | Peoples & Hazards | `Native` | 🗿 | Indigenous, aboriginal, tribal peoples |
  | | `Secret` | 💼 | Espionage, spy, intrigue, conspiracy, plots |
  | | `Warning` | ⚠️ | Alerts, ultimatums, threats, danger |
  | | `Nuclear` | ☢️ | Nukes, atomic, radiation, fallout |
  | | `Biohazard` | ☣️ | Bioweapons, contamination, contagion |
  | | `Pirate` | 🏴‍☠️ | Pirates, piracy, raiders, corsairs, privateers |

Aliases are case-insensitive. Full list lives in `tools/preprocess.py` (`TAG_ALIASES`) and `tools/sync_events.py` (`VALID_TAGS`); both kept in sync.

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

The fastest path is `newinstance.bat` at the repo root — it scaffolds the folder, view.html, init.bat, snapshots dimensions, and a campaigns.json entry.

For an existing-game campaign (game reference data already in `assets/reference/<game>/`):

1. Run `newinstance.bat` (or `python tools/new_instance.py`). Prompts for folder name, game tag, label.
2. Drop snapshot images into `<campaign>/data/maps/` (run `python tools/refresh_snapshots.py` to register them).
3. Drop a Discord export into `<campaign>/data/discord/` and run `<campaign>/init.bat` — runs preprocess + downsample + serves on `http://localhost:8000/<campaign>/view.html`.
4. Use `calibrate.html` to pin country capitals into `data/coords.json`, `sessions.html` to set session date ranges, `events.html` to fine-tune individual events.

For a **new game** with no shared reference data yet:

1. Seed `assets/reference/<game>/` with the game's tag list + position/locator files (see `assets/reference/PARADOX-GAME-DATA.md` for the file list).
2. Run the relevant parser (`tools/parse_positions.py` for EU4-style games, `tools/parse_eu5_reference.py` for EU5) to derive `tags.json` + `provinces.json`.
3. Then proceed as above.

## Discord sync (alternative to local preprocessing)

The repo includes an hourly GitHub Actions workflow (`.github/workflows/discord-sync.yml`) that calls Discord's Bot API directly and commits new events to `<campaign>/data/events.json`. To enable for a campaign, add a `discord_sync` block to its `campaigns.json` entry:

```json
{
  "folder": "darthsunday",
  "game": "eu5",
  …
  "discord_sync": {
    "enabled": true,
    "channel_id": "1434628920371581079",
    "reference_game": "eu5"
  }
}
```

Requires a `DISCORD_TOKEN` repo secret with View Channels + Read Message History permissions. The workflow is single-campaign for now (hardcoded to `--campaign darthsunday`); fan out via matrix strategy for multiple synced campaigns. The local preprocess pipeline auto-skips campaigns with `discord_sync.enabled` to avoid clobbering live data.

A second workflow (`discord-session-heartbeat.yml`) covers the live Sunday play session: GitHub's cron scheduler drops sub-hourly schedules, so instead of many scheduled runs it fires **once** near session start and self-loops internally (sync → sleep 10 min → repeat) for the session window. When the window closes it runs `tools/roll_session.py`, which auto-rolls `sessions.json` if today's burst looks like a real session (Sunday + ≥25 posts today + in-game frontier ≥1 year past the open session's start; dates >30 years out are ignored as typos). The auto-set session end is the max in-game event date — nudge it in `sessions.html` if the end-of-session map export disagrees.

## Local preview

```
init.bat                      (root) opens the hub
<campaign>/init.bat           opens a specific campaign
```

Or manually: `python -m http.server 8000` from the repo root, then visit `http://localhost:8000/`.
