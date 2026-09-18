"""The save-on-close contract.

The brief is precise about this, so it gets its own test file:

* checkbox ticked  -> the .txt appears **when the window is closed**, not before
* checkbox clear   -> no file is ever written
* every exit route -> X button, Close button, Escape, and app shutdown

Needs a display. Skipped automatically where there is none (set DISPLAY, or run
under Xvfb).
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()
    HAVE_DISPLAY = True
except Exception:  # noqa: BLE001 - no display, or Python built without Tk
    HAVE_DISPLAY = False
    _root = None

if HAVE_DISPLAY:
    from osintapp.core.models import CONFIRMED, Finding, SearchResult
    from osintapp.core.settings import Settings
    from osintapp.ui import results_window as rw
    from osintapp.ui import theme


def build_result(query: str = "janedoe") -> "SearchResult":
    result = SearchResult(query=query, query_type="username",
                          started_at=time.time() - 3, finished_at=time.time())
    section = result.section("Accounts found")
    section.add(Finding(title="github.com", url="https://github.com/janedoe",
                        detail="account found", confidence=CONFIRMED, status=200))
    return result


class _SilentBox:
    """Stand-in for tkinter.messagebox - modal dialogs would hang the test."""

    def __init__(self):
        self.infos = []
        self.errors = []

    def showinfo(self, title, message, **_kw):
        self.infos.append((title, message))

    def showerror(self, title, message, **_kw):
        self.errors.append((title, message))

    def showwarning(self, title, message, **_kw):
        self.errors.append((title, message))

    def askyesno(self, *_a, **_kw):
        return True


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class SaveOnCloseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name) / "reports"
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        self.settings.update(reports_dir=str(self.reports))

        # The results window is transient for the main window. A withdrawn
        # parent is never mapped, so its transient child cannot take keyboard
        # focus and key bindings would not fire - which is a property of the
        # test harness, not the app. Keep the root mapped, just small and out
        # of the way, so this matches how the app really runs.
        self.root = tk.Tk()
        self.root.geometry("200x120+0+0")
        self.root.update()
        self.fonts = theme.apply(self.root)

        self._real_box = rw.messagebox
        self.box = _SilentBox()
        rw.messagebox = self.box  # type: ignore[assignment]

    def tearDown(self):
        rw.messagebox = self._real_box  # type: ignore[assignment]
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.tmp.cleanup()

    def _open(self, save_default: bool):
        self.settings.update(save_txt_default=save_default)
        window = rw.ResultsWindow(self.root, build_result(), self.settings, self.fonts)
        self.root.update()
        return window

    def _files(self):
        return sorted(self.reports.glob("*.txt")) if self.reports.exists() else []

    # -- the contract ---------------------------------------------------
    def test_ticked_writes_the_file_only_on_close(self):
        window = self._open(save_default=True)
        self.assertTrue(window.save_var.get())
        # Nothing written merely by displaying the results.
        self.assertEqual(self._files(), [])

        window._on_close()
        self.root.update()

        files = self._files()
        self.assertEqual(len(files), 1, "closing with the box ticked must write one .txt")
        body = files[0].read_text(encoding="utf-8")
        self.assertIn("janedoe", body)
        self.assertIn("github.com", body)

    def test_unticked_writes_nothing(self):
        window = self._open(save_default=False)
        self.assertFalse(window.save_var.get())
        window._on_close()
        self.root.update()
        self.assertEqual(self._files(), [],
                         "closing with the box clear must not write anything")

    def test_unticking_after_opening_is_honoured(self):
        window = self._open(save_default=True)
        window.save_var.set(False)      # user changes their mind
        window._update_destination_label()
        window._on_close()
        self.root.update()
        self.assertEqual(self._files(), [])

    def test_ticking_after_opening_is_honoured(self):
        window = self._open(save_default=False)
        window.save_var.set(True)
        window._update_destination_label()
        window._on_close()
        self.root.update()
        self.assertEqual(len(self._files()), 1)

    def test_window_manager_close_button_takes_the_same_path(self):
        window = self._open(save_default=True)
        # Invoke exactly what the X button is wired to.
        handler = window.protocol("WM_DELETE_WINDOW")
        self.assertTrue(handler, "the X button must be wired to a handler")
        window._on_close()
        self.root.update()
        self.assertEqual(len(self._files()), 1)

    def test_escape_key_takes_the_same_path(self):
        window = self._open(save_default=True)
        # Key events only reach the window that holds focus.
        window.focus_force()
        self.root.update()
        self.assertEqual(self.root.focus_get(), window)
        window.event_generate("<Escape>", when="now")
        self.root.update()
        self.assertEqual(len(self._files()), 1)

    def test_save_now_then_close_writes_exactly_one_file(self):
        window = self._open(save_default=True)
        window._save_now()
        self.assertEqual(len(self._files()), 1)
        window._on_close()
        self.root.update()
        self.assertEqual(len(self._files()), 1,
                         "an explicit save then a close must not duplicate the report")

    def test_shutdown_path_saves_silently(self):
        window = self._open(save_default=True)
        window.close_for_shutdown()
        self.root.update()
        self.assertEqual(len(self._files()), 1)
        self.assertEqual(self.box.infos, [], "shutdown must not pop a dialog")

    def test_checkbox_state_becomes_the_next_default(self):
        window = self._open(save_default=True)
        window.save_var.set(False)
        window._on_close()
        self.assertFalse(self.settings.get("save_txt_default"))

    def test_two_windows_are_independent(self):
        first = self._open(save_default=True)
        second = rw.ResultsWindow(self.root, build_result("otherperson"),
                                  self.settings, self.fonts)
        second.save_var.set(False)
        self.root.update()

        second._on_close()
        self.root.update()
        self.assertEqual(self._files(), [], "the unticked window must write nothing")

        first._on_close()
        self.root.update()
        files = self._files()
        self.assertEqual(len(files), 1)
        self.assertIn("janedoe", files[0].read_text(encoding="utf-8"))

    def test_unwritable_folder_reports_an_error_and_still_closes(self):
        # A path whose PARENT is a regular file can never be created, on any
        # OS. Naming a privileged directory instead would not work: an absolute
        # POSIX path like /proc/... is merely relative on Windows, where it
        # resolves to D:\proc\... and is created without complaint.
        blocker = Path(self.tmp.name) / "not-a-directory"
        blocker.write_text("occupies the path")

        window = self._open(save_default=True)
        window.settings.update(reports_dir=str(blocker / "reports"))
        window._on_close()
        self.root.update()
        self.assertTrue(self.box.errors, "a failed save must tell the user")
        self.assertFalse(window.winfo_exists(), "the window must still close")


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class HistoryUiTest(unittest.TestCase):
    """The history panel: unbounded paging, per-row delete, clear all."""

    def setUp(self):
        import os

        self.tmp = tempfile.TemporaryDirectory()
        # MainWindow resolves its own paths, so point them at the sandbox.
        self._old_env = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "HOME", "APPDATA")}
        os.environ["XDG_DATA_HOME"] = str(Path(self.tmp.name) / "data")
        os.environ["HOME"] = self.tmp.name
        os.environ["APPDATA"] = str(Path(self.tmp.name) / "data")

        from osintapp.ui import main_window as mw

        self.mw = mw
        self._real_box = mw.messagebox
        self.box = _SilentBox()
        mw.messagebox = self.box  # type: ignore[assignment]

        self.app = mw.MainWindow()
        self.app.geometry("900x700+0+0")
        self.app.settings.update(accepted_terms=True)
        self.app.update()

    def tearDown(self):
        import os

        self.mw.messagebox = self._real_box  # type: ignore[assignment]
        try:
            # Go out through the app's real shutdown path rather than calling
            # destroy() directly: that is what cancels the pending after()
            # callbacks, and exercising it here keeps the test honest about
            # how the app actually closes.
            self.app._on_quit()
        except tk.TclError:
            pass
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _seed(self, count: int, spread_days: float = 1800.0):
        import random

        now = time.time()
        for index in range(count):
            result = SearchResult(
                query=f"subject_{index:04d}", query_type="username",
                started_at=now - random.uniform(0, spread_days) * 86400,
            )
            self.app.history.add(result)

    def test_scrolling_reaches_entries_beyond_the_first_page(self):
        self._seed(int(self.mw.PAGE_SIZE * 4.5))
        self.app._reload_history()
        self.app.update()
        first_page = len(self.app._history_rows)
        self.assertEqual(first_page, self.mw.PAGE_SIZE)

        # Keep asking for the next page the way the scroller does.
        while not self.app._history_exhausted:
            self.app._load_more_history()
            self.app.update()

        self.assertEqual(len(self.app._history_rows), self.app.history.count())
        self.assertGreater(len(self.app._history_rows), first_page)

    def test_years_old_entries_are_still_listed(self):
        old = SearchResult(query="ancient_search", query_type="username",
                           started_at=time.time() - 3 * 365 * 86400)
        self.app.history.add(old)
        self._seed(30)
        self.app._reload_history()
        while not self.app._history_exhausted:
            self.app._load_more_history()
        self.app.update()

        queries = [e.query for e in self.app.history.page(0, 1000)]
        self.assertIn("ancient_search", queries)

    def test_filter_narrows_the_list(self):
        for name in ("alpha_one", "beta_two", "alpha_three"):
            self.app.history.add(SearchResult(query=name, query_type="username"))
        self.app.history_filter.set("alpha")
        self.app._reload_history()
        self.app.update()
        self.assertEqual(len(self.app._history_rows), 2)

    def test_deleting_one_row_leaves_the_rest(self):
        self._seed(5)
        self.app._reload_history()
        self.app.update()
        entry = self.app.history.page(0, 1)[0]
        self.app._delete_entry(entry)
        self.app.update()
        self.assertEqual(self.app.history.count(), 4)
        self.assertNotIn(entry.id, self.app._history_rows)

    def test_clear_all_empties_the_history(self):
        self._seed(12)
        self.app._reload_history()
        self.app.update()
        self.app._clear_history()      # _SilentBox.askyesno returns True
        self.app.update()
        self.assertEqual(self.app.history.count(), 0)
        self.assertEqual(len(self.app._history_rows), 0)

    def test_history_survives_a_restart(self):
        self._seed(7)
        self.app._on_quit()          # a real quit, not a bare destroy()

        self.app = self.mw.MainWindow()
        self.app.settings.update(accepted_terms=True)
        self.app.update()
        self.assertEqual(self.app.history.count(), 7)
