@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
echo PAPER SELL (fake money). You will see a ticket and must type YES to send it.
echo.
set /p TICKER=Type the ticker to sell, e.g. AAPL, then press Enter: 
if "%TICKER%"=="" (echo No ticker typed. Nothing done. & pause & exit /b 0)
.venv\Scripts\python scripts\paper_order.py --ticker %TICKER% --side SELL
pause
