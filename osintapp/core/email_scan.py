"""Email address investigation.

What an email actually yields, in order of usefulness:

1. **Gravatar** - the single richest public source keyed on an email. The
   profile JSON is self-published and frequently lists the person's real name,
   location, bio and *their other social accounts*. That is a direct pivot.
2. **Domain intelligence** - MX/A records tell you whether the address can
   receive mail at all, and whether it is a free provider, a disposable
   burner, or a corporate domain (which names the employer).
3. **Derived handles** - the local part is very often the person's username
   elsewhere, so it feeds the site sweep.
4. **Breach exposure** - only with a HaveIBeenPwned API key, which is paid.
   Without one we link to the manual search page instead of pretending.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from .context import ScanContext
from .models import CONFIRMED, ERROR, INFO, LIKELY, PIVOT, Finding, Section
from .query import email_domain, email_local_part, looks_like_email, normalize

try:  # pragma: no cover - optional dependency
    import dns.resolver  # type: ignore

    HAVE_DNS = True
except Exception:  # noqa: BLE001
    dns = None  # type: ignore
    HAVE_DNS = False

FREE_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "pm.me", "gmx.com", "gmx.net", "gmx.de", "mail.com", "zoho.com", "yandex.ru",
    "yandex.com", "mail.ru", "inbox.ru", "list.ru", "bk.ru", "qq.com", "163.com",
    "126.com", "naver.com", "daum.net", "hanmail.net", "rediffmail.com",
    "tutanota.com", "tuta.io", "fastmail.com", "hushmail.com", "web.de",
    "seznam.cz", "libero.it", "orange.fr", "free.fr", "wanadoo.fr", "t-online.de",
}

DISPOSABLE_PROVIDERS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "throwawaymail.com", "yopmail.com", "trashmail.com",
    "getnada.com", "maildrop.cc", "dispostable.com", "fakeinbox.com",
    "sharklasers.com", "spam4.me", "mailnesia.com", "mohmal.com", "moakt.com",
    "tempr.email", "emailondeck.com", "burnermail.io", "anonaddy.com",
    "simplelogin.io", "duck.com", "mailsac.com", "inboxkitten.com",
}

ROLE_ACCOUNTS = {
    "admin", "administrator", "info", "support", "sales", "contact", "help",
    "billing", "office", "hello", "team", "noreply", "no-reply", "webmaster",
    "postmaster", "abuse", "security", "hr", "jobs", "careers", "marketing",
}


# --------------------------------------------------------------------------
# Gravatar
# --------------------------------------------------------------------------
def gravatar_hashes(address: str) -> Tuple[str, str]:
    """(sha256, md5) of the canonicalised address.

    Gravatar canonicalisation is "trim, lowercase". SHA-256 is the current
    identifier; MD5 is the legacy one and is still accepted, so both are tried.
    MD5 here is an address identifier, never a security primitive - flagged as
    such so the call still works on a FIPS-restricted Python build.
    """
    canonical = normalize(address).strip().lower().encode("utf-8")
    sha = hashlib.sha256(canonical).hexdigest()
    try:
        md5 = hashlib.md5(canonical, usedforsecurity=False).hexdigest()  # type: ignore[call-arg]
    except TypeError:  # Python < 3.9
        md5 = hashlib.md5(canonical).hexdigest()  # noqa: S324
    return sha, md5


def _gravatar_profile(ctx: ScanContext, digest: str) -> Optional[dict]:
    payload = ctx.fetcher.get_json(f"https://gravatar.com/{digest}.json")
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entry")
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return entries[0]
    return None


def _scan_gravatar(ctx: ScanContext, address: str, section: Section) -> None:
    sha, md5 = gravatar_hashes(address)

    avatar_found = False
    for digest in (sha, md5):
        if ctx.cancelled():
            return
        # d=404 makes Gravatar 404 instead of serving a generated default,
        # which is what turns this into a yes/no existence check.
        response = ctx.fetcher.get(
            f"https://www.gravatar.com/avatar/{digest}?d=404&s=200", want_body=False
        )
        if response.status == 200:
            section.add(Finding(
                title="Gravatar avatar exists",
                url=f"https://www.gravatar.com/avatar/{digest}?s=400",
                detail="This address has a Gravatar image, so it is registered with Gravatar.",
                confidence=CONFIRMED,
                source="gravatar",
                status=200,
            ))
            avatar_found = True
            break

    profile = None
    for digest in (sha, md5):
        if ctx.cancelled():
            return
        profile = _gravatar_profile(ctx, digest)
        if profile:
            break

    if not profile:
        if not avatar_found:
            section.add(Finding(
                title="No Gravatar profile",
                detail="No Gravatar avatar or public profile is registered for this address.",
                confidence=INFO,
                source="gravatar",
            ))
        return

    profile_url = str(profile.get("profileUrl") or "")
    attributes: Dict[str, str] = {}
    for key, label in (
        ("displayName", "Display name"),
        ("preferredUsername", "Username"),
        ("aboutMe", "About"),
        ("currentLocation", "Location"),
        ("job_title", "Job title"),
        ("company", "Company"),
    ):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            attributes[label] = value.strip()[:500]

    name_block = profile.get("name")
    if isinstance(name_block, dict):
        formatted = name_block.get("formatted")
        if isinstance(formatted, str) and formatted.strip():
            attributes.setdefault("Full name", formatted.strip())

    section.add(Finding(
        title="Gravatar public profile",
        url=profile_url,
        detail="Self-published profile tied to this email address.",
        confidence=CONFIRMED,
        source="gravatar",
        attributes=attributes,
    ))

    # The real prize: accounts the owner linked themselves.
    for account in profile.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        service = str(account.get("shortname") or account.get("domain") or "linked account")
        section.add(Finding(
            title=f"Linked account: {service}",
            url=str(account.get("url") or ""),
            detail="Listed by the account owner on their Gravatar profile.",
            confidence=CONFIRMED,
            source="gravatar",
            attributes={"Username": str(account.get("username") or account.get("display") or "")},
        ))

    for url_entry in profile.get("urls") or []:
        if not isinstance(url_entry, dict):
            continue
        section.add(Finding(
            title=f"Personal link: {url_entry.get('title') or 'website'}",
            url=str(url_entry.get("value") or ""),
            detail="Listed by the account owner on their Gravatar profile.",
            confidence=CONFIRMED,
            source="gravatar",
        ))


# --------------------------------------------------------------------------
# Domain
# --------------------------------------------------------------------------
def _resolve(domain: str, record: str) -> List[str]:
    if not HAVE_DNS:
        return []
    try:
        answers = dns.resolver.resolve(domain, record, lifetime=6.0)  # type: ignore[union-attr]
        return [str(a).strip() for a in answers]
    except Exception:  # noqa: BLE001 - NXDOMAIN, timeout, no answer all mean "nothing"
        return []


def _scan_domain(ctx: ScanContext, address: str, section: Section) -> None:
    domain = email_domain(address)
    local = email_local_part(address)
    if not domain:
        return

    if domain in DISPOSABLE_PROVIDERS:
        kind = "Disposable / burner provider"
        note = "Throwaway address. Weak link to a real identity, but the fact it was used is itself a signal."
    elif domain in FREE_PROVIDERS:
        kind = "Free consumer webmail"
        note = "Anyone can register this. The domain says nothing about the owner."
    else:
        kind = "Custom or corporate domain"
        note = "Not a known free provider. The domain itself may identify an employer or organisation - worth looking up."

    section.add(Finding(
        title=f"Domain: {domain}",
        url=f"https://{domain}",
        detail=f"{kind}. {note}",
        confidence=INFO,
        source="domain",
        attributes={"Local part": local, "Domain type": kind},
    ))

    if local.lower() in ROLE_ACCOUNTS:
        section.add(Finding(
            title="Role address, not a person",
            detail=(
                f"'{local}' is a shared/functional mailbox (support, info, admin...). "
                "It most likely maps to a team rather than an individual."
            ),
            confidence=INFO,
            source="domain",
        ))

    if not HAVE_DNS:
        section.notes.append(
            "DNS checks skipped: the 'dnspython' package is not installed. "
            "Install it with 'pip install dnspython' to see MX and A records."
        )
        return

    mx = _resolve(domain, "MX")
    if mx:
        section.add(Finding(
            title="Domain accepts mail (MX records present)",
            detail="; ".join(sorted(mx)[:6]),
            confidence=CONFIRMED,
            source="dns",
            attributes={"MX count": str(len(mx))},
        ))
    else:
        a_records = _resolve(domain, "A")
        if a_records:
            section.add(Finding(
                title="No MX records",
                detail=(
                    "The domain resolves but publishes no mail servers, so this address "
                    "probably cannot receive mail. Resolves to: " + ", ".join(a_records[:4])
                ),
                confidence=LIKELY,
                source="dns",
            ))
        else:
            section.add(Finding(
                title="Domain does not resolve",
                detail="No MX and no A record. The address is very likely invalid or the domain is gone.",
                confidence=LIKELY,
                source="dns",
            ))


# --------------------------------------------------------------------------
# Breaches (optional, requires a paid HIBP key)
# --------------------------------------------------------------------------
def _scan_breaches(ctx: ScanContext, address: str, section: Section) -> None:
    api_key = str(ctx.settings.get("hibp_api_key") or "").strip()
    if not api_key:
        section.add(Finding(
            title="Breach exposure - check manually",
            url=f"https://haveibeenpwned.com/account/{quote_plus(address)}",
            detail=(
                "Automated breach lookup needs a HaveIBeenPwned API key (paid). "
                "Add one under Settings, or open this link and check by hand."
            ),
            confidence=PIVOT,
            source="hibp",
        ))
        return

    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote_plus(address)}?truncateResponse=false"
    original_headers = ctx.fetcher._headers  # noqa: SLF001 - deliberate, see below

    def keyed_headers():
        # HIBP requires its key on the request; this is the only call that
        # needs custom headers, so it is patched in rather than widening the
        # Fetcher API for one caller.
        headers = original_headers()
        headers["hibp-api-key"] = api_key
        headers["User-Agent"] = "OSINT-Lookup-Desktop"
        return headers

    ctx.fetcher._headers = keyed_headers  # type: ignore[method-assign]
    try:
        response = ctx.fetcher.get(url)
    finally:
        ctx.fetcher._headers = original_headers  # type: ignore[method-assign]

    if response.status == 404:
        section.add(Finding(
            title="No known breaches",
            detail="HaveIBeenPwned has no breach records for this address.",
            confidence=CONFIRMED,
            source="hibp",
        ))
        return
    if response.status == 401:
        section.add(Finding(
            title="Breach lookup rejected",
            detail="HaveIBeenPwned refused the API key. Check it under Settings.",
            confidence=ERROR,
            source="hibp",
        ))
        return
    if response.status != 200:
        section.add(Finding(
            title="Breach lookup failed",
            detail=f"HaveIBeenPwned returned HTTP {response.status}. {response.error}".strip(),
            confidence=ERROR,
            source="hibp",
        ))
        return

    import json

    try:
        breaches = json.loads(response.body)
    except (ValueError, TypeError):
        breaches = []

    if not isinstance(breaches, list) or not breaches:
        section.add(Finding(
            title="No known breaches",
            detail="HaveIBeenPwned returned no breach records for this address.",
            confidence=CONFIRMED,
            source="hibp",
        ))
        return

    for breach in breaches:
        if not isinstance(breach, dict):
            continue
        classes = breach.get("DataClasses") or []
        section.add(Finding(
            title=f"Breach: {breach.get('Title') or breach.get('Name')}",
            url=f"https://haveibeenpwned.com/PwnedWebsites#{breach.get('Name', '')}",
            detail=f"Breached {breach.get('BreachDate', 'unknown date')}. "
                   f"Exposed: {', '.join(str(c) for c in classes) or 'unspecified'}",
            confidence=CONFIRMED,
            source="hibp",
            attributes={
                "Domain": str(breach.get("Domain") or ""),
                "Accounts affected": f"{breach.get('PwnCount', 0):,}",
                "Verified": str(breach.get("IsVerified", "")),
            },
        ))


# --------------------------------------------------------------------------
# Pivots
# --------------------------------------------------------------------------
def _add_pivots(address: str, section: Section) -> None:
    encoded = quote_plus(f'"{address}"')
    local = email_local_part(address)
    for label, url in (
        ("Google - exact address", f"https://www.google.com/search?q={encoded}"),
        ("Bing - exact address", f"https://www.bing.com/search?q={encoded}"),
        ("DuckDuckGo - exact address", f"https://duckduckgo.com/?q={encoded}"),
        ("GitHub commits by this address",
         f"https://github.com/search?q={quote_plus(address)}&type=commits"),
        ("Google - address in pastes/dumps",
         f"https://www.google.com/search?q={encoded}+(site%3Apastebin.com+OR+site%3Aghostbin.com)"),
        ("Have I Been Pwned", f"https://haveibeenpwned.com/account/{quote_plus(address)}"),
        ("Hunter.io - who else is at this domain",
         f"https://hunter.io/search/{quote_plus(email_domain(address))}"),
    ):
        section.add(Finding(
            title=label, url=url,
            detail="Open in a browser to continue by hand.",
            confidence=PIVOT, source="pivot",
        ))
    if local:
        section.add(Finding(
            title=f"Sweep the handle '{local}' across social sites",
            detail="The local part of an address is very often reused as a username.",
            confidence=PIVOT, source="pivot",
        ))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def investigate(address: str, ctx: ScanContext) -> List[Section]:
    address = normalize(address)
    overview = Section("Email address")

    if not looks_like_email(address):
        overview.add(Finding(
            title="Not a valid email address",
            detail=f"'{address}' does not parse as an address, so email-specific checks were skipped.",
            confidence=ERROR,
            source="validation",
        ))
        return [overview]

    overview.add(Finding(
        title=address,
        url=f"mailto:{address}",
        detail="Syntax is valid.",
        confidence=INFO,
        source="validation",
        attributes={
            "Local part": email_local_part(address),
            "Domain": email_domain(address),
        },
    ))

    ctx.progress(0, 4, "Checking domain records")
    _scan_domain(ctx, address, overview)

    if not ctx.cancelled():
        ctx.progress(1, 4, "Checking Gravatar")
        _scan_gravatar(ctx, address, overview)

    breaches = Section("Breach exposure")
    if not ctx.cancelled():
        ctx.progress(2, 4, "Checking breach exposure")
        _scan_breaches(ctx, address, breaches)

    pivots = Section("Manual follow-up")
    _add_pivots(address, pivots)
    ctx.progress(3, 4, "Email checks complete")

    return [overview, breaches, pivots]
