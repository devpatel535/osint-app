"""Search orchestration.

One public entry point, ``run_search``. It picks the right investigation for
the query, runs it on the calling thread (the GUI runs it in a worker), and
returns a fully populated :class:`SearchResult` that can be rendered, written
to a report, and stored in history without further work.

Cancellation and progress are cooperative and pushed through
:class:`ScanContext`, so the GUI stays responsive and a half-finished search
still produces a usable result marked ``cancelled``.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional
from urllib.parse import quote_plus

from . import email_scan, name_scan, phone_scan
from .context import ScanContext
from .models import INFO, PIVOT, Finding, SearchResult, Section
from .net import HAVE_REQUESTS, Fetcher
from .query import EMAIL, NAME, PHONE, detect_type, normalize, usernames_from_email
from .settings import Settings
from .sweep import sweep_handles

ProgressFn = Callable[[int, int, str], None]


def _username_pivots(handle: str) -> Section:
    exact = quote_plus(f'"{handle}"')
    plain = quote_plus(handle)
    section = Section("Manual follow-up")
    entries = [
        ("Google - exact handle", f"https://www.google.com/search?q={exact}"),
        ("Bing - exact handle", f"https://www.bing.com/search?q={exact}"),
        ("DuckDuckGo - exact handle", f"https://duckduckgo.com/?q={exact}"),
        ("Reddit user", f"https://www.reddit.com/user/{plain}"),
        ("GitHub user search", f"https://github.com/search?q={plain}&type=users"),
        ("Handle mentioned in forums",
         f"https://www.google.com/search?q={exact}+(forum+OR+profile+OR+member)"),
        ("Handle in breach/paste dumps",
         f"https://www.google.com/search?q={exact}+(site%3Apastebin.com+OR+site%3Aghostbin.com)"),
        ("Archived profile pages", f"https://web.archive.org/web/*/{plain}*"),
        ("Images posted under this handle",
         f"https://www.google.com/search?q={exact}&tbm=isch"),
    ]
    for label, url in entries:
        section.add(Finding(
            title=label, url=url,
            detail="Open in a browser to continue by hand.",
            confidence=PIVOT, source="pivot",
        ))
    return section


def _environment_notes(result: SearchResult) -> None:
    """Tell the user when a missing optional package limited the search."""
    missing: List[str] = []
    if not HAVE_REQUESTS:
        missing.append(
            "'requests' is not installed - falling back to urllib, which is slower "
            "and less tolerant of odd servers (pip install requests)"
        )
    if not phone_scan.HAVE_PHONENUMBERS and result.query_type == PHONE:
        missing.append("'phonenumbers' is not installed (pip install phonenumbers)")
    if not email_scan.HAVE_DNS and result.query_type == EMAIL:
        missing.append("'dnspython' is not installed (pip install dnspython)")
    if missing:
        section = result.section("Notes")
        for note in missing:
            section.add(Finding(
                title="Reduced capability",
                detail=note,
                confidence=INFO,
                source="environment",
            ))


def run_search(
    raw_query: str,
    forced_type: Optional[str] = None,
    settings: Optional[Settings] = None,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[ProgressFn] = None,
) -> SearchResult:
    """Run one search end to end.

    *forced_type* overrides auto-detection when the user picked a type by hand.
    """
    settings = settings or Settings()
    cancel = cancel or threading.Event()
    query = normalize(raw_query)

    query_type = forced_type or detect_type(query)
    result = SearchResult(query=query, query_type=query_type, started_at=time.time())

    if not query:
        result.section("Notes").add(Finding(
            title="Empty search",
            detail="Type a username, email address, phone number or name to search.",
            confidence=INFO,
            source="validation",
        ))
        result.finished_at = time.time()
        return result

    fetcher = Fetcher(
        timeout=float(settings.get("timeout")),
        proxy=str(settings.get("proxy") or ""),
        verify_tls=bool(settings.get("verify_tls")),
    )
    ctx = ScanContext(fetcher=fetcher, settings=settings, cancel=cancel, on_progress=on_progress)

    try:
        if query_type == EMAIL:
            result.sections.extend(email_scan.investigate(query, ctx))
            handles = usernames_from_email(query)
            if handles and not ctx.cancelled():
                result.sections.extend(sweep_handles(
                    handles, ctx,
                    full_scan_first=True,
                    label="Accounts under handles derived from the address",
                    derived=True,
                ))

        elif query_type == PHONE:
            result.sections.extend(phone_scan.investigate(query, ctx))

        elif query_type == NAME:
            result.sections.extend(name_scan.investigate(query, ctx))

        else:  # USERNAME / keyword
            result.sections.extend(sweep_handles(
                [query], ctx, full_scan_first=True, label="Accounts found",
            ))
            result.sections.append(_username_pivots(query))

    except Exception as exc:  # noqa: BLE001 - a scanner bug must not lose the result
        result.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        fetcher.close()

    result.cancelled = cancel.is_set()
    result.finished_at = time.time()
    _environment_notes(result)

    result.stats = {
        "sections": len(result.sections),
        "findings": result.total_findings,
        "hits": result.hit_count,
        "duration_seconds": round(result.duration, 2),
        "cancelled": result.cancelled,
    }
    return result
