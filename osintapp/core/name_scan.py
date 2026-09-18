"""Real-name investigation.

A name is the weakest of the four inputs: it is not unique, and no public API
resolves one to a person. What works in practice is a two-pronged approach:

1. Convert the name into the handles that person plausibly uses
   ("Jane Doe" -> janedoe, jane.doe, jdoe...) and sweep those across the site
   catalogue. This is the part that can be automated.
2. Emit targeted search-engine queries that a human runs. Site-scoped dorks
   against LinkedIn, Facebook and public-record aggregators are how name-based
   OSINT is actually done.

Everything found this way is flagged ``possible``, never ``confirmed`` - a hit
on "janedoe" is not proof it is *your* Jane Doe.
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote_plus

from .context import ScanContext
from .models import ERROR, INFO, PIVOT, Finding, Section
from .query import normalize, split_name, username_candidates
from .sweep import sweep_handles


def _analysis_section(raw_name: str, candidates: List[str]) -> Section:
    first, middles, last = split_name(raw_name)
    section = Section("Name breakdown")
    section.add(Finding(
        title=raw_name,
        detail="Parsed into its parts to build candidate handles and search queries.",
        confidence=INFO,
        source="parser",
        attributes={
            "First name": first or "-",
            "Middle name(s)": " ".join(middles) or "-",
            "Surname": last or "-",
            "Candidate handles": ", ".join(candidates) or "-",
        },
    ))
    return section


def _pivot_section(raw_name: str) -> Section:
    exact = quote_plus(f'"{raw_name}"')
    plain = quote_plus(raw_name)
    section = Section("Manual follow-up")

    entries = [
        ("Google - exact name", f"https://www.google.com/search?q={exact}"),
        ("Bing - exact name", f"https://www.bing.com/search?q={exact}"),
        ("DuckDuckGo - exact name", f"https://duckduckgo.com/?q={exact}"),
        ("LinkedIn profiles", f"https://www.google.com/search?q={exact}+site%3Alinkedin.com%2Fin"),
        ("Facebook profiles", f"https://www.facebook.com/search/people/?q={plain}"),
        ("Instagram", f"https://www.google.com/search?q={exact}+site%3Ainstagram.com"),
        ("X / Twitter", f"https://twitter.com/search?q={exact}&f=user"),
        ("GitHub users", f"https://github.com/search?q={plain}&type=users"),
        ("Images of the person", f"https://www.google.com/search?q={exact}&tbm=isch"),
        ("News mentions", f"https://news.google.com/search?q={exact}"),
        ("Company / staff pages",
         f"https://www.google.com/search?q={exact}+(%22about+us%22+OR+%22our+team%22+OR+staff)"),
        ("Documents naming the person",
         f"https://www.google.com/search?q={exact}+(filetype%3Apdf+OR+filetype%3Adocx+OR+filetype%3Axlsx)"),
        ("Academic / research output", f"https://scholar.google.com/scholar?q={exact}"),
        ("Court and public records (US)",
         f"https://www.google.com/search?q={exact}+(site%3Aunicourt.com+OR+site%3Acourtlistener.com)"),
        ("Company officer records (UK)",
         f"https://find-and-update.company-information.service.gov.uk/search/officers?q={plain}"),
        ("Archived pages", f"https://web.archive.org/web/*/{plain}"),
    ]
    for label, url in entries:
        section.add(Finding(
            title=label, url=url,
            detail="Open in a browser to continue by hand.",
            confidence=PIVOT, source="pivot",
        ))

    section.notes.append(
        "A name alone rarely identifies one person. Pair it with a city, employer "
        "or school in these queries to cut the noise down."
    )
    return section


def investigate(raw_name: str, ctx: ScanContext) -> List[Section]:
    raw_name = normalize(raw_name)
    limit = int(ctx.settings.get("name_candidates"))
    candidates = username_candidates(raw_name, limit=limit)

    sections: List[Section] = [_analysis_section(raw_name, candidates)]

    if not candidates:
        sections[0].add(Finding(
            title="No usable handles could be derived",
            detail="The name contains no letters that survive conversion to a username.",
            confidence=ERROR,
            source="parser",
        ))
    else:
        sections.extend(sweep_handles(
            candidates,
            ctx,
            full_scan_first=True,
            label="Possible accounts (handles derived from the name)",
            derived=True,
        ))

    sections.append(_pivot_section(raw_name))
    return sections
