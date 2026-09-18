@echo off
REM Installs the optional extras. The app runs without them, with reduced
REM phone/email analysis - this just turns the full feature set on.
setlocal
cd /d "%~dp0"
echo Installing dependencies for OSINT Lookup...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Done. Start the app with run.bat
pause
