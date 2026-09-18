"""Colours, fonts and ttk styles.

Tk's native themes on Windows ('vista') ignore most colour options, so the
'clam' theme is used as the base everywhere - it is the only built-in theme
that honours background/foreground on every widget we style. The result then
looks the same on Windows, macOS and Linux.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# --- palette -------------------------------------------------------------
BG = "#0d1117"          # window background
SURFACE = "#161b22"     # cards, panels
SURFACE_HOVER = "#1f2630"
SURFACE_ALT = "#1c2128"  # inputs
BORDER = "#30363d"
BORDER_FOCUS = "#2f81f7"

TEXT = "#e6edf3"
TEXT_MUTED = "#8b949e"
TEXT_FAINT = "#6e7681"

ACCENT = "#2f81f7"
ACCENT_HOVER = "#4d95f9"
ACCENT_DISABLED = "#1b3a5b"
ACCENT_TEXT = "#ffffff"

SUCCESS = "#3fb950"
WARNING = "#d29922"
DANGER = "#f85149"
PURPLE = "#a371f7"
CYAN = "#39c5cf"

# Confidence -> colour, shared by the results tree and the legend.
CONFIDENCE_COLOURS = {
    "confirmed": SUCCESS,
    "likely": CYAN,
    "possible": WARNING,
    "info": TEXT_MUTED,
    "pivot": PURPLE,
    "error": DANGER,
}

# Query type -> (badge colour, single-letter badge)
QUERY_BADGES = {
    "username": (ACCENT, "@"),
    "email": (PURPLE, "@"),
    "phone": (SUCCESS, "#"),
    "name": (WARNING, "A"),
}


def _pick_family(root: tk.Misc, *candidates: str) -> str:
    available = set(tkfont.families(root))
    for family in candidates:
        if family in available:
            return family
    return "TkDefaultFont"


class Fonts:
    """Resolved font tuples. Built after the root window exists."""

    def __init__(self, root: tk.Misc):
        ui = _pick_family(root, "Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans", "Arial")
        mono = _pick_family(root, "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")
        self.family = ui
        self.title = (ui, 17, "bold")
        self.heading = (ui, 12, "bold")
        self.body = (ui, 10)
        self.body_bold = (ui, 10, "bold")
        self.small = (ui, 9)
        self.small_bold = (ui, 9, "bold")
        self.tiny = (ui, 8)
        self.search = (ui, 13)
        self.badge = (ui, 12, "bold")
        self.mono = (mono, 9)
        self.mono_small = (mono, 8)


def apply(root: tk.Misc) -> Fonts:
    """Install the dark theme on *root* and return the font set."""
    fonts = Fonts(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure(".", background=BG, foreground=TEXT, font=fonts.body,
                    borderwidth=0, focuscolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Card.TFrame", background=SURFACE, relief="flat")

    style.configure("TLabel", background=BG, foreground=TEXT, font=fonts.body)
    style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED, font=fonts.small)
    style.configure("SurfaceMuted.TLabel", background=SURFACE, foreground=TEXT_MUTED,
                    font=fonts.small)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=fonts.title)
    style.configure("Heading.TLabel", background=BG, foreground=TEXT, font=fonts.heading)

    # --- buttons ---------------------------------------------------------
    style.configure("TButton", background=SURFACE_ALT, foreground=TEXT,
                    font=fonts.body, padding=(14, 7), borderwidth=0, relief="flat")
    style.map("TButton",
              background=[("active", SURFACE_HOVER), ("pressed", SURFACE_HOVER),
                          ("disabled", SURFACE)],
              foreground=[("disabled", TEXT_FAINT)])

    style.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                    font=fonts.body_bold, padding=(20, 8))
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER),
                          ("disabled", ACCENT_DISABLED)],
              foreground=[("disabled", TEXT_FAINT)])

    style.configure("Danger.TButton", background=SURFACE_ALT, foreground=DANGER,
                    font=fonts.body, padding=(12, 6))
    style.map("Danger.TButton", background=[("active", "#3d1d1d")])

    style.configure("Link.TButton", background=SURFACE, foreground=ACCENT,
                    font=fonts.small, padding=(6, 2))
    style.map("Link.TButton", background=[("active", SURFACE_HOVER)])

    # --- inputs ----------------------------------------------------------
    style.configure("TEntry", fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=TEXT, insertcolor=TEXT, borderwidth=1,
                    relief="flat", padding=8)
    style.map("TEntry",
              fieldbackground=[("focus", SURFACE_ALT)],
              bordercolor=[("focus", BORDER_FOCUS)],
              lightcolor=[("focus", BORDER_FOCUS)],
              darkcolor=[("focus", BORDER_FOCUS)])

    style.configure("TCombobox", fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=TEXT, arrowcolor=TEXT_MUTED, borderwidth=1,
                    relief="flat", padding=6)
    style.map("TCombobox",
              fieldbackground=[("readonly", SURFACE_ALT)],
              background=[("readonly", SURFACE_ALT)],
              foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", SURFACE_ALT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_TEXT)

    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=fonts.body,
                    indicatorcolor=SURFACE_ALT, focuscolor=BG)
    style.map("TCheckbutton",
              background=[("active", BG)],
              indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_HOVER)])
    style.configure("Surface.TCheckbutton", background=SURFACE, foreground=TEXT,
                    indicatorcolor=SURFACE_ALT, focuscolor=SURFACE)
    style.map("Surface.TCheckbutton",
              background=[("active", SURFACE)],
              indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_HOVER)])

    # --- containers ------------------------------------------------------
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=TEXT_MUTED,
                    padding=(16, 8), font=fonts.body, borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", SURFACE)],
              foreground=[("selected", TEXT)])

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, borderwidth=0, rowheight=26, font=fonts.body)
    style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT_MUTED,
                    font=fonts.small_bold, relief="flat", padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", SURFACE_HOVER)])
    style.map("Treeview",
              background=[("selected", "#1f3a5f")],
              foreground=[("selected", TEXT)])

    style.configure("Vertical.TScrollbar", background=SURFACE_ALT, troughcolor=BG,
                    borderwidth=0, arrowcolor=TEXT_FAINT, relief="flat", width=12)
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])
    style.configure("Horizontal.TScrollbar", background=SURFACE_ALT, troughcolor=BG,
                    borderwidth=0, arrowcolor=TEXT_FAINT, relief="flat")

    style.configure("TProgressbar", background=ACCENT, troughcolor=SURFACE_ALT,
                    borderwidth=0, thickness=6)
    style.configure("TSeparator", background=BORDER)

    style.configure("TLabelframe", background=BG, foreground=TEXT_MUTED,
                    borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT_MUTED,
                    font=fonts.small_bold)

    style.configure("TSpinbox", fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=TEXT, arrowcolor=TEXT_MUTED, borderwidth=1, padding=5)

    return fonts
