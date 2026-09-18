"""Username sweep across the site catalogue.

Upstream Tookie decides "account exists" from the HTTP status code alone in its
fast path, and only consults ``errorMessage`` when you opt into the Selenium
scraper. That produces a lot of false positives, because a large share of the
sites in the list answer 200 with a rendered "user not found" page.

This module keeps the plain-HTTP speed but adds the body check, so a soft-404
is caught without ChromeDriver:

    status >= 400                     -> no account
    status 2xx/3xx + error text found -> no account  (soft 404)
    status 2xx/3xx + bounced to "/"   -> no account  (homepage redirect)
    status 2xx/3xx + bounced to login -> unverifiable, flag for manual check
    status 2xx/3xx otherwise          -> account found
"""

from __future__ import annotations

import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from .models import CONFIRMED, ERROR, LIKELY, POSSIBLE, Finding
from .net import Fetcher, Response
from .sites import Site, extra_profile_fields

# Typographic variants that differ between a site's recorded error string and
# the HTML it actually serves. Without this the match rate drops sharply -
# many recorded messages use curly quotes where the page emits &#39;.
_CHAR_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "…": "...",
})

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(
    r"""<meta\s+[^>]*?(?:property|name)\s*=\s*["']([^"']+)["'][^>]*?\s"""
    r"""content\s*=\s*["']([^"']*)["']""",
    re.I | re.S,
)
_META_REVERSED_RE = re.compile(
    r"""<meta\s+[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?\s"""
    r"""(?:property|name)\s*=\s*["']([^"']+)["']""",
    re.I | re.S,
)

# Meta keys worth showing to the user, mapped to friendly labels.
_INTERESTING_META = {
    "og:title": "Display name",
    "og:description": "Bio",
    "og:image": "Avatar",
    "og:site_name": "Platform",
    "profile:username": "Handle",
    "profile:first_name": "First name",
    "profile:last_name": "Last name",
    "description": "Description",
    "twitter:title": "Title",
    "twitter:description": "Summary",
    "author": "Author",
    "keywords": "Keywords",
}

# Script/style bodies routinely embed JSON containing every string the site can
# render, including its 404 text - matching inside them would flag live profiles
# as missing. They are removed before any body comparison.
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_LOGIN_HINTS = ("/login", "/signin", "/sign_in", "/sign-in", "/auth", "/accounts/login",
                "/account/login", "/session/new", "/challenge", "/consent")

# Statuses that mean "the site refused to answer the question", not "no user".
_BLOCKED_STATUSES = {401, 403, 405, 406, 429}


def _fold(text: str) -> str:
    """Lowercase, decode entities, unify punctuation, collapse whitespace."""
    decoded = html.unescape(text or "")
    return re.sub(r"\s+", " ", decoded.translate(_CHAR_FOLD)).strip().lower()


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def visible_text(body: str) -> str:
    """Body with script/style blocks and all tags removed.

    Needed because a site's 404 string is frequently broken up by inline markup
    ("that<span> </span>content"), which defeats a plain substring search.
    """
    without_blocks = _SCRIPT_STYLE_RE.sub(" ", body or "")
    return html.unescape(_TAG_RE.sub(" ", without_blocks))


def body_contains_error(body: str, error_message: str) -> bool:
    """Does *body* show the site's "no such user" text?

    Tags and script/style blocks are stripped first, then the comparison is
    tried twice: once on punctuation-normalised text, then on an
    alphanumeric-only reduction. The second pass catches messages broken up by
    inline markup such as ``that<span> </span>content``.
    """
    if not body or not error_message:
        return False
    folded_message = _fold(error_message)
    if len(folded_message) < 4:
        return False  # too generic to be evidence

    text = visible_text(body)
    if folded_message in _fold(text):
        return True

    # Second pass: compare with every non-alphanumeric character removed, so
    # markup and punctuation inserted mid-sentence cannot hide the match.
    reduced = _alnum(folded_message)
    return len(reduced) >= 8 and reduced in _alnum(text)


def _is_homepage_bounce(probe_url: str, final_url: str) -> bool:
    """True when a profile URL redirected back to the site root."""
    if not final_url:
        return False
    try:
        probe, final = urlparse(probe_url), urlparse(final_url)
    except ValueError:
        return False
    probe_path = (probe.path or "/").rstrip("/")
    final_path = (final.path or "/").rstrip("/")
    if final_path not in ("", "/"):
        return False
    # Only a bounce if the probe actually pointed somewhere deeper than root.
    return bool(probe_path) or bool(probe.query)


def _is_login_bounce(final_url: str) -> bool:
    lowered = (final_url or "").lower()
    return any(hint in lowered for hint in _LOGIN_HINTS)


def extract_profile_details(body: str) -> Dict[str, str]:
    """Pull display name / bio / avatar out of a profile page's HTML head."""
    if not body:
        return {}

    found: Dict[str, str] = {}

    title_match = _TITLE_RE.search(body)
    if title_match:
        title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip()
        if title:
            found["Page title"] = title[:300]

    for pattern, order in ((_META_RE, "kv"), (_META_REVERSED_RE, "vk")):
        for first, second in pattern.findall(body):
            key, value = (first, second) if order == "kv" else (second, first)
            label = _INTERESTING_META.get(key.strip().lower())
            if not label or label in found:
                continue
            cleaned = html.unescape(re.sub(r"\s+", " ", value)).strip()
            if cleaned:
                found[label] = cleaned[:500]

    return found


@dataclass
class SiteHit:
    """Outcome of probing one site."""

    site: Site
    url: str
    status: Optional[int] = None
    found: bool = False
    verified: bool = False       # decided by reading the body, not just the status
    needs_review: bool = False   # login wall / rate limit: could not be decided
    error: str = ""
    reason: str = ""
    details: Dict[str, str] = field(default_factory=dict)

    def to_finding(self) -> Finding:
        if self.error:
            confidence = ERROR
        elif self.needs_review:
            confidence = POSSIBLE
        else:
            confidence = CONFIRMED if self.verified else LIKELY
        return Finding(
            title=self.site.name,
            url=self.url,
            detail=self.reason or self.error,
            confidence=confidence,
            source="site-probe",
            status=self.status,
            attributes=dict(self.details),
        )


def probe_site(site: Site, username: str, fetcher: Fetcher, fetch_details: bool = True) -> SiteHit:
    """Check one site for *username* and classify the answer."""
    url = site.probe_url(username)
    response: Response = fetcher.get(url, want_body=True)

    hit = SiteHit(site=site, url=url, status=response.status)

    if response.error or response.status is None:
        hit.error = response.error or "no response"
        return hit

    if response.status in _BLOCKED_STATUSES:
        hit.needs_review = True
        hit.reason = f"site refused the request (HTTP {response.status}) - check by hand"
        return hit

    if response.status >= 400:
        hit.reason = f"no account (HTTP {response.status})"
        return hit

    if site.has_error_signature and body_contains_error(response.body, site.error_message):
        hit.verified = True
        hit.reason = "no account (site returned its 'not found' page)"
        return hit

    if _is_login_bounce(response.final_url):
        hit.needs_review = True
        hit.reason = "redirected to a login wall - cannot confirm without signing in"
        return hit

    if _is_homepage_bounce(url, response.final_url):
        hit.reason = "no account (redirected to the site homepage)"
        return hit

    hit.found = True
    hit.verified = site.has_error_signature
    hit.reason = (
        "account found (HTTP %s, 'not found' text absent)" % response.status
        if site.has_error_signature
        else "account found (HTTP %s; no 'not found' signature on record for this site)"
        % response.status
    )
    if fetch_details:
        hit.details = extract_profile_details(response.body)
        # Tookie's field database records what else this platform publishes on
        # a public profile. Those values need a rendered page to read, which
        # this app deliberately does not do - but naming them tells the analyst
        # which hits are worth opening by hand.
        extras = extra_profile_fields(site.domain)
        if extras:
            hit.details["Also on this profile"] = ", ".join(extras)
    return hit


def scan_username(
    username: str,
    sites: Sequence[Site],
    fetcher: Fetcher,
    threads: int = 24,
    fetch_details: bool = True,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[SiteHit]:
    """Probe every site concurrently. Returns hits in completion order.

    Cancellation is cooperative: queued probes exit immediately once *cancel*
    is set, and the pool is torn down without waiting for in-flight requests.
    """
    sites = list(sites)
    total = len(sites)
    results: List[SiteHit] = []
    if not username or total == 0:
        return results

    done = 0
    lock = threading.Lock()

    def worker(site: Site) -> Optional[SiteHit]:
        if cancel is not None and cancel.is_set():
            return None
        return probe_site(site, username, fetcher, fetch_details)

    with ThreadPoolExecutor(max_workers=max(1, int(threads))) as pool:
        futures = {pool.submit(worker, site): site for site in sites}
        try:
            for future in as_completed(futures):
                hit = future.result()
                with lock:
                    done += 1
                    current = done
                if hit is not None:
                    results.append(hit)
                if on_progress is not None:
                    label = hit.site.name if hit else futures[future].name
                    on_progress(current, total, f"{username} @ {label}")
                if cancel is not None and cancel.is_set():
                    break
        finally:
            if cancel is not None and cancel.is_set():
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)

    return results
