@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo Scanning 93 stocks for signals (read-only, no orders)...
.venv\Scripts\python scripts\run_scanner.py
pause
