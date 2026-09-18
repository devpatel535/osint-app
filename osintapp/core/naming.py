"""Turning an arbitrary search query into a safe filename.

This matters more than it looks. The query is attacker-influenced text that we
paste into a path, so "../../../Windows/System32/x" or "CON" must not escape the
reports folder or collide with a DOS device. Upstream Tookie shipped a CVE fix
for exactly this class of bug; the rules below are the Windows-complete version:

* only ``[A-Za-z0-9._-]`` survives, so separators cannot appear at all
* ``..`` sequences are collapsed after substitution, not before
* Windows reserved device names (CON, NUL, COM1...) get a suffix
* Windows silently strips trailing dots and spaces from filenames, which would
  let ``foo.`` and ``foo`` collide, so both are stripped up front
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Reserved on Windows both bare and with any extension ("NUL.txt" is still NUL).
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_STEM = 96


def safe_stem(text: str, fallback: str = "search") -> str:
    """Reduce arbitrary text to a filename stem that is safe on every OS.

    A query written entirely in a non-Latin script survives none of the
    substitution, so it falls back to *fallback* plus a short digest of the
    original - otherwise every such search would produce the same filename.
    """
    original = str(text or "")
    candidate = _UNSAFE.sub("_", original)

    # Collapse repeatedly: "...." -> ".." -> "_" needs more than one pass.
    while ".." in candidate:
        candidate = candidate.replace("..", "_")

    candidate = candidate.strip(". _")
    candidate = re.sub(r"_{2,}", "_", candidate)

    if not candidate:
        candidate = fallback
        if original.strip():
            digest = hashlib.blake2b(original.encode("utf-8"), digest_size=4).hexdigest()
            candidate = f"{fallback}_{digest}"
    # Windows matches a device name against the part BEFORE the first dot, so
    # "NUL.txt" is still the NUL device. The suffix therefore has to go inside
    # that first segment - appending it to the end of the whole name would not
    # help ("nul.txt_file" is still NUL).
    head, dot, tail = candidate.partition(".")
    if head.upper() in _RESERVED:
        candidate = f"{head}_file{dot}{tail}"

    return candidate[:MAX_STEM].strip(". _") or fallback


def report_filename(query: str, query_type: str, when: float | None = None) -> str:
    """Build the .txt name for one search result.

    The timestamp goes last and is second-resolution, so repeat searches of the
    same subject sort chronologically in Explorer instead of overwriting.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
    return f"{safe_stem(query)}_{safe_stem(query_type, 'query')}_{stamp}.txt"


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path inside *directory* that does not exist yet.

    Two saves inside the same second would otherwise clobber each other.
    """
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for counter in range(2, 1000):
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{int(time.time() * 1000)}{suffix}"
