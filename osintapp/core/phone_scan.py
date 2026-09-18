"""Phone number investigation.

Everything here is derived from the number itself plus public numbering-plan
data. There is no legitimate public API that maps a phone number to a person's
identity, so this module does not pretend otherwise: it establishes what the
number *is* (valid? mobile or landline? which country, region and carrier?) and
then hands off a set of targeted searches for the analyst to run.

``phonenumbers`` (Google's libphonenumber port) does the heavy lifting when
installed. Without it there is a reduced fallback based on the E.164 country
code table, and the report says plainly that it is reduced.
"""

from __future__ import annotations

import re
from typing import List
from urllib.parse import quote_plus

from .context import ScanContext
from .models import CONFIRMED, ERROR, INFO, LIKELY, PIVOT, Finding, Section
from .query import normalize

try:  # pragma: no cover - optional dependency
    import phonenumbers
    from phonenumbers import carrier, geocoder, timezone as pn_timezone
    from phonenumbers.phonenumberutil import NumberParseException, PhoneNumberType

    HAVE_PHONENUMBERS = True
except Exception:  # noqa: BLE001
    phonenumbers = None  # type: ignore
    HAVE_PHONENUMBERS = False

# Enough of the E.164 table to give a useful answer when libphonenumber is
# missing. Longest-prefix wins, so 1-3 digit codes coexist.
_COUNTRY_CODES = {
    "1": "United States / Canada (NANP)", "7": "Russia / Kazakhstan",
    "20": "Egypt", "27": "South Africa", "30": "Greece", "31": "Netherlands",
    "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary",
    "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria",
    "44": "United Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway",
    "48": "Poland", "49": "Germany", "51": "Peru", "52": "Mexico",
    "53": "Cuba", "54": "Argentina", "55": "Brazil", "56": "Chile",
    "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia",
    "62": "Indonesia", "63": "Philippines", "64": "New Zealand",
    "65": "Singapore", "66": "Thailand", "81": "Japan", "82": "South Korea",
    "84": "Vietnam", "86": "China", "90": "Turkey", "91": "India",
    "92": "Pakistan", "93": "Afghanistan", "94": "Sri Lanka", "95": "Myanmar",
    "98": "Iran", "212": "Morocco", "213": "Algeria", "216": "Tunisia",
    "218": "Libya", "220": "Gambia", "233": "Ghana", "234": "Nigeria",
    "249": "Sudan", "251": "Ethiopia", "254": "Kenya", "255": "Tanzania",
    "256": "Uganda", "260": "Zambia", "263": "Zimbabwe", "351": "Portugal",
    "352": "Luxembourg", "353": "Ireland", "354": "Iceland", "355": "Albania",
    "358": "Finland", "359": "Bulgaria", "370": "Lithuania", "371": "Latvia",
    "372": "Estonia", "375": "Belarus", "380": "Ukraine", "381": "Serbia",
    "385": "Croatia", "386": "Slovenia", "420": "Czechia", "421": "Slovakia",
    "852": "Hong Kong", "853": "Macau", "855": "Cambodia", "856": "Laos",
    "880": "Bangladesh", "886": "Taiwan", "960": "Maldives", "961": "Lebanon",
    "962": "Jordan", "963": "Syria", "964": "Iraq", "965": "Kuwait",
    "966": "Saudi Arabia", "967": "Yemen", "968": "Oman", "970": "Palestine",
    "971": "United Arab Emirates", "972": "Israel", "973": "Bahrain",
    "974": "Qatar", "975": "Bhutan", "976": "Mongolia", "977": "Nepal",
    "992": "Tajikistan", "993": "Turkmenistan", "994": "Azerbaijan",
    "995": "Georgia", "996": "Kyrgyzstan", "998": "Uzbekistan",
}

_TYPE_LABELS = {}
if HAVE_PHONENUMBERS:  # pragma: no branch
    _TYPE_LABELS = {
        PhoneNumberType.MOBILE: "Mobile",
        PhoneNumberType.FIXED_LINE: "Landline",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "Landline or mobile",
        PhoneNumberType.TOLL_FREE: "Toll-free",
        PhoneNumberType.PREMIUM_RATE: "Premium rate",
        PhoneNumberType.SHARED_COST: "Shared cost",
        PhoneNumberType.VOIP: "VoIP",
        PhoneNumberType.PERSONAL_NUMBER: "Personal number",
        PhoneNumberType.PAGER: "Pager",
        PhoneNumberType.UAN: "Universal access number",
        PhoneNumberType.VOICEMAIL: "Voicemail",
        PhoneNumberType.UNKNOWN: "Unknown",
    }


def digits_only(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _guess_country(raw: str) -> str:
    """Longest-prefix country lookup for the no-library fallback."""
    digits = digits_only(raw)
    if not raw.strip().startswith("+") and not raw.strip().startswith("00"):
        return ""
    if raw.strip().startswith("00"):
        digits = digits[2:]
    for length in (3, 2, 1):
        if _COUNTRY_CODES.get(digits[:length]):
            return _COUNTRY_CODES[digits[:length]]
    return ""


def _parse_with_library(raw: str, section: Section) -> str:
    """Populate *section* from libphonenumber. Returns the E.164 form, if any."""
    parsed = None
    last_error = ""
    # Without a leading +, the parser needs a region hint. Try the most common
    # ones rather than silently failing on a domestic-format number.
    attempts = [None] if raw.strip().startswith("+") else [None, "US", "GB", "IN", "DE", "AU"]
    for region in attempts:
        try:
            candidate = phonenumbers.parse(raw, region)  # type: ignore[union-attr]
        except NumberParseException as exc:  # type: ignore[misc]
            last_error = str(exc)
            continue
        if phonenumbers.is_valid_number(candidate):  # type: ignore[union-attr]
            parsed = candidate
            break
        if parsed is None:
            parsed = candidate  # keep the first parse as a fallback

    if parsed is None:
        section.add(Finding(
            title="Could not parse the number",
            detail=last_error or "The value is not a recognisable phone number.",
            confidence=ERROR,
            source="phonenumbers",
        ))
        return ""

    valid = phonenumbers.is_valid_number(parsed)  # type: ignore[union-attr]
    possible = phonenumbers.is_possible_number(parsed)  # type: ignore[union-attr]
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)  # type: ignore[union-attr]
    intl = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)  # type: ignore[union-attr]
    national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)  # type: ignore[union-attr]

    region_code = phonenumbers.region_code_for_number(parsed) or ""  # type: ignore[union-attr]
    location = geocoder.description_for_number(parsed, "en") or ""
    network = carrier.name_for_number(parsed, "en") or ""
    zones = list(pn_timezone.time_zones_for_number(parsed) or ())
    number_type = _TYPE_LABELS.get(
        phonenumbers.number_type(parsed), "Unknown"  # type: ignore[union-attr]
    )

    attributes = {
        "E.164": e164,
        "International": intl,
        "National": national,
        "Country code": f"+{parsed.country_code}",
        "Region": region_code,
        "Line type": number_type,
    }
    if location:
        attributes["Geographic area"] = location
    if network:
        attributes["Carrier (at assignment)"] = network
    if zones:
        attributes["Time zone(s)"] = ", ".join(zones)

    section.add(Finding(
        title=intl or e164 or normalize(raw),
        detail=(
            "Valid, assignable number." if valid
            else "Matches the country's number length but is not a valid assigned number."
            if possible else "Does not match any known numbering plan - probably not a real number."
        ),
        confidence=CONFIRMED if valid else LIKELY,
        source="phonenumbers",
        attributes=attributes,
    ))

    if network:
        section.add(Finding(
            title=f"Carrier: {network}",
            detail=(
                "The carrier the number block was originally assigned to. "
                "Number portability means the current carrier may differ."
            ),
            confidence=LIKELY,
            source="phonenumbers",
        ))
    if location:
        section.add(Finding(
            title=f"Registered area: {location}",
            detail="Geographic area the number block belongs to - not the holder's current location.",
            confidence=LIKELY,
            source="phonenumbers",
        ))

    return e164 if valid else ""


def _parse_without_library(raw: str, section: Section) -> str:
    digits = digits_only(raw)
    country = _guess_country(raw)
    section.add(Finding(
        title=normalize(raw),
        detail=(
            "Reduced analysis: the 'phonenumbers' package is not installed, so validity, "
            "line type and carrier could not be determined."
        ),
        confidence=INFO,
        source="fallback",
        attributes={
            "Digits": digits,
            "Digit count": str(len(digits)),
            "Country (from prefix)": country or "unknown - no international prefix given",
        },
    ))
    section.notes.append(
        "Install 'phonenumbers' (pip install phonenumbers) for carrier, region, "
        "line type and validity checks."
    )
    return f"+{digits}" if raw.strip().startswith("+") else ""


def _add_pivots(raw: str, e164: str, section: Section) -> None:
    digits = digits_only(raw)
    forms = {f for f in (raw.strip(), e164, digits) if f}
    if len(digits) == 10:  # NANP-style grouping people actually post online
        forms.add(f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")
        forms.add(f"{digits[:3]}-{digits[3:6]}-{digits[6:]}")

    quoted = "+OR+".join(quote_plus(f'"{form}"') for form in sorted(forms))
    primary = quote_plus(f'"{e164 or raw.strip()}"')

    entries = [
        ("Google - every written form of the number",
         f"https://www.google.com/search?q={quoted}"),
        ("Bing - exact number", f"https://www.bing.com/search?q={primary}"),
        ("DuckDuckGo - exact number", f"https://duckduckgo.com/?q={primary}"),
        ("Facebook - search by number",
         f"https://www.facebook.com/search/top?q={quote_plus(e164 or digits)}"),
        ("Number posted in classifieds / listings",
         f"https://www.google.com/search?q={primary}+(site%3Acraigslist.org+OR+site%3Agumtree.com+OR+site%3Aolx.com)"),
        ("Number in leaked document dumps",
         f"https://www.google.com/search?q={primary}+(site%3Apastebin.com+OR+filetype%3Axlsx+OR+filetype%3Acsv)"),
        ("Truecaller (needs an account)",
         f"https://www.truecaller.com/search/global/{quote_plus(digits)}"),
        ("Who-called / spam-report databases",
         f"https://www.google.com/search?q={primary}+(whocalled+OR+%22who+called%22+OR+scam+OR+spam)"),
    ]
    for label, url in entries:
        section.add(Finding(
            title=label, url=url,
            detail="Open in a browser to continue by hand.",
            confidence=PIVOT, source="pivot",
        ))


def investigate(raw: str, ctx: ScanContext) -> List[Section]:
    raw = normalize(raw)
    overview = Section("Phone number")

    ctx.progress(0, 2, "Parsing number")
    if HAVE_PHONENUMBERS:
        e164 = _parse_with_library(raw, overview)
    else:
        e164 = _parse_without_library(raw, overview)

    pivots = Section("Manual follow-up")
    _add_pivots(raw, e164, pivots)

    pivots.notes.append(
        "No public API maps a phone number to a named individual. These searches "
        "look for places the number was published - listings, profiles, leaked "
        "documents - which is how a number is actually attributed in practice."
    )
    ctx.progress(2, 2, "Phone checks complete")
    return [overview, pivots]
