@echo off
REM ---------------------------------------------------------------------------
REM  OSINT Lookup - run from source on Windows.
REM  Uses pythonw.exe when available so no console window sits behind the app.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "run.py"
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "run.py"
    goto :eof
)

echo Python was not found on your PATH.
echo Install Python 3.9 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
pause
