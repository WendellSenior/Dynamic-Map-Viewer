# Dynamic Map Viewer

Interactive timeline + map for reviewing Paradox multiplayer campaigns after the fact. Loads a Discord export, places event dots on snapshot maps, and lets you scrub through dates.

## Layout

```
index.html              page shell
assets/style.css        styles
assets/app.js           viewer logic (vanilla JS, no build step)
data/events.json        tagged events (output of tools/preprocess.py)
data/snapshots.json     map snapshots: { date, image, label } + width/height config
data/coords.json        country / province name -> [x, y] lookup (pixel space)
data/maps/              snapshot images (PNG/SVG)
tools/preprocess.py     Discord JSON export -> data/events.json
.nojekyll               disable Jekyll processing on GitHub Pages
```

## Tag format players use in Discord

```
[FRA][1789-07-14] [PROV:Paris] The Bastille has fallen!
```

`[COUNTRY]` is a 2–4 letter tag, `[YYYY-MM-DD]` is the in-game date, `[PROV:Name]` is optional and overrides the country's default coordinates.

## Workflow

1. Export the campaign Discord channel as JSON via DiscordChatExporter.
2. `python tools/preprocess.py path/to/export.json` — writes `data/events.json`.
3. Drop snapshot map images into `data/maps/` and register each in `data/snapshots.json` with its in-game date.
4. Maintain `data/coords.json` with the pixel coordinates of countries/provinces on those snapshots. (A future preprocessor will derive this from Paradox game files.)
5. Open `index.html` locally, or push to GitHub Pages.

## Local preview

Browsers block `fetch()` from `file://`, so serve over HTTP:

```
python -m http.server 8000
```

Then visit `http://localhost:8000/`.
