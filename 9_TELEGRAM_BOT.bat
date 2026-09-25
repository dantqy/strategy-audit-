@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup has not been done on this computer yet. Double-click 0_FIRST_TIME_SETUP.bat first.
  pause
  exit /b 1
)
title Moomoo paper scanner - Telegram bot (keep open)
echo Keep this window AND OpenD open. Closing either stops the bot.
echo Control it from Telegram: send /help to your bot.
.venv\Scripts\python scripts\telegram_bot.py
pause
