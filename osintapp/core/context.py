"""Everything a scanner needs to do its job, passed as one object.

Keeping this separate from ``engine`` lets each scanner module import it
without a circular dependency back to the orchestrator.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .net import Fetcher
from .settings import Settings


@dataclass
class ScanContext:
    fetcher: Fetcher
    settings: Settings
    cancel: threading.Event
    on_progress: Optional[Callable[[int, int, str], None]] = None

    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def progress(self, done: int, total: int, message: str = "") -> None:
        if self.on_progress is not None:
            self.on_progress(done, total, message)

    # Convenience accessors so scanners do not repeat the key strings.
    @property
    def threads(self) -> int:
        return self.settings.effective_threads()

    @property
    def include_nsfw(self) -> bool:
        return bool(self.settings.get("include_nsfw"))

    @property
    def fetch_details(self) -> bool:
        return bool(self.settings.get("fetch_profile_details"))
