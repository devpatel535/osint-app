@echo off
REM ---------------------------------------------------------------------------
REM  Run OSINT Lookup from source on Windows.
REM
REM  No packaged executable is involved, so there is nothing for antivirus to
REM  object to - this path always works, even when a downloaded .exe is blocked.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

REM pythonw.exe runs a GUI app with no console window behind it. Prefer it,
REM but fall back to python.exe so errors stay visible if something is wrong.
set "PY="
for %%C in (pythonw.exe python.exe) do (
    if not defined PY (
        where %%C >nul 2>nul && set "PY=%%C"
    )
)

if not defined PY goto :nopython

REM Windows ships a stub at python.exe that only opens the Microsoft Store.
REM Running it would silently do nothing, so check we have a real interpreter.
%PY% -c "import sys; sys.exit(0)" >nul 2>nul
if not %errorlevel%==0 goto :nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
if not %errorlevel%==0 (
    echo Your Python is too old. OSINT Lookup needs 3.9 or newer.
    %PY% --version
    echo Install a current version from https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -c "import tkinter" >nul 2>nul
if not %errorlevel%==0 (
    echo This Python was built without Tk, so the window cannot be created.
    echo Reinstall Python from python.org and leave "tcl/tk and IDLE" ticked.
    pause
    exit /b 1
)

if /i "%PY%"=="pythonw.exe" (
    start "" pythonw "run.py"
) else (
    python "run.py"
)
exit /b 0

:nopython
echo.
echo ============================================================
echo   Python was not found.
echo ============================================================
echo.
echo  Quickest fix - run this in the same window:
echo.
echo      winget install -e --id Python.Python.3.12
echo.
echo  Then close this window, open a new one, and run run.bat again.
echo.
echo  No winget? Download the installer from
echo  https://www.python.org/downloads/ and tick
echo  "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1
