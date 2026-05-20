@echo off
setlocal
cd /d "%~dp0\.."

set "LATEST="
for %%F in (eu4-paradox-in\data\discord\*.html) do set "LATEST=%%F"
if defined LATEST (
  echo Preprocessing %LATEST%
  python tools\preprocess.py "%LATEST%" ^
    --out eu4-paradox-in\data\events.json ^
    --tags eu4-paradox-in\data\reference\eu4\tags.json ^
    --raw-tags eu4-paradox-in\data\reference\eu4\00_countries.txt ^
    --aliases eu4-paradox-in\data\reference\eu4\aliases.json ^
    --untagged-log eu4-paradox-in\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in eu4-paradox-in\data\discord\ — skipping preprocess
)

python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/eu4-paradox-in/view.html
python -m http.server 8000
