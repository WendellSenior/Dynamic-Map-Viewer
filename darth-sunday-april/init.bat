@echo off
setlocal
cd /d "%~dp0\.."

set "LATEST="
for %%F in (darth-sunday-april\data\discord\*.html) do set "LATEST=%%F"
if defined LATEST (
  echo Preprocessing %LATEST%
  python tools\preprocess.py "%LATEST%" ^
    --out darth-sunday-april\data\events.json ^
    --tags darth-sunday-april\data\reference\eu5\tags.json ^
    --aliases darth-sunday-april\data\reference\eu5\aliases.json ^
    --untagged-log darth-sunday-april\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in darth-sunday-april\data\discord\ -- skipping preprocess
)

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/darth-sunday-april/view.html
python -m http.server 8000
