@echo off
setlocal
cd /d "%~dp0"

python tools\preprocess_all.py
python tools\downsample_maps.py
python tools\refresh_snapshots.py

echo.
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/
python -m http.server 8000
