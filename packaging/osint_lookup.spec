# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file Windows build.

Run from the repository root:

    pyinstaller --noconfirm packaging/osint_lookup.spec

Produces dist/OSINT-Lookup.exe - one file, no console window, with the site
catalogue and icon bundled inside.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
DATA = ROOT / "osintapp" / "data"

block_cipher = None

# Everything in osintapp/data is read at runtime via paths.resource_path(),
# which looks under sys._MEIPASS/data in a frozen build - hence the 'data'
# destination here.
datas = [(str(DATA), "data")]

# phonenumbers and dnspython ship large data subpackages that PyInstaller's
# static analysis does not always follow; naming them keeps the full feature
# set in the binary. They stay optional - if they are not installed, the build
# simply omits them and the app reports reduced capability at runtime.
hiddenimports = []
for module in (
    "phonenumbers",
    "phonenumbers.geocoder",
    "phonenumbers.carrier",
    "phonenumbers.timezone",
    "dns",
    "dns.resolver",
    "dns.rdatatype",
):
    try:
        __import__(module)
    except Exception:
        continue
    hiddenimports.append(module)

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim the parts of the stdlib and third-party tree a GUI OSINT tool never
    # touches; this is worth roughly 10-15 MB in the finished binary.
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy", "PIL", "pytest",
        "setuptools", "pip", "wheel", "test", "unittest", "pydoc_data",
        "selenium", "flask", "IPython", "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OSINT-Lookup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no terminal window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(DATA / "app.ico"),
)
