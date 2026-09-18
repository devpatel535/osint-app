"""Filesystem locations, resolved the same way whether we run from source or
from a PyInstaller one-file bundle.

Three distinct roots, deliberately kept apart:

* ``resource_path()``  - read-only data shipped inside the app (site list...).
  Under PyInstaller this lives in the temporary extraction dir, which is wiped
  on exit, so nothing writable may ever go here.
* ``data_dir()``       - per-user state that must survive upgrades: the search
  history database and settings.
* ``default_reports_dir()`` - where generated .txt reports land by default.
  Somewhere the user will actually look, i.e. Documents on Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "OSINT Lookup"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(*parts: str) -> Path:
    """Absolute path to a read-only file shipped in ``osintapp/data``."""
    if is_frozen():
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath("data", *parts)


def data_dir() -> Path:
    """Per-user writable directory for history.db and settings.json."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_reports_dir() -> Path:
    """Default destination for saved .txt reports.

    Not created here - creation is deferred until something is actually saved,
    so merely launching the app does not litter the user's Documents folder.
    """
    if sys.platform == "win32":
        documents = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents"
        if not documents.is_dir():
            documents = Path.home()
        return documents / f"{APP_DIR_NAME} Reports"
    return Path.home() / f"{APP_DIR_NAME} Reports"


def history_db_path() -> Path:
    return data_dir() / "history.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"
