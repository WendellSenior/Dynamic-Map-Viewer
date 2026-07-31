@echo off
setlocal
cd /d "%~dp0\.."

set "HAS_HTML="
for %%F in (hoi4-kr-test\data\discord\*.html) do set "HAS_HTML=1"
if defined HAS_HTML (
  echo Preprocessing all exports in hoi4-kr-test\data\discord\
  python tools\preprocess.py hoi4-kr-test\data\discord ^
    --out hoi4-kr-test\data\events.json ^
    --tags assets\reference\hoi4-kr\tags.json ^
    --raw-tags assets\reference\hoi4-kr\00_countries.txt ^
    --aliases hoi4-kr-test\data\reference\hoi4-kr\aliases.json ^
    --untagged-log hoi4-kr-test\data\untagged.log ^
    --non-interactive
) else (
  echo No discord exports in hoi4-kr-test\data\discord\ -- skipping preprocess
)

python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/hoi4-kr-test/view.html
python -m http.server 8000
