#!/usr/bin/env python3
"""Launcher for OSINT Lookup.

Double-click this on Windows, or run `python run.py`. It exists mainly to fail
*usefully*: the two things that actually go wrong on a fresh machine are an old
Python and a Python built without Tk, and the default tracebacks for both are
not something a non-developer can act on.
"""

from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 9)

# Allow running straight from a source checkout without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _die(title: str, body: str) -> int:
    message = f"{title}\n\n{body}"
    print(message, file=sys.stderr)
    try:  # a dialog is more use than stderr when launched by double-click
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, body)
        root.destroy()
    except Exception:  # noqa: BLE001 - if Tk is the thing that is broken
        pass
    return 1


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        return _die(
            "Python is too old",
            f"OSINT Lookup needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.\n"
            f"You are running {sys.version.split()[0]}.\n\n"
            "Install a current Python from https://www.python.org/downloads/",
        )

    try:
        import tkinter  # noqa: F401
    except ImportError:
        return _die(
            "Tkinter is missing",
            "This Python was built without Tk, so the window cannot be created.\n\n"
            "On Windows: reinstall Python from python.org and leave the\n"
            "'tcl/tk and IDLE' option ticked.\n\n"
            "On Linux: install the python3-tk package.",
        )

    from osintapp.ui.main_window import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
