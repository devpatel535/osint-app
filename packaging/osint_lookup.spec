# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

Builds in one of two modes, chosen by the OSINT_ONEDIR environment variable:

    pyinstaller --noconfirm packaging/osint_lookup.spec          # one file
    OSINT_ONEDIR=1 pyinstaller --noconfirm ... (set in the shell) # one folder

Why both, and why one-folder is the one we recommend:

A one-FILE PyInstaller binary carries a compressed payload that its bootloader
unpacks into a temporary directory and executes at run time. That is, step for
step, what a malware dropper does, so antivirus heuristics flag it - and
because plenty of real malware is written with PyInstaller, the stock
bootloader's own bytes are a known signature too. The result is a steady stream
of false positives on a perfectly clean build.

A one-FOLDER build performs no runtime self-extraction: the interpreter, the
libraries and the data sit next to the .exe as ordinary files. The dropper
heuristic has nothing to fire on, and the false-positive rate drops sharply.
It costs the user a ZIP to unpack instead of a single file to double-click.
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
DATA = ROOT / "osintapp" / "data"

ONEDIR = os.environ.get("OSINT_ONEDIR", "").strip().lower() in {"1", "true", "yes"}

block_cipher = None

# Everything in osintapp/data is read at runtime via paths.resource_path(),
# which looks under sys._MEIPASS/data in a frozen build - hence the 'data'
# destination here. This holds in one-folder mode too: PyInstaller sets
# sys._MEIPASS to the application directory there.
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
    # touches. Besides the size saving, a smaller import surface means fewer
    # unrelated modules for a heuristic scanner to take exception to.
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

# Shared EXE settings. UPX stays off in both modes: it saves a few MB, but
# packed executables are themselves a well-known AV heuristic trigger.
common = dict(
    name="OSINT-Lookup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # no terminal window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(DATA / "app.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,   # binaries go beside the exe, not inside it
        **common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="OSINT-Lookup",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        upx_exclude=[],
        runtime_tmpdir=None,
        **common,
    )
