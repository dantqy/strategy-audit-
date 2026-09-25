@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo Testing the strategy on 2019-today history. The first run takes ~30 min; later runs are fast.
echo Keep OpenD open until it finishes.
.venv\Scripts\python scripts\run_backtest.py
pause
