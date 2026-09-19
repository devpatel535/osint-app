@echo off
REM ---------------------------------------------------------------------------
REM  Build OSINT Lookup for Windows.
REM
REM  Produces both packagings:
REM    dist\OSINT-Lookup\OSINT-Lookup.exe   one folder  (recommended)
REM    dist\OSINT-Lookup.exe                one file    (AV-prone, see README)
REM
REM  A locally built binary is not subject to SmartScreen's "downloaded from
REM  the internet" reputation check, so building here is the simplest way
REM  around a download that your antivirus objects to.
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
echo [1/5] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not %errorlevel%==0 goto :failed

echo.
echo [2/5] Installing PyInstaller (compiling the bootloader from source)...
REM The stock wheel's bootloader is byte-identical for everyone and is itself
REM signatured by some AV engines, because plenty of real malware is built with
REM PyInstaller. Compiling it locally avoids that specific false positive. If
REM you have no C compiler this falls back to the prebuilt wheel, which is fine.
python -m pip install --no-binary :all: --no-cache-dir pyinstaller
if not %errorlevel%==0 (
    echo     Source build unavailable, using the prebuilt wheel instead.
    python -m pip install --force-reinstall pyinstaller
    if not %errorlevel%==0 goto :failed
)

echo.
echo [3/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist build-onedir rmdir /s /q build-onedir
if exist build-onefile rmdir /s /q build-onefile

echo.
echo [4/5] Building the one-folder version (recommended)...
set OSINT_ONEDIR=1
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build-onedir packaging\osint_lookup.spec
if not %errorlevel%==0 goto :failed
set OSINT_ONEDIR=

echo.
echo [5/5] Building the one-file version...
python -m PyInstaller --noconfirm --clean --distpath dist-onefile --workpath build-onefile packaging\osint_lookup.spec
if not %errorlevel%==0 goto :failed
move /y dist-onefile\OSINT-Lookup.exe dist\OSINT-Lookup.exe >nul
rmdir /s /q dist-onefile

echo.
echo ==========================================================
echo  Done.
echo.
echo   Recommended:  dist\OSINT-Lookup\OSINT-Lookup.exe
echo                 (keep that whole folder together)
echo.
echo   Single file:  dist\OSINT-Lookup.exe
echo                 (more convenient; more likely to upset AV)
echo ==========================================================
pause
exit /b 0

:failed
echo.
echo Build failed. See the messages above.
pause
exit /b 1
