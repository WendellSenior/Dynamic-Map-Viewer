@echo off
setlocal
cd /d "%~dp0\.."

set "HAS_HTML="
for %%F in (eu4-paradox-in\data\discord\*.html) do set "HAS_HTML=1"
if defined HAS_HTML (
  echo Preprocessing all exports in eu4-paradox-in\data\discord\
  python tools\preprocess.py eu4-paradox-in\data\discord ^
    --out eu4-paradox-in\data\events.json ^
    --tags eu4-paradox-in\data\reference\eu4\tags.json ^
    --raw-tags eu4-paradox-in\data\reference\eu4\00_countries.txt ^
    --aliases eu4-paradox-in\data\reference\eu4\aliases.json ^
    --untagged-log eu4-paradox-in\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in eu4-paradox-in\data\discord\ -- skipping preprocess
)

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/eu4-paradox-in/view.html
python -m http.server 8000
