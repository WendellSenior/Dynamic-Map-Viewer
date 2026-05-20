@echo off
setlocal
cd /d "%~dp0\.."

set "HAS_HTML="
for %%F in (eu5-tbd\data\discord\*.html) do set "HAS_HTML=1"
if defined HAS_HTML (
  echo Preprocessing all exports in eu5-tbd\data\discord\
  python tools\preprocess.py eu5-tbd\data\discord ^
    --out eu5-tbd\data\events.json ^
    --tags eu5-tbd\data\reference\eu5\tags.json ^
    --raw-tags eu5-tbd\data\reference\eu5\00_countries.txt ^
    --aliases eu5-tbd\data\reference\eu5\aliases.json ^
    --untagged-log eu5-tbd\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in eu5-tbd\data\discord\ -- skipping preprocess
)

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/eu5-tbd/view.html
python -m http.server 8000
