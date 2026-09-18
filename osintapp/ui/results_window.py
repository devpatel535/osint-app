"""The results window.

The behaviour the brief is specific about:

* a checkbox offers to save the result as a .txt file
* if it is ticked, the file is written **when the user closes this window**
* if it is not ticked, no file is ever written

So the write happens in :meth:`ResultsWindow._on_close`, not at render time and
not when the box is ticked. Every route out of the window - the X button, the
Close button, Escape, and the main window shutting down - goes through that one
method, so the promise holds however the window is dismissed.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from ..core import report
from ..core.models import (
    CONFIDENCE_LABEL,
    CONFIRMED,
    ERROR,
    LIKELY,
    PIVOT,
    POSSIBLE,
    Finding,
    SearchResult,
)
from ..core.query import TYPE_LABELS
from ..core.settings import Settings
from ..core.timefmt import absolute
from . import theme
from .widgets import Badge, separator

_MARKERS = {
    CONFIRMED: "+", LIKELY: "~", POSSIBLE: "?", PIVOT: ">", ERROR: "!",
}


def open_url(url: str, parent: Optional[tk.Misc] = None) -> None:
    """Open *url* in the default browser, refusing anything but http(s).

    Some URLs in a result come from remote page metadata, so the scheme is
    checked rather than trusted - handing an arbitrary scheme to the OS opener
    is how you end up launching something you did not intend.
    """
    if not url:
        return
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme in ("http", "https"):
        webbrowser.open_new_tab(url)
    elif scheme == "mailto":
        webbrowser.open(url)
    else:
        messagebox.showwarning(
            "Link not opened",
            f"This link does not use http or https, so it was not opened:\n\n{url}",
            parent=parent,
        )


class ResultsWindow(tk.Toplevel):
    """One window per search result. Several may be open at once."""

    def __init__(
        self,
        master: tk.Misc,
        result: SearchResult,
        settings: Settings,
        fonts,
        on_destroy: Optional[Callable[["ResultsWindow"], None]] = None,
        title_suffix: str = "",
    ):
        super().__init__(master)
        self.result = result
        self.settings = settings
        self.fonts = fonts
        self._on_destroy = on_destroy
        self._saved_path = None          # set once a file has been written
        self._finding_by_row: Dict[str, Finding] = {}
        self._closing = False

        self.title(f"Results - {result.query}{title_suffix}")
        self.configure(bg=theme.BG)
        self.geometry("1060x720")
        self.minsize(820, 560)

        self.save_var = tk.BooleanVar(value=bool(settings.get("save_txt_default")))
        self.filter_var = tk.StringVar()

        self._build()
        self._populate()

        # Every exit route funnels here so the save promise cannot be bypassed.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())
        self.bind("<Control-s>", lambda _e: self._save_now())

        if isinstance(master, tk.Misc):
            self.transient(master.winfo_toplevel())

        # Raising has to wait for the window to be mapped. The id is kept so
        # the callback can be cancelled if the window is closed first -
        # otherwise Tcl complains about a command that no longer exists.
        self._focus_job: Optional[str] = self.after(60, self._focus_self)

    def _focus_self(self) -> None:
        self._focus_job = None
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self) -> None:
        # Order matters. Tk's packer hands out space in call order, so the
        # footer has to claim the bottom strip before the body (which expands)
        # takes everything that is left - otherwise Save/Close get clipped on a
        # short window. The bottom separator is packed after the footer so it
        # lands above it.
        self._build_header()
        separator(self).pack(fill="x")
        self._build_toolbar()
        self._build_footer()
        separator(self).pack(fill="x", side="bottom")
        self._build_body()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", side="top")

        inner = tk.Frame(header, bg=theme.SURFACE)
        inner.pack(fill="x", padx=18, pady=14)

        colour, letter = theme.QUERY_BADGES.get(
            self.result.query_type, (theme.ACCENT, "?")
        )
        Badge(inner, letter, colour, size=40, background=theme.SURFACE,
              font=self.fonts.badge).pack(side="left", padx=(0, 14))

        text = tk.Frame(inner, bg=theme.SURFACE)
        text.pack(side="left", fill="x", expand=True)

        tk.Label(text, text=self.result.query, bg=theme.SURFACE, fg=theme.TEXT,
                 font=self.fonts.title, anchor="w").pack(anchor="w")

        meta = " • ".join(filter(None, [
            TYPE_LABELS.get(self.result.query_type, self.result.query_type),
            absolute(self.result.started_at, "%d %b %Y, %H:%M"),
            f"{self.result.duration:.1f}s",
            f"{self.result.total_findings} findings",
            f"{self.result.hit_count} accounts/records",
            "STOPPED EARLY" if self.result.cancelled else "",
        ]))
        tk.Label(text, text=meta, bg=theme.SURFACE, fg=theme.TEXT_MUTED,
                 font=self.fonts.small, anchor="w").pack(anchor="w", pady=(3, 0))

        counts = tk.Frame(inner, bg=theme.SURFACE)
        counts.pack(side="right")
        for label, confidence in (("Confirmed", CONFIRMED), ("Likely", LIKELY),
                                  ("Possible", POSSIBLE)):
            total = sum(
                1 for section in self.result.sections
                for finding in section.findings
                if finding.confidence == confidence
            )
            chip = tk.Frame(counts, bg=theme.SURFACE)
            chip.pack(side="left", padx=6)
            tk.Label(chip, text=str(total), bg=theme.SURFACE,
                     fg=theme.CONFIDENCE_COLOURS[confidence],
                     font=self.fonts.heading).pack()
            tk.Label(chip, text=label, bg=theme.SURFACE, fg=theme.TEXT_FAINT,
                     font=self.fonts.tiny).pack()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.pack(fill="x", padx=18, pady=(12, 6))

        ttk.Label(bar, text="Filter:", style="Muted.TLabel").pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.filter_var, width=34)
        entry.pack(side="left", padx=(8, 12))
        self.filter_var.trace_add("write", lambda *_: self._populate())

        ttk.Button(bar, text="Expand all", command=lambda: self._set_open(True)).pack(side="left")
        ttk.Button(bar, text="Collapse all", command=lambda: self._set_open(False)).pack(
            side="left", padx=(6, 0))

        self.hits_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Hits only", variable=self.hits_only_var,
                        command=self._populate).pack(side="left", padx=(14, 0))

        ttk.Button(bar, text="Copy all as text", command=self._copy_all).pack(side="right")

    def _build_body(self) -> None:
        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=18, pady=(4, 10))

        tree_frame = ttk.Frame(panes, style="TFrame")
        panes.add(tree_frame, weight=4)

        columns = ("confidence", "detail")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Finding", anchor="w")
        self.tree.heading("confidence", text="Confidence", anchor="w")
        self.tree.heading("detail", text="Detail", anchor="w")
        self.tree.column("#0", width=380, minwidth=220, stretch=True)
        # Wide enough for the longest label plus a status code, e.g.
        # "Manual check (429)" - narrower and it truncates.
        self.tree.column("confidence", width=150, minwidth=120, stretch=False)
        self.tree.column("detail", width=460, minwidth=220, stretch=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview,
                               style="Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        for confidence, colour in theme.CONFIDENCE_COLOURS.items():
            self.tree.tag_configure(confidence, foreground=colour)
        self.tree.tag_configure("section", foreground=theme.TEXT,
                                font=self.fonts.body_bold)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_activate)
        self.tree.bind("<Return>", self._on_activate)
        self.tree.bind("<Button-3>", self._on_right_click)

        # --- detail pane -------------------------------------------------
        detail_frame = ttk.Frame(panes, style="TFrame")
        panes.add(detail_frame, weight=1)

        self.detail = tk.Text(detail_frame, height=8, wrap="word", bg=theme.SURFACE,
                              fg=theme.TEXT, insertbackground=theme.TEXT,
                              relief="flat", borderwidth=0, padx=12, pady=10,
                              font=self.fonts.mono, state="disabled")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical",
                                      command=self.detail.yview,
                                      style="Vertical.TScrollbar")
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")
        detail_frame.grid_rowconfigure(0, weight=1)
        detail_frame.grid_columnconfigure(0, weight=1)

        self.detail.tag_configure("h", foreground=theme.ACCENT, font=self.fonts.body_bold)
        self.detail.tag_configure("k", foreground=theme.TEXT_MUTED)
        self.detail.tag_configure("link", foreground=theme.CYAN, underline=True)

        actions = ttk.Frame(detail_frame, style="TFrame")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.open_button = ttk.Button(actions, text="Open link in browser",
                                      command=self._open_selected, state="disabled")
        self.open_button.pack(side="left")
        self.copy_button = ttk.Button(actions, text="Copy link",
                                      command=self._copy_selected_url, state="disabled")
        self.copy_button.pack(side="left", padx=(8, 0))

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=theme.SURFACE)
        footer.pack(fill="x", side="bottom")

        inner = tk.Frame(footer, bg=theme.SURFACE)
        inner.pack(fill="x", padx=18, pady=12)

        # Buttons first: they get their natural width before the label block
        # is allowed to expand into whatever remains.
        right = tk.Frame(inner, bg=theme.SURFACE)
        right.pack(side="right")
        ttk.Button(right, text="Change folder...", command=self._choose_folder).pack(side="left")
        ttk.Button(right, text="Save now", command=self._save_now).pack(side="left", padx=8)
        ttk.Button(right, text="Close", style="Accent.TButton",
                   command=self._on_close).pack(side="left")

        left = tk.Frame(inner, bg=theme.SURFACE)
        left.pack(side="left", fill="x", expand=True)

        ttk.Checkbutton(
            left,
            text="Save these results to a .txt file when I close this window",
            variable=self.save_var,
            style="Surface.TCheckbutton",
            command=self._update_destination_label,
        ).pack(anchor="w")

        self.destination_label = tk.Label(
            left, bg=theme.SURFACE, fg=theme.TEXT_MUTED, font=self.fonts.small,
            anchor="w", justify="left",
        )
        self.destination_label.pack(anchor="w", pady=(4, 0))
        self._update_destination_label()

    # ------------------------------------------------------------------
    # content
    # ------------------------------------------------------------------
    def _matches_filter(self, finding: Finding, needle: str) -> bool:
        if self.hits_only_var.get() and finding.confidence not in (CONFIRMED, LIKELY, POSSIBLE):
            return False
        if not needle:
            return True
        haystack = " ".join([
            finding.title, finding.url, finding.detail,
            " ".join(f"{k} {v}" for k, v in finding.attributes.items()),
        ]).lower()
        return needle in haystack

    def _populate(self) -> None:
        needle = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        self._finding_by_row.clear()

        shown = 0
        for section in self.result.sections:
            findings = [f for f in section.sorted_findings() if self._matches_filter(f, needle)]
            if not findings:
                continue
            parent = self.tree.insert(
                "", "end",
                text=f"{section.name}  ({len(findings)})",
                values=("", "; ".join(section.notes)[:200]),
                open=True, tags=("section",),
            )
            for finding in findings:
                marker = _MARKERS.get(finding.confidence, "i")
                label = CONFIDENCE_LABEL.get(finding.confidence, finding.confidence)
                if finding.status:
                    label = f"{label} ({finding.status})"
                row = self.tree.insert(
                    parent, "end",
                    text=f"[{marker}] {finding.title}",
                    values=(label, finding.detail.replace("\n", " ")),
                    tags=(finding.confidence,),
                )
                self._finding_by_row[row] = finding
                shown += 1

        if shown == 0:
            self.tree.insert("", "end", text="No findings match the current filter",
                             values=("", ""), tags=("info",))

    def _set_open(self, is_open: bool) -> None:
        for row in self.tree.get_children():
            self.tree.item(row, open=is_open)

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def _selected_finding(self) -> Optional[Finding]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._finding_by_row.get(selection[0])

    def _on_select(self, _event=None) -> None:
        finding = self._selected_finding()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")

        if finding is None:
            self.detail.insert("end", "Select a finding to see its full detail.")
            self.open_button.configure(state="disabled")
            self.copy_button.configure(state="disabled")
        else:
            self.detail.insert("end", finding.title + "\n", "h")
            if finding.url:
                self.detail.insert("end", finding.url + "\n", "link")
            self.detail.insert("end", "\n")
            self.detail.insert("end", "Confidence: ", "k")
            self.detail.insert(
                "end", CONFIDENCE_LABEL.get(finding.confidence, finding.confidence) + "\n")
            if finding.status:
                self.detail.insert("end", "HTTP status: ", "k")
                self.detail.insert("end", f"{finding.status}\n")
            if finding.detail:
                self.detail.insert("end", "\n" + finding.detail + "\n")
            if finding.attributes:
                self.detail.insert("end", "\n")
                for key, value in finding.attributes.items():
                    if not value:
                        continue
                    self.detail.insert("end", f"{key}: ", "k")
                    self.detail.insert("end", f"{value}\n")
            state = "normal" if finding.url else "disabled"
            self.open_button.configure(state=state)
            self.copy_button.configure(state=state)

        self.detail.configure(state="disabled")

    def _on_activate(self, _event=None) -> None:
        self._open_selected()

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self._on_select()
        finding = self._selected_finding()

        menu = tk.Menu(self, tearoff=0, bg=theme.SURFACE_ALT, fg=theme.TEXT,
                       activebackground=theme.ACCENT, activeforeground=theme.ACCENT_TEXT,
                       borderwidth=0)
        if finding and finding.url:
            menu.add_command(label="Open link in browser", command=self._open_selected)
            menu.add_command(label="Copy link", command=self._copy_selected_url)
            menu.add_separator()
        menu.add_command(label="Copy this finding", command=self._copy_selected_row)
        menu.add_command(label="Copy all as text", command=self._copy_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_selected(self) -> None:
        finding = self._selected_finding()
        if finding and finding.url:
            open_url(finding.url, parent=self)

    def _to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_selected_url(self) -> None:
        finding = self._selected_finding()
        if finding and finding.url:
            self._to_clipboard(finding.url)

    def _copy_selected_row(self) -> None:
        finding = self._selected_finding()
        if not finding:
            return
        lines = [finding.title]
        if finding.url:
            lines.append(finding.url)
        if finding.detail:
            lines.append(finding.detail)
        lines += [f"{k}: {v}" for k, v in finding.attributes.items() if v]
        self._to_clipboard("\n".join(lines))

    def _copy_all(self) -> None:
        self._to_clipboard(report.render(self.result))

    # ------------------------------------------------------------------
    # saving
    # ------------------------------------------------------------------
    def _destination_dir(self):
        return self.settings.reports_dir()

    @staticmethod
    def _elide(text: str, limit: int = 62) -> str:
        """Shorten a path in the middle, keeping the drive and the folder name."""
        if len(text) <= limit:
            return text
        head = (limit - 3) // 2
        tail = limit - 3 - head
        return f"{text[:head]}...{text[-tail:]}"

    def _update_destination_label(self) -> None:
        if self.save_var.get():
            text = f"Will be saved to:  {self._elide(str(self._destination_dir()))}"
            colour = theme.TEXT_MUTED
        else:
            text = "No file will be written when this window closes."
            colour = theme.TEXT_FAINT
        self.destination_label.configure(text=text, fg=colour)

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self,
            title="Choose where to save reports",
            initialdir=str(self._destination_dir()),
        )
        if chosen:
            self.settings.update(reports_dir=chosen)
            self._update_destination_label()

    def _write_report(self, directory=None) -> Optional[object]:
        """Write the .txt, reporting any failure instead of swallowing it."""
        try:
            return report.save(self.result, directory or self._destination_dir())
        except PermissionError:
            messagebox.showerror(
                "Could not save",
                "Windows refused write access to:\n\n"
                f"{directory or self._destination_dir()}\n\n"
                "Pick a different folder with 'Change folder...'.",
                parent=self,
            )
        except OSError as exc:
            messagebox.showerror(
                "Could not save",
                f"The report could not be written:\n\n{exc}",
                parent=self,
            )
        return None

    def _save_now(self) -> None:
        path = self._write_report()
        if path is None:
            return
        self._saved_path = path
        messagebox.showinfo("Report saved", f"Saved to:\n\n{path}", parent=self)

    # ------------------------------------------------------------------
    # closing - this is where the checkbox promise is kept
    # ------------------------------------------------------------------
    def _on_close(self, silent: bool = False) -> None:
        if self._closing:
            return
        self._closing = True

        if self._focus_job is not None:
            try:
                self.after_cancel(self._focus_job)
            except (tk.TclError, ValueError):
                pass
            self._focus_job = None

        # The requirement, in one place: write only if the box is ticked, and
        # only on the way out. Saving twice is pointless when "Save now"
        # already produced the identical file, so that is skipped.
        if self.save_var.get() and self._saved_path is None:
            path = self._write_report()
            if path is not None:
                self._saved_path = path
                if not silent:
                    messagebox.showinfo(
                        "Report saved",
                        f"The results for '{self.result.query}' were saved to:\n\n{path}",
                        parent=self,
                    )

        # Remember the checkbox state as the default for the next search.
        try:
            self.settings.update(save_txt_default=bool(self.save_var.get()))
        except Exception:  # noqa: BLE001 - never block closing on a settings write
            pass

        if self._on_destroy is not None:
            self._on_destroy(self)
        self.destroy()

    def close_for_shutdown(self) -> None:
        """Called when the main window quits with this window still open."""
        self._on_close(silent=True)
