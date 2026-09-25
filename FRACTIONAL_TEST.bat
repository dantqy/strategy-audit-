@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo ONE-TIME TEST: can the PAPER account buy part of a share (0.1 Apple share, fake money)?
echo You will be asked to type YES. Run this while the US market is open.
echo.
.venv\Scripts\python scripts\fractional_test.py
pause
