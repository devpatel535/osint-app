"""Plain data structures shared by the engine, the report writer, the history
store and the GUI.

Everything is JSON round-trippable: the exact object the results window shows
is what gets written into the history database, so re-opening a search from
three years ago renders identically to the day it ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Confidence levels, ordered from strongest to weakest. The report and the GUI
# both sort on this, so keep the order meaningful.
CONFIRMED = "confirmed"      # positively verified (body checked, no error text)
LIKELY = "likely"            # status says yes but we could not verify the body
POSSIBLE = "possible"        # derived/guessed, e.g. a username built from a name
INFO = "info"                # a fact about the query itself, not a discovery
PIVOT = "pivot"              # a hand-off link for the analyst to follow
ERROR = "error"              # the check itself failed

CONFIDENCE_ORDER = {CONFIRMED: 0, LIKELY: 1, POSSIBLE: 2, INFO: 3, PIVOT: 4, ERROR: 5}

CONFIDENCE_LABEL = {
    CONFIRMED: "Confirmed",
    LIKELY: "Likely",
    POSSIBLE: "Possible",
    INFO: "Info",
    PIVOT: "Manual check",
    ERROR: "Check failed",
}


@dataclass
class Finding:
    """One row of a result: a hit, a fact, or a link to follow up by hand."""

    title: str
    url: str = ""
    detail: str = ""
    confidence: str = CONFIRMED
    source: str = ""
    status: Optional[int] = None
    attributes: Dict[str, str] = field(default_factory=dict)

    @property
    def sort_key(self):
        return (CONFIDENCE_ORDER.get(self.confidence, 9), self.title.lower())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Finding":
        known = {f for f in cls.__dataclass_fields__}  # tolerate older snapshots
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Section:
    """A titled group of findings, e.g. "Social media profiles"."""

    name: str
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def sorted_findings(self) -> List[Finding]:
        return sorted(self.findings, key=lambda f: f.sort_key)

    @property
    def hit_count(self) -> int:
        return sum(1 for f in self.findings if f.confidence in (CONFIRMED, LIKELY))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "findings": [f.to_dict() for f in self.findings],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Section":
        return cls(
            name=raw.get("name", "Results"),
            findings=[Finding.from_dict(f) for f in raw.get("findings", [])],
            notes=list(raw.get("notes", [])),
        )


@dataclass
class SearchResult:
    """The complete outcome of one search."""

    query: str
    query_type: str
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    sections: List[Section] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False

    def section(self, name: str) -> Section:
        """Fetch a section by name, creating it on first use."""
        for existing in self.sections:
            if existing.name == name:
                return existing
        created = Section(name)
        self.sections.append(created)
        return created

    @property
    def duration(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def total_findings(self) -> int:
        return sum(len(s.findings) for s in self.sections)

    @property
    def hit_count(self) -> int:
        """Findings that represent an actual discovered account or record."""
        return sum(s.hit_count for s in self.sections)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "query_type": self.query_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sections": [s.to_dict() for s in self.sections],
            "errors": list(self.errors),
            "stats": dict(self.stats),
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SearchResult":
        return cls(
            query=raw.get("query", ""),
            query_type=raw.get("query_type", "unknown"),
            started_at=raw.get("started_at", 0.0),
            finished_at=raw.get("finished_at", 0.0),
            sections=[Section.from_dict(s) for s in raw.get("sections", [])],
            errors=list(raw.get("errors", [])),
            stats=dict(raw.get("stats", {})),
            cancelled=bool(raw.get("cancelled", False)),
        )


@dataclass
class Progress:
    """Pushed from the worker thread to the GUI while a search runs."""

    done: int = 0
    total: int = 0
    message: str = ""

    @property
    def fraction(self) -> float:
        return (self.done / self.total) if self.total else 0.0
