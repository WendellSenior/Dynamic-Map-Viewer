@echo off
setlocal
cd /d "%~dp0\.."
python tools\refresh_snapshots.py
echo Starting local server at http://localhost:8000/
echo Press Ctrl+C in this window to stop.
echo.
start "" http://localhost:8000/eu5-tbd/view.html
python -m http.server 8000
