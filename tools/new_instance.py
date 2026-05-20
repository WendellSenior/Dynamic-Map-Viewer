"""Scaffold a new campaign folder. Prompts for folder name, game, and label, then
writes view.html, init.bat, empty data files, and stub reference folder."""

import json
import shutil
import sys
from pathlib import Path


VIEW_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <header>
    <h1>Dynamic Map Viewer — __TITLE__</h1>
    <div class="header-right">
      <label class="filter-toggle" id="resolution-toggle-wrap" hidden>
        Quality:
        <select id="resolution-toggle">
          <option value="full">Full</option>
          <option value="lowres">Half</option>
        </select>
      </label>
      <label class="filter-toggle">
        Show:
        <select id="filter">
          <option value="all">All events</option>
          <option value="past">Past only</option>
        </select>
      </label>
      <div id="current-date" aria-live="polite"></div>
    </div>
  </header>

  <main>
    <div class="left-col">
      <div id="map-container">
        <div id="map-frame">
          <img id="map-image" alt="Campaign map" decoding="async">
          <div id="event-dots"></div>
        </div>
      </div>
      <section id="browser">
        <div class="tabs" role="tablist">
          <button class="tab active" data-tab="events" role="tab">
            Events <span id="event-count" class="count"></span>
          </button>
        </div>
        <div id="tab-events" class="tab-panel active" role="tabpanel">
          <table id="events-table">
            <thead>
              <tr>
                <th class="col-date">Date</th>
                <th class="col-tag">Tag</th>
                <th class="col-country">Country</th>
                <th class="col-province">Province</th>
                <th class="col-author">Author</th>
                <th class="col-snippet">Title / snippet</th>
              </tr>
            </thead>
            <tbody id="events-tbody"></tbody>
          </table>
        </div>
      </section>
    </div>
    <aside id="event-panel">
      <p class="empty">Click a dot or a row to see the event.</p>
    </aside>
  </main>

  <footer>
    <div class="timeline-track">
      <div id="timeline-marks"></div>
      <input type="range" id="timeline" min="0" max="1000" value="0" step="1" aria-label="Timeline">
    </div>
    <div id="timeline-labels"></div>
  </footer>

  <script>window.CAMPAIGN_GAME = '__GAME__';</script>
  <script src="../assets/app.js"></script>
</body>
</html>
"""


EDITOR_BAT_TEMPLATE = """@echo off
setlocal
cd /d "%~dp0\\.."
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/__FOLDER__/__EDITOR__.html
python -m http.server 8000
"""


INIT_BAT_TEMPLATE = """@echo off
setlocal
cd /d "%~dp0\\.."

set "LATEST="
for %%F in (__FOLDER__\\data\\discord\\*.html) do set "LATEST=%%F"
if defined LATEST (
  echo Preprocessing %LATEST%
  python tools\\preprocess.py "%LATEST%" ^
    --out __FOLDER__\\data\\events.json ^
    --tags __FOLDER__\\data\\reference\\__GAME__\\tags.json ^
    --raw-tags __FOLDER__\\data\\reference\\__GAME__\\00_countries.txt ^
    --aliases __FOLDER__\\data\\reference\\__GAME__\\aliases.json ^
    --untagged-log __FOLDER__\\data\\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in __FOLDER__\\data\\discord\\ -- skipping preprocess
)

python tools\\downsample_maps.py
python tools\\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/__FOLDER__/view.html
python -m http.server 8000
"""


def slugify(name):
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name)
    return cleaned.strip("-_").lower()


def prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    return ans or default


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    print()
    print("=== New campaign ===")
    print()
    raw = prompt("Folder name (e.g. eu5-myrun)")
    if not raw:
        print("Aborted.")
        return
    folder = slugify(raw)
    if not folder:
        print("Folder name is empty after normalisation. Aborted.")
        return
    if folder != raw:
        print(f"  (normalised to '{folder}')")

    folder_path = repo_root / folder
    if folder_path.exists():
        print(f"Already exists: {folder_path}. Aborted.")
        return

    game_default = folder.split("-", 1)[0]
    game = slugify(prompt("Game tag", game_default)) or game_default

    default_label = raw.replace("-", " ").replace("_", " ").strip().title()
    label = prompt("Display label for the hub card", default_label)

    print(f"\nCreating {folder}/ for game '{game}' …")

    maps_dir = folder_path / "data" / "maps"
    discord_dir = folder_path / "data" / "discord"
    ref_dir = folder_path / "data" / "reference" / game
    for d in (maps_dir, discord_dir, ref_dir):
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()

    write_json(folder_path / "data" / "events.json", {"events": []})
    snapshots_init = {"config": {"width": 0, "height": 0}, "snapshots": []}
    write_json(folder_path / "data" / "snapshots.json", snapshots_init)
    write_json(folder_path / "data" / "coords.json",
               {"countries": {}, "provinces": {}})
    write_json(folder_path / "data" / "sessions.json", {"sessions": []})
    (ref_dir / "aliases.json").write_text("{}\n", encoding="utf-8")

    # Port over reference data + map dimensions from an existing campaign of the same game.
    ref_source = next(
        (
            c for c in sorted(repo_root.iterdir())
            if c.is_dir() and c != folder_path
            and (c / "data" / "reference" / game).is_dir()
            and any(
                f.is_file() and f.name != ".gitkeep"
                for f in (c / "data" / "reference" / game).iterdir()
            )
        ),
        None,
    )
    if ref_source:
        src_ref = ref_source / "data" / "reference" / game
        copied = 0
        for src_file in src_ref.iterdir():
            if src_file.is_file() and src_file.name != ".gitkeep":
                shutil.copy(src_file, ref_dir / src_file.name)
                copied += 1
        print(f"  copied {copied} reference file(s) from {ref_source.name}/data/reference/{game}/")

        src_snap_path = ref_source / "data" / "snapshots.json"
        if src_snap_path.exists():
            try:
                src_cfg = json.loads(src_snap_path.read_text(encoding="utf-8")).get("config") or {}
                if src_cfg.get("width") and src_cfg.get("height"):
                    snapshots_init["config"] = src_cfg
                    write_json(folder_path / "data" / "snapshots.json", snapshots_init)
                    print(f"  set map dimensions to {src_cfg['width']}x{src_cfg['height']}")
            except (json.JSONDecodeError, KeyError):
                pass

    view = (
        VIEW_HTML_TEMPLATE
        .replace("__GAME__", game)
        .replace("__TITLE__", label)
    )
    (folder_path / "view.html").write_text(view, encoding="utf-8")

    init = (
        INIT_BAT_TEMPLATE
        .replace("__FOLDER__", folder)
        .replace("__GAME__", game)
    )
    (folder_path / "init.bat").write_text(init, encoding="utf-8")

    # Editor tools: write the bat fresh per-campaign, copy the html from any
    # existing campaign that has one (sessions.html / calibrate.html are
    # game-agnostic, identical across campaigns).
    template_src = next(
        (c for c in sorted(repo_root.iterdir())
         if c.is_dir() and c != folder_path and (c / "sessions.html").exists()),
        None,
    )
    for editor in ("sessions", "calibrate", "events"):
        bat = (
            EDITOR_BAT_TEMPLATE
            .replace("__FOLDER__", folder)
            .replace("__EDITOR__", editor)
        )
        (folder_path / f"{editor}.bat").write_text(bat, encoding="utf-8")
        if template_src and (template_src / f"{editor}.html").exists():
            shutil.copy(template_src / f"{editor}.html", folder_path / f"{editor}.html")
    if template_src is None:
        print("  (no template campaign found — sessions.html / calibrate.html not copied)")

    # Append to the hub manifest so the new campaign appears on index.html automatically.
    campaigns_file = repo_root / "campaigns.json"
    if campaigns_file.exists():
        try:
            manifest = json.loads(campaigns_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {"campaigns": []}
    else:
        manifest = {"campaigns": []}
    existing_folders = {c.get("folder") for c in manifest.get("campaigns", [])}
    if folder not in existing_folders:
        manifest.setdefault("campaigns", []).append({
            "folder": folder,
            "game": game,
            "label": label,
            "dates": "—",
            "description": "",
            "hidden": False,
        })
        write_json(campaigns_file, manifest)
        print(f"  appended to campaigns.json (hub will list it on next load)")

    print(f"\n  Created {folder}/ ({label}, game={game})")
    print()
    print("Next steps:")
    print(f"  - Drop maps into       {folder}/data/maps/")
    print(f"  - Drop Discord exports {folder}/data/discord/   (use --media for image persistence)")
    if ref_source is None:
        print(f"  - Add reference files  {folder}/data/reference/{game}/  (no existing {game} campaign to copy from)")
        print(f"  - Update map dims in   {folder}/data/snapshots.json config")
    print(f"  - Launch with          {folder}\\init.bat")
    print(f"  - Edit campaign card   campaigns.json  (dates/description optional)")


if __name__ == "__main__":
    main()
