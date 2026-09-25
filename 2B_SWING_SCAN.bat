@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo SWING IDEAS: today's big movers on heavy volume across the whole US market.
echo UNTESTED idea list - no orders are placed.
echo.
.venv\Scripts\python scripts\run_swing_scan.py
pause
