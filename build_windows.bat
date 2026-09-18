@echo off
REM ---------------------------------------------------------------------------
REM  Build OSINT-Lookup.exe (single file, no console window).
REM  Run this on Windows. The finished binary lands in dist\OSINT-Lookup.exe
REM  and needs no Python on the machine you copy it to.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
    echo Python was not found on your PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

echo.
echo [1/3] Installing build and runtime dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if not %errorlevel%==0 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/3] Building the executable...
python -m PyInstaller --noconfirm packaging\osint_lookup.spec
if not %errorlevel%==0 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo  Done.  Your application is:  dist\OSINT-Lookup.exe
echo  Copy that single file anywhere - no Python needed.
echo ==========================================================
pause
