"""User settings, persisted as JSON next to the history database.

Loading is forgiving on purpose: a corrupt or hand-edited settings file must
never stop the app from starting, so unknown keys are dropped and unusable
values fall back to the default.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict

from ..paths import default_reports_dir, settings_path

DEFAULTS: Dict[str, Any] = {
    # --- scanning -------------------------------------------------------
    "threads": 24,               # concurrent site probes
    "timeout": 8.0,              # seconds per probe
    "include_nsfw": False,       # adult sites are in the Tookie list, off by default
    "name_candidates": 4,        # username spellings tried for a real-name search
    "proxy": "",                 # e.g. http://127.0.0.1:8080
    "verify_tls": True,
    # --- enrichment -----------------------------------------------------
    "fetch_profile_details": True,   # pull <title> / OpenGraph off hits
    "hibp_api_key": "",              # optional, paid; breach lookup stays off without it
    # --- output ---------------------------------------------------------
    "save_txt_default": True,    # initial state of the "save on close" checkbox
    "reports_dir": "",           # empty => paths.default_reports_dir()
    # --- misc -----------------------------------------------------------
    "accepted_terms": False,
}

# Values that must be numeric and within range, or the default is used instead.
_BOUNDS = {
    "threads": (1, 128),
    "timeout": (2.0, 120.0),
    "name_candidates": (1, 12),
}


class Settings:
    """Thread-safe settings holder. The GUI and worker threads both read it."""

    def __init__(self, path=None):
        self._path = path or settings_path()
        self._lock = threading.RLock()
        self._values = dict(DEFAULTS)
        self.load()

    # -- persistence -----------------------------------------------------
    def load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(stored, dict):
            return
        with self._lock:
            for key, default in DEFAULTS.items():
                if key not in stored:
                    continue
                self._values[key] = self._coerce(key, stored[key], default)

    def save(self) -> None:
        with self._lock:
            snapshot = dict(self._values)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, sort_keys=True)
            tmp.replace(self._path)  # atomic, so a crash mid-write cannot corrupt
        except OSError:
            pass  # read-only home dir: run with defaults rather than crashing

    @staticmethod
    def _coerce(key: str, value: Any, default: Any) -> Any:
        try:
            if isinstance(default, bool):
                return bool(value)
            if isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, str):
                value = str(value)
        except (TypeError, ValueError):
            return default
        low, high = _BOUNDS.get(key, (None, None))
        if low is not None and not (low <= value <= high):
            return default
        return value

    # -- access ----------------------------------------------------------
    def get(self, key: str, fallback: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, DEFAULTS.get(key, fallback))

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting: {key}")
        with self._lock:
            self._values[key] = self._coerce(key, value, DEFAULTS[key])

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            self.set(key, value)
        self.save()

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    # -- derived ---------------------------------------------------------
    def reports_dir(self):
        raw = str(self.get("reports_dir") or "").strip()
        if raw:
            from pathlib import Path

            return Path(raw).expanduser()
        return default_reports_dir()
