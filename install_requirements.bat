@echo off
REM ---------------------------------------------------------------------------
REM  Optional extras for OSINT Lookup.
REM
REM  The app runs without these - phone and email lookups are simply reduced,
REM  and it tells you so in the results. This turns the full feature set on.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
    echo Python was not found. Run run.bat first - it explains how to install it.
    pause
    exit /b 1
)

echo Installing optional extras for OSINT Lookup...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo.
    echo Installation failed. The app will still run with reduced
    echo phone and email analysis - start it with run.bat.
    pause
    exit /b 1
)

echo.
echo Done. Start the app with run.bat
pause
