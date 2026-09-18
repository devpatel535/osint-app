"""The main application window.

Layout, top to bottom: title bar, search box, progress strip, history list.

Two things worth knowing about how this is wired:

* **The search runs on a worker thread.** Tk is not thread-safe, so the worker
  never touches a widget. It pushes progress and the final result onto a
  ``queue.Queue`` which the GUI drains from an ``after()`` tick. That is what
  keeps the window responsive (and the Stop button working) during a sweep of
  260 sites.
* **History is paged, not loaded.** The list starts with one page and appends
  another whenever the user scrolls near the bottom, forever. Nothing is ever
  trimmed, so a search from years ago is reachable purely by scrolling - which
  is exactly the behaviour asked for.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from .. import APP_NAME, __version__
from ..core import engine
from ..core.history import HistoryEntry, SearchHistory
from ..core.models import SearchResult
from ..core.query import EMAIL, NAME, PHONE, TYPE_LABELS, USERNAME, detect_type
from ..core.settings import Settings
from ..core.timefmt import absolute, relative_age
from ..paths import resource_path
from . import theme
from .results_window import ResultsWindow
from .settings_dialog import SettingsDialog
from .widgets import Badge, HoverRow, PlaceholderEntry, ScrollableFrame, separator

PAGE_SIZE = 40
AUTO_LABEL = "Auto-detect"

_TYPE_CHOICES = [AUTO_LABEL] + [TYPE_LABELS[t] for t in (USERNAME, EMAIL, PHONE, NAME)]
_LABEL_TO_TYPE = {TYPE_LABELS[t]: t for t in (USERNAME, EMAIL, PHONE, NAME)}

RESPONSIBLE_USE = (
    f"{APP_NAME} checks whether public profile pages exist and reads publicly "
    "served page metadata. It does not break into anything, bypass any login, "
    "or access private data.\n\n"
    "Automated checks produce false positives. A handle matching on a site is "
    "not proof that your subject owns it - verify before you rely on it.\n\n"
    "You are responsible for using this lawfully: only research people and "
    "accounts you have a legitimate reason to research, and follow the rules "
    "that apply where you are."
)


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.history = SearchHistory()

        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("980x780")
        self.minsize(760, 560)

        self.fonts = theme.apply(self)
        self._set_icon()

        # --- search state ------------------------------------------------
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._cancel = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._searching = False
        self._open_results: List[ResultsWindow] = []

        # --- history paging state ---------------------------------------
        self._history_offset = 0
        self._history_exhausted = False
        self._history_rows: Dict[int, tk.Widget] = {}
        self._filter_job: Optional[str] = None

        self.type_var = tk.StringVar(value=AUTO_LABEL)
        self.status_var = tk.StringVar(value="Ready")

        self._build_menu()
        self._build_header()
        self._build_search()
        self._build_progress()
        self._build_history()

        self.protocol("WM_DELETE_WINDOW", self._on_quit)
        self.bind("<Control-q>", lambda _e: self._on_quit())
        self.bind("<Control-l>", lambda _e: self.search_entry.focus_set())
        self.bind("<F5>", lambda _e: self._reload_history())

        self._reload_history()
        self.after(100, self._drain_queue)
        self.after(400, self._maybe_show_first_run_notice)
        self.search_entry.focus_set()

    # ------------------------------------------------------------------
    # chrome
    # ------------------------------------------------------------------
    def _set_icon(self) -> None:
        # A .ico only exists in the packaged build; a missing icon is not worth
        # failing over, so this stays best-effort.
        try:
            icon = resource_path("app.ico")
            if icon.exists() and sys.platform == "win32":
                self.iconbitmap(default=str(icon))
        except Exception:  # noqa: BLE001
            pass

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        common = dict(tearoff=0, bg=theme.SURFACE_ALT, fg=theme.TEXT,
                      activebackground=theme.ACCENT, activeforeground=theme.ACCENT_TEXT,
                      borderwidth=0)

        file_menu = tk.Menu(menubar, **common)
        file_menu.add_command(label="Settings...", command=self._open_settings)
        file_menu.add_command(label="Open reports folder", command=self._open_reports_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", accelerator="Ctrl+Q", command=self._on_quit)
        menubar.add_cascade(label="File", menu=file_menu)

        history_menu = tk.Menu(menubar, **common)
        history_menu.add_command(label="Refresh", accelerator="F5", command=self._reload_history)
        history_menu.add_command(label="Clear all search history", command=self._clear_history)
        menubar.add_cascade(label="History", menu=history_menu)

        help_menu = tk.Menu(menubar, **common)
        help_menu.add_command(label="Responsible use", command=self._show_responsible_use)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.configure(menu=menubar)

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=24, pady=(18, 0))
        tk.Label(header, text=APP_NAME, bg=theme.BG, fg=theme.TEXT,
                 font=self.fonts.title).pack(anchor="w")
        tk.Label(header,
                 text="Search a username, email address, phone number or real name "
                      "across public sources.",
                 bg=theme.BG, fg=theme.TEXT_MUTED, font=self.fonts.small).pack(anchor="w",
                                                                              pady=(2, 0))

    def _build_search(self) -> None:
        wrapper = tk.Frame(self, bg=theme.BG)
        wrapper.pack(fill="x", padx=24, pady=(16, 0))

        card = tk.Frame(wrapper, bg=theme.SURFACE, highlightthickness=1,
                        highlightbackground=theme.BORDER, highlightcolor=theme.BORDER)
        card.pack(fill="x")

        inner = tk.Frame(card, bg=theme.SURFACE)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="\U0001F50D", bg=theme.SURFACE, fg=theme.TEXT_MUTED,
                 font=self.fonts.search).pack(side="left", padx=(2, 8))

        self.search_entry = tk.Entry(
            inner, bg=theme.SURFACE, fg=theme.TEXT, insertbackground=theme.ACCENT,
            font=self.fonts.search, relief="flat", borderwidth=0,
            highlightthickness=0,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.search_entry.bind("<Return>", lambda _e: self._start_search())
        self.search_entry.bind("<KeyRelease>", self._update_detected_hint)

        self.clear_entry_button = tk.Label(
            inner, text="×", bg=theme.SURFACE, fg=theme.TEXT_FAINT,
            font=(self.fonts.family, 14), cursor="hand2",
        )
        self.clear_entry_button.pack(side="left", padx=6)
        self.clear_entry_button.bind("<Button-1>", lambda _e: self._clear_search_entry())

        ttk.Combobox(inner, textvariable=self.type_var, values=_TYPE_CHOICES,
                     state="readonly", width=18).pack(side="left", padx=(6, 8))

        self.search_button = ttk.Button(inner, text="Search", style="Accent.TButton",
                                        command=self._start_search)
        self.search_button.pack(side="left")

        self.detected_label = tk.Label(wrapper, text="", bg=theme.BG, fg=theme.TEXT_FAINT,
                                       font=self.fonts.small, anchor="w")
        self.detected_label.pack(fill="x", pady=(6, 0))

    def _build_progress(self) -> None:
        self.progress_frame = tk.Frame(self, bg=theme.BG)
        # Packed and unpacked on demand so it takes no space when idle.

        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="determinate",
                                            maximum=100)
        self.progress_bar.pack(fill="x", side="top", pady=(0, 6))

        row = tk.Frame(self.progress_frame, bg=theme.BG)
        row.pack(fill="x")
        tk.Label(row, textvariable=self.status_var, bg=theme.BG, fg=theme.TEXT_MUTED,
                 font=self.fonts.small, anchor="w").pack(side="left")
        self.stop_button = ttk.Button(row, text="Stop", style="Danger.TButton",
                                      command=self._cancel_search)
        self.stop_button.pack(side="right")

    def _build_history(self) -> None:
        container = tk.Frame(self, bg=theme.BG)
        container.pack(fill="both", expand=True, padx=24, pady=(18, 18))

        head = tk.Frame(container, bg=theme.BG)
        head.pack(fill="x")
        tk.Label(head, text="Search history", bg=theme.BG, fg=theme.TEXT,
                 font=self.fonts.heading).pack(side="left")
        self.history_count_label = tk.Label(head, text="", bg=theme.BG,
                                            fg=theme.TEXT_FAINT, font=self.fonts.small)
        self.history_count_label.pack(side="left", padx=(10, 0))

        self.clear_all_button = tk.Label(
            head, text="Clear all", bg=theme.BG, fg=theme.ACCENT,
            font=self.fonts.small_bold, cursor="hand2",
        )
        self.clear_all_button.pack(side="right")
        self.clear_all_button.bind("<Button-1>", lambda _e: self._clear_history())

        filter_row = tk.Frame(container, bg=theme.BG)
        filter_row.pack(fill="x", pady=(10, 8))
        self.history_filter = PlaceholderEntry(
            filter_row, placeholder="Filter history...", width=34,
        )
        self.history_filter.pack(side="left")
        self.history_filter.bind("<KeyRelease>", self._on_filter_changed)

        separator(container).pack(fill="x")

        self.history_scroll = ScrollableFrame(
            container, background=theme.BG, on_near_bottom=self._load_more_history
        )
        self.history_scroll.pack(fill="both", expand=True, pady=(8, 0))

        self.history_footer = tk.Frame(container, bg=theme.BG)
        self.history_footer.pack(fill="x")
        self.load_more_button = ttk.Button(self.history_footer, text="Load older searches",
                                           command=self._load_more_history)
        self.history_status = tk.Label(self.history_footer, text="", bg=theme.BG,
                                       fg=theme.TEXT_FAINT, font=self.fonts.small)
        self.history_status.pack(side="left", pady=(6, 0))

    # ------------------------------------------------------------------
    # search entry helpers
    # ------------------------------------------------------------------
    def _clear_search_entry(self) -> None:
        self.search_entry.delete(0, "end")
        self._update_detected_hint()
        self.search_entry.focus_set()

    def _update_detected_hint(self, _event=None) -> None:
        text = self.search_entry.get().strip()
        if not text:
            self.detected_label.configure(text="")
            return
        if self.type_var.get() == AUTO_LABEL:
            detected = detect_type(text)
            self.detected_label.configure(
                text=f"Will search as: {TYPE_LABELS.get(detected, detected)}")
        else:
            self.detected_label.configure(
                text=f"Forced type: {self.type_var.get()}")

    # ------------------------------------------------------------------
    # running a search
    # ------------------------------------------------------------------
    def _start_search(self, query: Optional[str] = None,
                      forced_type: Optional[str] = None) -> None:
        if self._searching:
            messagebox.showinfo("Search running",
                                "A search is already running. Stop it first.", parent=self)
            return

        text = (query if query is not None else self.search_entry.get()).strip()
        if not text:
            self.search_entry.focus_set()
            return

        if query is not None:
            self.search_entry.delete(0, "end")
            self.search_entry.insert(0, text)
        self._update_detected_hint()

        if forced_type is None:
            label = self.type_var.get()
            forced_type = _LABEL_TO_TYPE.get(label)  # None when Auto-detect

        self._searching = True
        self._cancel = threading.Event()
        self.search_button.configure(state="disabled", text="Searching...")
        self.progress_bar.configure(value=0)
        self.status_var.set("Starting...")
        self.progress_frame.pack(fill="x", padx=24, pady=(14, 0))

        def progress(done: int, total: int, message: str) -> None:
            self._queue.put(("progress", done, total, message))

        def run() -> None:
            try:
                result = engine.run_search(
                    text, forced_type=forced_type, settings=self.settings,
                    cancel=self._cancel, on_progress=progress,
                )
                self._queue.put(("done", result))
            except Exception as exc:  # noqa: BLE001 - surface, never hang the UI
                self._queue.put(("failed", exc))

        self._worker = threading.Thread(target=run, daemon=True,
                                        name="osint-search")
        self._worker.start()

    def _cancel_search(self) -> None:
        if self._searching:
            self._cancel.set()
            self.status_var.set("Stopping...")
            self.stop_button.configure(state="disabled")

    def _drain_queue(self) -> None:
        """Pump worker messages into the GUI. Runs forever on the Tk loop."""
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, done, total, text = message
                    percent = (done / total * 100.0) if total else 0.0
                    self.progress_bar.configure(value=percent)
                    self.status_var.set(f"{done}/{total}  -  {text}")
                elif kind == "done":
                    self._finish_search(message[1])
                elif kind == "failed":
                    self._fail_search(message[1])
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_queue)

    def _reset_search_ui(self) -> None:
        self._searching = False
        self.search_button.configure(state="normal", text="Search")
        self.stop_button.configure(state="normal")
        self.progress_frame.pack_forget()

    def _finish_search(self, result: SearchResult) -> None:
        self._reset_search_ui()
        try:
            self.history.add(result)
        except Exception as exc:  # noqa: BLE001 - a history failure must not eat the result
            messagebox.showwarning(
                "History not saved",
                f"The search finished, but could not be written to history:\n\n{exc}",
                parent=self,
            )
        self._reload_history()
        self._show_result(result)

    def _fail_search(self, exc: BaseException) -> None:
        self._reset_search_ui()
        messagebox.showerror("Search failed",
                             f"{type(exc).__name__}: {exc}", parent=self)

    def _show_result(self, result: SearchResult, title_suffix: str = "") -> None:
        window = ResultsWindow(
            self, result, self.settings, self.fonts,
            on_destroy=self._forget_result_window, title_suffix=title_suffix,
        )
        self._open_results.append(window)

    def _forget_result_window(self, window: ResultsWindow) -> None:
        if window in self._open_results:
            self._open_results.remove(window)

    # ------------------------------------------------------------------
    # history list
    # ------------------------------------------------------------------
    def _on_filter_changed(self, _event=None) -> None:
        # Debounce: rebuilding the list on every keystroke is visibly janky.
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(220, self._reload_history)

    def _current_filter(self) -> str:
        return self.history_filter.get().strip()

    def _reload_history(self) -> None:
        self._filter_job = None
        for child in self.history_scroll.body.winfo_children():
            child.destroy()
        self._history_rows.clear()
        self._history_offset = 0
        self._history_exhausted = False
        self.history_scroll.scroll_to_top()
        self._load_more_history(initial=True)

    def _load_more_history(self, initial: bool = False) -> None:
        if self._history_exhausted:
            return

        term = self._current_filter()
        entries = self.history.page(offset=self._history_offset, limit=PAGE_SIZE,
                                    search=term)
        if not entries:
            self._history_exhausted = True
            if initial:
                self._render_empty_state(term)
            self._update_history_footer(term)
            return

        for entry in entries:
            self._render_history_row(entry)

        self._history_offset += len(entries)
        if len(entries) < PAGE_SIZE:
            self._history_exhausted = True

        self.history_scroll.reset_paging_flag()
        self._update_history_footer(term)

    def _update_history_footer(self, term: str) -> None:
        total = self.history.count(term)
        shown = len(self._history_rows)

        if term:
            self.history_count_label.configure(text=f"{shown} of {total} matching")
        else:
            self.history_count_label.configure(
                text=f"{shown} of {total} shown" if total else "")

        if self._history_exhausted:
            self.load_more_button.pack_forget()
            if total:
                oldest = self.history.oldest_timestamp()
                suffix = f" - oldest entry {relative_age(oldest)}" if oldest and not term else ""
                self.history_status.configure(
                    text=f"End of history{suffix}. Nothing is ever deleted automatically.")
            else:
                self.history_status.configure(text="")
        else:
            self.history_status.configure(text="Scroll for older searches")
            self.load_more_button.pack(side="right", pady=(6, 0))

    def _render_empty_state(self, term: str) -> None:
        message = (f"No searches match “{term}”."
                   if term else
                   "No searches yet. Results will be listed here, oldest kept forever.")
        tk.Label(self.history_scroll.body, text=message, bg=theme.BG,
                 fg=theme.TEXT_FAINT, font=self.fonts.body,
                 pady=30).pack(fill="x")

    def _render_history_row(self, entry: HistoryEntry) -> None:
        row = HoverRow(self.history_scroll.body,
                       on_click=lambda e=entry: self._rerun_entry(e))
        row.pack(fill="x", pady=2)

        inner = tk.Frame(row, bg=theme.SURFACE)
        inner.pack(fill="x", padx=12, pady=9)

        colour, letter = theme.QUERY_BADGES.get(entry.query_type, (theme.ACCENT, "?"))
        Badge(inner, letter, colour, size=34, background=theme.SURFACE,
              font=self.fonts.badge).pack(side="left", padx=(0, 12))

        text_frame = tk.Frame(inner, bg=theme.SURFACE)
        text_frame.pack(side="left", fill="x", expand=True)

        tk.Label(text_frame, text=entry.query, bg=theme.SURFACE, fg=theme.TEXT,
                 font=self.fonts.body_bold, anchor="w").pack(anchor="w")

        bits = [
            TYPE_LABELS.get(entry.query_type, entry.query_type),
            relative_age(entry.created_at),
            f"{entry.hit_count} found" if entry.hit_count else "nothing found",
        ]
        if entry.cancelled:
            bits.append("stopped early")
        tk.Label(text_frame, text="  •  ".join(bits), bg=theme.SURFACE,
                 fg=theme.TEXT_MUTED, font=self.fonts.small,
                 anchor="w").pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(inner, bg=theme.SURFACE)
        actions.pack(side="right")

        date_label = tk.Label(actions, text=absolute(entry.created_at, "%d %b %Y"),
                              bg=theme.SURFACE, fg=theme.TEXT_FAINT,
                              font=self.fonts.tiny)
        date_label.pack(side="left", padx=(0, 12))

        view = tk.Label(actions, text="View saved", bg=theme.SURFACE, fg=theme.ACCENT,
                        font=self.fonts.small, cursor="hand2")
        view.pack(side="left", padx=(0, 12))
        view.bind("<Button-1>", lambda _e, item=entry: self._open_snapshot(item))

        remove = tk.Label(actions, text="×", bg=theme.SURFACE, fg=theme.TEXT_FAINT,
                          font=(self.fonts.family, 15), cursor="hand2")
        remove.pack(side="left")
        remove.bind("<Button-1>", lambda _e, item=entry: self._delete_entry(item))
        remove.bind("<Enter>", lambda _e: remove.configure(fg=theme.DANGER))
        remove.bind("<Leave>", lambda _e: remove.configure(fg=theme.TEXT_FAINT))

        # Hover/click on the row, but not on the two controls that do their own thing.
        row.bind_tree(inner)
        for widget in (view, remove):
            widget.unbind("<Button-1>")
        view.bind("<Button-1>", lambda _e, item=entry: self._open_snapshot(item))
        remove.bind("<Button-1>", lambda _e, item=entry: self._delete_entry(item))

        self._history_rows[entry.id] = row

    def _rerun_entry(self, entry: HistoryEntry) -> None:
        self.type_var.set(TYPE_LABELS.get(entry.query_type, AUTO_LABEL))
        self._start_search(entry.query, forced_type=entry.query_type)

    def _open_snapshot(self, entry: HistoryEntry) -> str:
        result = self.history.snapshot(entry.id)
        if result is None:
            messagebox.showinfo(
                "No saved copy",
                "This history entry has no stored result. Click the row to run "
                "the search again.",
                parent=self,
            )
            return "break"
        self._show_result(result, title_suffix="  (saved copy)")
        return "break"

    def _delete_entry(self, entry: HistoryEntry) -> str:
        self.history.delete(entry.id)
        row = self._history_rows.pop(entry.id, None)
        if row is not None:
            row.destroy()
        # Keep the offset consistent with the rows actually on screen, or the
        # next page would skip one.
        self._history_offset = max(0, self._history_offset - 1)
        self._update_history_footer(self._current_filter())
        return "break"

    def _clear_history(self) -> None:
        total = self.history.count()
        if not total:
            messagebox.showinfo("Nothing to clear", "Your search history is already empty.",
                                parent=self)
            return
        if not messagebox.askyesno(
            "Clear search history",
            f"Delete all {total} searches from your history?\n\n"
            "This cannot be undone. Reports already saved as .txt files are not affected.",
            icon="warning", parent=self,
        ):
            return
        removed = self.history.clear()
        self._reload_history()
        messagebox.showinfo("History cleared", f"Removed {removed} searches.", parent=self)

    # ------------------------------------------------------------------
    # menu actions
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings, self.fonts)
        self.wait_window(dialog)

    def _open_reports_folder(self) -> None:
        folder = self.settings.reports_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Cannot open folder", str(exc), parent=self)
            return
        try:
            if sys.platform == "win32":
                import os

                os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("Reports folder", f"{folder}\n\n({exc})", parent=self)

    def _maybe_show_first_run_notice(self) -> None:
        if self.settings.get("accepted_terms"):
            return
        messagebox.showinfo(f"{APP_NAME} - before you start", RESPONSIBLE_USE, parent=self)
        self.settings.update(accepted_terms=True)

    def _show_responsible_use(self) -> None:
        messagebox.showinfo("Responsible use", RESPONSIBLE_USE, parent=self)

    def _show_about(self) -> None:
        from ..core.net import HAVE_REQUESTS
        from ..core.email_scan import HAVE_DNS
        from ..core.phone_scan import HAVE_PHONENUMBERS
        from ..core.sites import load_sites

        optional = "\n".join([
            f"  requests       : {'yes' if HAVE_REQUESTS else 'NO (using urllib fallback)'}",
            f"  phonenumbers   : {'yes' if HAVE_PHONENUMBERS else 'NO (reduced phone data)'}",
            f"  dnspython      : {'yes' if HAVE_DNS else 'NO (no MX checks)'}",
        ])
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} v{__version__}\n\n"
            f"Site catalogue: {len(load_sites())} sites, from Tookie-OSINT\n"
            "by Alfredredbird, used under the MIT Licence.\n"
            "https://github.com/Alfredredbird/tookie-osint\n\n"
            f"Optional components:\n{optional}\n\n"
            f"History database:\n{self.history.path}",
            parent=self,
        )

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def _on_quit(self) -> None:
        if self._searching:
            if not messagebox.askyesno(
                "Search running",
                "A search is still running. Stop it and quit?", parent=self,
            ):
                return
            self._cancel.set()

        # Honour the save-on-close checkbox of any result window still open,
        # so quitting the app does not silently drop a report the user asked for.
        for window in list(self._open_results):
            try:
                window.close_for_shutdown()
            except tk.TclError:
                pass

        try:
            self.history.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def run() -> int:
    app = MainWindow()
    app.mainloop()
    return 0
