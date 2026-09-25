@echo off
cd /d "%~dp0"
echo Setting up (only needed once per computer). This takes a few minutes...
python -m venv .venv
if errorlevel 1 (echo Python is not installed. Install Python 3.12+ from python.org, tick "Add to PATH", then retry. & pause & exit /b 1)
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
echo.
echo If the last line above says "passed", setup worked.
pause
