"""Reusable Tk widgets.

The important one is :class:`ScrollableFrame`. Tk has no scrollable container,
so it is built from a Canvas plus an inner Frame, with two details that are
easy to get wrong:

* **Mouse wheel** events differ per platform - Windows and macOS send
  ``<MouseWheel>`` with a delta, X11 sends ``<Button-4>``/``<Button-5>``. The
  binding is also scoped with Enter/Leave rather than ``bind_all`` for the
  lifetime of the widget, otherwise the main window's history list would eat
  wheel events aimed at a results window.
* **Near-bottom notification** is what turns the history list into an infinite
  scroller: the callback fires while the user still has a screenful left, so
  the next page is already in place by the time they reach it.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from . import theme


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container. Put children in ``.body``."""

    def __init__(
        self,
        master: tk.Misc,
        background: str = theme.BG,
        on_near_bottom: Optional[Callable[[], None]] = None,
        near_bottom_fraction: float = 0.82,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self._on_near_bottom = on_near_bottom
        self._near_bottom_fraction = near_bottom_fraction
        self._notified = False

        self.canvas = tk.Canvas(
            self, bg=background, highlightthickness=0, borderwidth=0, takefocus=0
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar"
        )
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.body = ttk.Frame(self.canvas, style="TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Scope wheel handling to "pointer is over this widget".
        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)

    # -- geometry --------------------------------------------------------
    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        # Keep the inner frame exactly as wide as the viewport so children can
        # use sticky="ew" and fill the row.
        self.canvas.itemconfigure(self._window, width=event.width)

    # -- scrolling -------------------------------------------------------
    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        if self._on_near_bottom is None:
            return
        try:
            last_value = float(last)
        except (TypeError, ValueError):
            return
        if last_value >= 1.0 and float(first) <= 0.0:
            return  # everything fits; nothing to page in yet
        if last_value >= self._near_bottom_fraction:
            if not self._notified:
                self._notified = True
                self.after_idle(self._fire_near_bottom)
        else:
            self._notified = False

    def _fire_near_bottom(self) -> None:
        if self._on_near_bottom is not None:
            self._on_near_bottom()

    def reset_paging_flag(self) -> None:
        """Allow the near-bottom callback to fire again after new rows land."""
        self._notified = False

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)
        self._notified = False

    # -- wheel -----------------------------------------------------------
    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_wheel_x11, add="+")
        self.canvas.bind_all("<Button-5>", self._on_wheel_x11, add="+")

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event) -> None:
        # Windows reports multiples of 120; macOS reports small raw deltas.
        if sys.platform == "darwin":
            steps = -1 * int(event.delta)
        else:
            steps = -1 * int(event.delta / 120)
        if steps:
            self.canvas.yview_scroll(steps, "units")

    def _on_wheel_x11(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")


class Badge(tk.Canvas):
    """A small round avatar-style badge with a single character inside."""

    def __init__(self, master: tk.Misc, text: str, colour: str, size: int = 34,
                 background: str = theme.SURFACE, font=None):
        super().__init__(master, width=size, height=size, bg=background,
                         highlightthickness=0, borderwidth=0, takefocus=0)
        pad = 1
        self.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline="")
        self.create_text(size / 2, size / 2 + 1, text=text[:1].upper(),
                         fill="#ffffff", font=font or ("TkDefaultFont", 12, "bold"))


class HoverRow(ttk.Frame):
    """A frame that highlights on hover and reports clicks.

    Tk does not propagate <Enter>/<Leave> from children, so every descendant is
    bound explicitly - otherwise the highlight flickers off the moment the
    pointer crosses a label inside the row.
    """

    def __init__(self, master: tk.Misc, on_click: Optional[Callable[[], None]] = None,
                 normal: str = theme.SURFACE, hover: str = theme.SURFACE_HOVER, **kwargs):
        super().__init__(master, **kwargs)
        self._on_click = on_click
        self._normal = normal
        self._hover = hover
        self._style_name = f"Row{id(self)}.TFrame"
        self._style = ttk.Style()
        self._style.configure(self._style_name, background=normal)
        self.configure(style=self._style_name)

    def bind_hover(self, widget: tk.Misc, clickable: bool = True) -> None:
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        if clickable and self._on_click is not None:
            widget.bind("<Button-1>", self._click, add="+")
            try:
                widget.configure(cursor="hand2")
            except tk.TclError:
                pass

    def bind_tree(self, widget: tk.Misc, clickable: bool = True) -> None:
        """Apply hover/click bindings to *widget* and everything inside it."""
        self.bind_hover(widget, clickable)
        for child in widget.winfo_children():
            self.bind_tree(child, clickable)

    def set_backgrounds(self, colour: str) -> None:
        self._style.configure(self._style_name, background=colour)
        self._paint(self, colour)

    def _paint(self, widget: tk.Misc, colour: str) -> None:
        for child in widget.winfo_children():
            if isinstance(child, (tk.Label, tk.Canvas, tk.Frame)):
                try:
                    child.configure(bg=colour)
                except tk.TclError:
                    pass
            self._paint(child, colour)

    def _enter(self, _event=None) -> None:
        self.set_backgrounds(self._hover)

    def _leave(self, _event=None) -> None:
        self.set_backgrounds(self._normal)

    def _click(self, _event=None) -> None:
        if self._on_click is not None:
            self._on_click()


class PlaceholderEntry(ttk.Entry):
    """An entry showing grey placeholder text while empty and unfocused."""

    def __init__(self, master: tk.Misc, placeholder: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        if not super().get() and self._placeholder:
            self._showing = True
            super().insert(0, self._placeholder)
            self.configure(foreground=theme.TEXT_FAINT)

    def _clear_placeholder(self) -> None:
        if self._showing:
            self._showing = False
            super().delete(0, "end")
            self.configure(foreground=theme.TEXT)

    def _on_focus_in(self, _event=None) -> None:
        self._clear_placeholder()

    def _on_focus_out(self, _event=None) -> None:
        if not super().get():
            self._show_placeholder()

    # -- API that hides the placeholder from callers ---------------------
    def get(self) -> str:  # type: ignore[override]
        return "" if self._showing else super().get()

    def set(self, value: str) -> None:
        self._clear_placeholder()
        super().delete(0, "end")
        if value:
            self.configure(foreground=theme.TEXT)
            super().insert(0, value)
        else:
            self._show_placeholder()


def separator(master: tk.Misc, colour: str = theme.BORDER) -> tk.Frame:
    """A 1px hairline. A plain Frame beats ttk.Separator for exact colouring.

    Returned unmanaged - the caller packs or grids it where it belongs.
    """
    return tk.Frame(master, height=1, bg=colour, borderwidth=0, highlightthickness=0)
