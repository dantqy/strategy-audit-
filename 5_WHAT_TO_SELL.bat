@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo Your paper positions and when each should be sold (no orders sent)...
.venv\Scripts\python scripts\paper_order.py --exits
pause
