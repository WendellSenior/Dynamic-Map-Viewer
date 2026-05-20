@echo off
setlocal
cd /d "%~dp0\.."

set "HAS_HTML="
for %%F in (darthsunday\data\discord\*.html) do set "HAS_HTML=1"
if defined HAS_HTML (
  echo Preprocessing all exports in darthsunday\data\discord\
  python tools\preprocess.py darthsunday\data\discord ^
    --out darthsunday\data\events.json ^
    --tags darthsunday\data\reference\eu5\tags.json ^
    --raw-tags darthsunday\data\reference\eu5\00_countries.txt ^
    --aliases darthsunday\data\reference\eu5\aliases.json ^
    --untagged-log darthsunday\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in darthsunday\data\discord\ -- skipping preprocess
)

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/darthsunday/view.html
python -m http.server 8000
