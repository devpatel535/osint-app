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


_fields_cache: Optional[Dict[str, List[str]]] = None
_fields_lock = threading.Lock()

# Field names we already read out of the page head ourselves, so listing them
# again as "also published here" would be noise.
_ALREADY_EXTRACTED = {"username", "handle", "bio", "description", "avatar", "name"}


def load_profile_fields(path=None) -> Dict[str, List[str]]:
    """Per-domain attribute names from Tookie's fields.json.

    Upstream feeds these selectors to Selenium to scrape each value. This app
    does not drive a browser, so the selectors themselves are of no use here -
    but the *field names* are: they record which extra attributes a given
    platform publishes on a public profile (about.me exposes location and
    linked socials; 7cups exposes rank and last-active). Surfacing that on a
    hit tells the analyst which profiles are worth opening by hand.

    Keys are normalised the same way site domains are, so lookups match.
    """
    global _fields_cache
    if path is None:
        with _fields_lock:
            if _fields_cache is not None:
                return _fields_cache

    source = path or resource_path("profile_fields.json")
    try:
        with open(source, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raw = {}

    parsed: Dict[str, List[str]] = {}
    if isinstance(raw, dict):
        for domain, fields in raw.items():
            if not isinstance(fields, dict):
                continue
            key = str(domain).strip().lower()
            if key.startswith("www."):
                key = key[4:]
            names = sorted(str(f).strip().lower() for f in fields if str(f).strip())
            if key and names:
                parsed[key] = names

    if path is None:
        with _fields_lock:
            _fields_cache = parsed
    return parsed


def extra_profile_fields(domain: str) -> List[str]:
    """Human-readable extra attributes this platform publishes, if any.

    Returns only the ones this app cannot already read from the page head,
    so the hint adds information instead of repeating what is on screen.
    """
    fields = load_profile_fields().get((domain or "").lower(), [])
    return [f.replace("_", " ") for f in fields if f not in _ALREADY_EXTRACTED]
