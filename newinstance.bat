@echo off
setlocal
cd /d "%~dp0"
python tools\new_instance.py
echo.
pause
