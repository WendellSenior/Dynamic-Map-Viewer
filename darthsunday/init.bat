@echo off
setlocal
cd /d "%~dp0\.."

REM events.json for this campaign is owned by the GH-Actions Discord sync.
REM No local preprocess step here — see darthsunday/scripts/sync_events.py and
REM .github/workflows/discord-sync.yml.

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/darthsunday/view.html
python -m http.server 8000
