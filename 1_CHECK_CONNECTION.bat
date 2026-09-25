@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo Checking that OpenD is running and logged in (read-only, no orders)...
.venv\Scripts\python scripts\check_moomoo_connection.py
echo.
echo Look at the list at the bottom: every line should say PASS.
pause
