@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install -q fastapi "uvicorn[standard]" httpx >nul 2>&1
echo Starting the Strategy Audit Platform...  open http://127.0.0.1:8765  (demo: http://127.0.0.1:8765/#/demo)
echo Close this window to stop it.
.venv\Scripts\python -m strategy_audit.run
pause
