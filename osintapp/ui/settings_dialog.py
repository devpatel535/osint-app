"""Settings dialog.

Modal, applies on OK, validates numeric fields before accepting them so a
typo cannot put the scanner into a broken state.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..core.settings import Settings
from . import theme


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, settings: Settings, fonts):
        super().__init__(master)
        self.settings = settings
        self.fonts = fonts
        self.saved = False

        self.title("Settings")
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())

        self.threads_var = tk.StringVar(value=str(settings.get("threads")))
        self.timeout_var = tk.StringVar(value=str(settings.get("timeout")))
        self.candidates_var = tk.StringVar(value=str(settings.get("name_candidates")))
        self.nsfw_var = tk.BooleanVar(value=bool(settings.get("include_nsfw")))
        self.details_var = tk.BooleanVar(value=bool(settings.get("fetch_profile_details")))
        self.verify_var = tk.BooleanVar(value=bool(settings.get("verify_tls")))
        self.save_default_var = tk.BooleanVar(value=bool(settings.get("save_txt_default")))
        self.proxy_var = tk.StringVar(value=str(settings.get("proxy")))
        self.hibp_var = tk.StringVar(value=str(settings.get("hibp_api_key")))
        self.reports_var = tk.StringVar(value=str(settings.reports_dir()))

        self._build()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._apply())

    def _section(self, parent, title: str) -> ttk.Frame:
        ttk.Label(parent, text=title.upper(), style="Muted.TLabel",
                  font=self.fonts.small_bold).pack(anchor="w", pady=(14, 6))
        frame = ttk.Frame(parent, style="TFrame")
        frame.pack(fill="x")
        return frame

    def _row(self, parent, label: str, widget_factory, hint: str = ""):
        row = ttk.Frame(parent, style="TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=24, anchor="w").pack(side="left")
        widget = widget_factory(row)
        widget.pack(side="left")
        if hint:
            ttk.Label(row, text=hint, style="Muted.TLabel").pack(side="left", padx=(10, 0))
        return widget

    def _build(self) -> None:
        body = ttk.Frame(self, style="TFrame", padding=20)
        body.pack(fill="both", expand=True)

        scanning = self._section(body, "Scanning")
        self._row(scanning, "Concurrent requests",
                  lambda p: ttk.Spinbox(p, from_=1, to=128, width=8,
                                        textvariable=self.threads_var),
                  "1-128. Higher is faster but more conspicuous.")
        self._row(scanning, "Timeout per site (s)",
                  lambda p: ttk.Spinbox(p, from_=2, to=120, increment=1, width=8,
                                        textvariable=self.timeout_var),
                  "2-120 seconds.")
        self._row(scanning, "Handles per name search",
                  lambda p: ttk.Spinbox(p, from_=1, to=12, width=8,
                                        textvariable=self.candidates_var),
                  "Spellings tried for 'Jane Doe'.")
        ttk.Checkbutton(scanning, text="Include adult (NSFW) sites from the catalogue",
                        variable=self.nsfw_var).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(scanning, text="Read profile details (display name, bio) from hits",
                        variable=self.details_var).pack(anchor="w")
        ttk.Checkbutton(scanning, text="Verify TLS certificates (leave on unless using an intercepting proxy)",
                        variable=self.verify_var).pack(anchor="w")

        network = self._section(body, "Network")
        self._row(network, "Proxy URL",
                  lambda p: ttk.Entry(p, textvariable=self.proxy_var, width=42),
                  "")
        ttk.Label(network, text="Example: http://127.0.0.1:8080 - leave empty for a direct connection.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        keys = self._section(body, "Optional API keys")
        self._row(keys, "HaveIBeenPwned key",
                  lambda p: ttk.Entry(p, textvariable=self.hibp_var, width=42, show="*"),
                  "")
        ttk.Label(keys, text="Paid key. Without one, email searches link to the manual HIBP page instead.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        output = self._section(body, "Reports")
        folder_row = ttk.Frame(output, style="TFrame")
        folder_row.pack(fill="x", pady=3)
        ttk.Label(folder_row, text="Save reports to", width=24, anchor="w").pack(side="left")
        ttk.Entry(folder_row, textvariable=self.reports_var, width=38).pack(side="left")
        ttk.Button(folder_row, text="Browse...", command=self._browse).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(output, text="Tick 'save on close' by default in new result windows",
                        variable=self.save_default_var).pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(body, style="TFrame")
        buttons.pack(fill="x", pady=(22, 0))
        ttk.Button(buttons, text="Restore defaults", command=self._restore).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Accent.TButton",
                   command=self._apply).pack(side="right", padx=8)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="Choose a reports folder",
                                         initialdir=self.reports_var.get())
        if chosen:
            self.reports_var.set(chosen)

    def _restore(self) -> None:
        from ..core.settings import DEFAULTS

        self.threads_var.set(str(DEFAULTS["threads"]))
        self.timeout_var.set(str(DEFAULTS["timeout"]))
        self.candidates_var.set(str(DEFAULTS["name_candidates"]))
        self.nsfw_var.set(DEFAULTS["include_nsfw"])
        self.details_var.set(DEFAULTS["fetch_profile_details"])
        self.verify_var.set(DEFAULTS["verify_tls"])
        self.save_default_var.set(DEFAULTS["save_txt_default"])
        self.proxy_var.set(DEFAULTS["proxy"])
        self.hibp_var.set(DEFAULTS["hibp_api_key"])
        from ..paths import default_reports_dir

        self.reports_var.set(str(default_reports_dir()))

    def _apply(self) -> None:
        try:
            threads = int(self.threads_var.get())
            timeout = float(self.timeout_var.get())
            candidates = int(self.candidates_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid value",
                "Concurrent requests, timeout and handle count must be numbers.",
                parent=self,
            )
            return
        if not 1 <= threads <= 128 or not 2 <= timeout <= 120 or not 1 <= candidates <= 12:
            messagebox.showerror(
                "Out of range",
                "Concurrent requests must be 1-128, timeout 2-120 seconds, "
                "handles per name 1-12.",
                parent=self,
            )
            return

        self.settings.update(
            threads=threads,
            timeout=timeout,
            name_candidates=candidates,
            include_nsfw=self.nsfw_var.get(),
            fetch_profile_details=self.details_var.get(),
            verify_tls=self.verify_var.get(),
            save_txt_default=self.save_default_var.get(),
            proxy=self.proxy_var.get().strip(),
            hibp_api_key=self.hibp_var.get().strip(),
            reports_dir=self.reports_var.get().strip(),
        )
        self.saved = True
        self.destroy()
