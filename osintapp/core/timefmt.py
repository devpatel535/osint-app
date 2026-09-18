"""Time formatting shared by the history list and the results header."""

from __future__ import annotations

import time

_STEPS = (
    (60, "second", 1),
    (3600, "minute", 60),
    (86400, "hour", 3600),
    (604800, "day", 86400),
    (2629746, "week", 604800),
    (31556952, "month", 2629746),
    (float("inf"), "year", 31556952),
)


def relative_age(when: float, now: float | None = None) -> str:
    """'just now', '4 minutes ago', '3 years ago'.

    The history list is explicitly unbounded, so this has to stay sensible for
    entries that are years old, not just hours.
    """
    now = time.time() if now is None else now
    delta = now - (when or 0)
    if delta < 0:
        return "just now"
    if delta < 45:
        return "just now"
    for limit, unit, divisor in _STEPS:
        if delta < limit:
            count = int(delta // divisor)
            count = max(1, count)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "a long time ago"


def absolute(when: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not when:
        return "unknown"
    return time.strftime(fmt, time.localtime(when))
