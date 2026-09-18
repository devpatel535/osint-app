"""The site catalogue, loaded from the vendored Tookie-OSINT database.

Each upstream entry looks like::

    {"site": "https://www.twitch.tv/", "nsfw": "false",
     "errorMessage": "Sorry, that page is in another castle!"}

``site`` is a URL *prefix* - the username is appended verbatim. ``errorMessage``
is the text a site shows for a profile that does not exist, which is what lets
us catch soft-404s (HTTP 200 with a "no such user" page) without a browser.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

from ..paths import resource_path

# Upstream writes "none" when nobody has recorded the site's 404 text yet.
_NO_ERROR_SENTINEL = {"", "none", "n/a", "null", "no error message defined"}

# Platforms worth checking for every candidate spelling of a real name. The
# full 260-site sweep is reserved for the single best candidate; running it for
# every permutation would quadruple the request count for little extra signal.
PRIORITY_DOMAINS = {
    "youtube.com", "twitch.tv", "x.com", "twitter.com", "facebook.com",
    "instagram.com", "tiktok.com", "reddit.com", "github.com", "gitlab.com",
    "linkedin.com", "pinterest.com", "tumblr.com", "flickr.com", "vimeo.com",
    "soundcloud.com", "spotify.com", "medium.com", "dev.to", "about.me",
    "patreon.com", "kickstarter.com", "steamcommunity.com", "roblox.com",
    "telegram.me", "t.me", "snapchat.com", "vk.com", "ok.ru", "quora.com",
    "stackoverflow.com", "behance.net", "dribbble.com", "deviantart.com",
    "last.fm", "bandcamp.com", "mixcloud.com", "9gag.com", "imgur.com",
    "keybase.io", "hackerone.com", "bitbucket.org", "codepen.io",
    "producthunt.com", "slideshare.net", "goodreads.com", "wattpad.com",
    "chess.com", "trakt.tv", "letterboxd.com", "500px.com", "ello.co",
    "myspace.com", "buymeacoffee.com", "ko-fi.com", "linktr.ee",
}


@dataclass(frozen=True)
class Site:
    """One probe target."""

    url_prefix: str
    nsfw: bool
    error_message: str
    domain: str

    @property
    def name(self) -> str:
        """Human label: 'twitch.tv', 'forum.3dnews.ru'."""
        return self.domain

    @property
    def has_error_signature(self) -> bool:
        """True when we can verify a hit by inspecting the page body."""
        return bool(self.error_message)

    @property
    def is_priority(self) -> bool:
        return self.domain in PRIORITY_DOMAINS

    def probe_url(self, username: str) -> str:
        """Build the URL to fetch for *username*.

        The username is percent-encoded so a query containing '#', '?' or a
        space cannot rewrite the URL's structure. '@' and '.' are left intact
        because several prefixes end in '@' and handles legitimately contain
        dots.
        """
        return self.url_prefix + quote(username, safe="@._-~")


def _domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        netloc = ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0] or url


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


_cache: Optional[List[Site]] = None
_cache_lock = threading.Lock()


def load_sites(path=None) -> List[Site]:
    """Parse sites.json once and memoise it.

    Malformed individual entries are skipped rather than aborting the load -
    the database is community-maintained and one bad record should not take
    the whole app down.
    """
    global _cache
    if path is None:
        with _cache_lock:
            if _cache is not None:
                return _cache

    source = path or resource_path("sites.json")
    try:
        with open(source, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raw = []

    sites: List[Site] = []
    seen = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        prefix = str(entry.get("site", "")).strip()
        if not prefix.startswith(("http://", "https://")):
            continue
        if prefix in seen:
            continue
        seen.add(prefix)

        message = str(entry.get("errorMessage", "") or "").strip()
        if message.lower() in _NO_ERROR_SENTINEL:
            message = ""

        sites.append(
            Site(
                url_prefix=prefix,
                nsfw=_as_bool(entry.get("nsfw")),
                error_message=message,
                domain=_domain_of(prefix),
            )
        )

    if path is None:
        with _cache_lock:
            _cache = sites
    return sites


def select_sites(include_nsfw: bool = False, priority_only: bool = False, path=None) -> List[Site]:
    """The site list for one scan, filtered by the user's settings."""
    sites = load_sites(path)
    if not include_nsfw:
        sites = [s for s in sites if not s.nsfw]
    if priority_only:
        sites = [s for s in sites if s.is_priority]
    return sites


def load_profile_fields(path=None) -> Dict[str, dict]:
    """Per-domain field hints from Tookie's fields.json.

    Upstream feeds these XPath selectors to Selenium. We do not drive a
    browser, so they are used only as a hint of which attributes are worth
    surfacing for a given domain.
    """
    source = path or resource_path("profile_fields.json")
    try:
        with open(source, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
