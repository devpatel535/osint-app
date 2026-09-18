"""Work out what the user typed.

The search box accepts a bare word, an email address, a real name or a phone
number, and picks the right investigation for it. The user can always override
the guess from the dropdown, so the rules here are tuned to be predictable
rather than clever - a wrong guess the user cannot explain is worse than a
conservative one.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

USERNAME = "username"
EMAIL = "email"
PHONE = "phone"
NAME = "name"

TYPE_LABELS = {
    USERNAME: "Username / keyword",
    EMAIL: "Email address",
    PHONE: "Phone number",
    NAME: "Real name",
}

# Deliberately permissive: we are classifying input, not certifying deliverability.
# RFC 5322 in full would accept quoted local parts nobody types into a search box.
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)

# Characters people use to lay out a phone number. Anything else means it is
# not a phone number, which keeps handles like "abc-123" out of the phone path.
PHONE_PUNCT = set(" -(). /–—.")

NAME_PARTICLES = {
    "de", "del", "della", "der", "di", "da", "dos", "du", "la", "le", "van",
    "von", "bin", "ibn", "al", "el", "st", "san", "mac", "mc", "ter", "ten",
}


def normalize(raw: str) -> str:
    """Trim, collapse inner whitespace, and normalise unicode look-alikes."""
    text = unicodedata.normalize("NFKC", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def _digits_and_punct_only(text: str) -> bool:
    seen_digit = False
    for char in text:
        if char.isdigit():
            seen_digit = True
        elif char in PHONE_PUNCT:
            continue
        elif char == "+":
            continue
        else:
            return False
    return seen_digit


def looks_like_email(text: str) -> bool:
    return bool(EMAIL_RE.match(text))


def looks_like_phone(text: str) -> bool:
    """A phone number is digits plus layout punctuation, 7-15 digits long.

    7 is the shortest usable subscriber number; 15 is the E.164 maximum. A
    leading + may appear only at the start.
    """
    if not text or not _digits_and_punct_only(text):
        return False
    if "+" in text[1:]:
        return False
    digits = re.sub(r"\D", "", text)
    return 7 <= len(digits) <= 15


def looks_like_name(text: str) -> bool:
    """Two or more alphabetic words - "Jane Doe", "Jean-Luc De Vries"."""
    if " " not in text:
        return False
    words = text.split(" ")
    if not 2 <= len(words) <= 6:
        return False
    for word in words:
        stripped = word.replace("-", "").replace("'", "").replace("’", "").rstrip(".")
        if not stripped or not stripped.isalpha():
            return False
    return True


def detect_type(raw: str) -> str:
    """Classify a query. Order matters: email before phone before name."""
    text = normalize(raw)
    if not text:
        return USERNAME
    if looks_like_email(text):
        return EMAIL
    if looks_like_phone(text):
        return PHONE
    if looks_like_name(text):
        return NAME
    return USERNAME


def split_name(raw: str) -> Tuple[str, List[str], str]:
    """Split a full name into (first, middles, last).

    Multi-word surname particles ("van der Berg") are folded into the surname
    so the generated handles read the way people actually write them.
    """
    words = [w for w in normalize(raw).split(" ") if w]
    if not words:
        return "", [], ""
    if len(words) == 1:
        return words[0], [], ""

    first = words[0]
    rest = words[1:]

    # Walk backwards from the end while the preceding word is a particle.
    cut = len(rest) - 1
    while cut > 0 and rest[cut - 1].lower().strip(".") in NAME_PARTICLES:
        cut -= 1
    last = " ".join(rest[cut:])
    middles = rest[:cut]
    return first, middles, last


def _clean_token(text: str) -> str:
    """Reduce a name part to the characters a username may contain."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]", "", ascii_only).lower()


def username_candidates(raw_name: str, limit: int = 8) -> List[str]:
    """Build the handles a person with this name most plausibly uses.

    Ordered most-likely first, because callers scan the full site list for the
    first candidate and a shortlist of major platforms for the rest.
    """
    first_raw, _middles, last_raw = split_name(raw_name)
    first = _clean_token(first_raw)
    last = _clean_token(last_raw)

    if not first:
        return []
    if not last:
        return [first][:limit]

    fi, li = first[0], last[0]
    ordered = [
        f"{first}{last}",
        f"{first}.{last}",
        f"{first}_{last}",
        f"{fi}{last}",
        f"{first}{li}",
        f"{last}{first}",
        f"{first}-{last}",
        f"{first}{last[0]}{li}" if len(last) > 1 else f"{first}{li}",
        first,
        last,
    ]

    seen, result = set(), []
    for candidate in ordered:
        if candidate and candidate not in seen and len(candidate) >= 3:
            seen.add(candidate)
            result.append(candidate)
    return result[:limit]


def email_local_part(address: str) -> str:
    return normalize(address).rsplit("@", 1)[0] if "@" in address else ""


def email_domain(address: str) -> str:
    return normalize(address).rsplit("@", 1)[-1].lower() if "@" in address else ""


def usernames_from_email(address: str) -> List[str]:
    """Handles worth scanning for an email address.

    "jane.doe+shopping@example.com" yields jane.doe, janedoe, jane - the plus
    tag is an alias, and the separator is often dropped on social platforms.
    """
    local = email_local_part(address)
    if not local:
        return []
    base = local.split("+", 1)[0]
    candidates = [base]
    flattened = re.sub(r"[._-]", "", base)
    if flattened != base and len(flattened) >= 3:
        candidates.append(flattened)
    head = re.split(r"[._-]", base)[0]
    if head != base and len(head) >= 3:
        candidates.append(head)
    seen, result = set(), []
    for candidate in candidates:
        if candidate and len(candidate) >= 3 and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result
