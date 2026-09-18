"""Turning raw site probes into presentable sections.

Shared by all three entry points that end up checking handles: a direct
username search, a real-name search (several candidate spellings), and an email
search (handles derived from the local part).
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from .context import ScanContext
from .models import INFO, POSSIBLE, Finding, Section
from .sites import select_sites
from .username_scan import SiteHit, scan_username

# When connectivity is broken every site fails, and listing all 243 of them
# buries the useful findings. Beyond this many, the rest are summarised.
MAX_LISTED_FAILURES = 25


def sweep_handles(
    handles: Sequence[str],
    ctx: ScanContext,
    full_scan_first: bool = True,
    label: str = "Accounts found",
    derived: bool = False,
) -> List[Section]:
    """Probe every handle and group the outcome into sections.

    ``full_scan_first`` reflects the cost trade-off for multi-handle searches:
    the best candidate is swept across the whole catalogue, while the rest only
    hit the major platforms. Checking 260 sites for six name permutations would
    be 1,500 requests for very little extra signal.
    """
    handles = [h for h in handles if h]
    if not handles:
        return []

    all_sites = select_sites(include_nsfw=ctx.include_nsfw)
    priority_sites = select_sites(include_nsfw=ctx.include_nsfw, priority_only=True)

    plan: List[tuple] = []
    for index, handle in enumerate(handles):
        sites = all_sites if (index == 0 or not full_scan_first) else priority_sites
        plan.append((handle, sites))

    grand_total = sum(len(sites) for _handle, sites in plan)
    completed = 0

    found_section = Section(label)
    review_section = Section("Needs a manual check")
    problem_section = Section("Sites that could not be checked")

    error_counts: Dict[str, int] = {}
    checked = 0

    for handle, sites in plan:
        if ctx.cancelled():
            break

        offset = completed

        def progress(done: int, _total: int, message: str, _offset=offset) -> None:
            ctx.progress(_offset + done, grand_total, message)

        hits: List[SiteHit] = scan_username(
            handle,
            sites,
            ctx.fetcher,
            threads=ctx.threads,
            fetch_details=ctx.fetch_details,
            cancel=ctx.cancel,
            on_progress=progress,
        )
        completed += len(sites)
        checked += len(hits)

        for hit in hits:
            finding = hit.to_finding()
            if len(handles) > 1 or derived:
                finding.attributes = dict(finding.attributes)
                finding.attributes["Handle checked"] = handle
            if derived:
                # A derived handle matching is evidence about the handle, not
                # proof it belongs to the person we started from.
                finding.confidence = POSSIBLE if hit.found else finding.confidence
                finding.detail = (
                    f"{finding.detail} (handle '{handle}' was derived from the query, "
                    "so confirm the profile really belongs to your subject)"
                )

            if hit.error:
                error_counts[hit.error] = error_counts.get(hit.error, 0) + 1
                if len(problem_section.findings) < MAX_LISTED_FAILURES:
                    problem_section.add(finding)
            elif hit.needs_review:
                review_section.add(finding)
            elif hit.found:
                found_section.add(finding)

    if not found_section.findings and not ctx.cancelled():
        found_section.add(Finding(
            title="No accounts found",
            detail=(
                "None of the checked sites reported a profile for "
                + ", ".join(f"'{h}'" for h in handles)
                + ". That is a real result, not an error - but a person may still "
                "use a handle that is not in the catalogue."
            ),
            confidence=INFO,
            source="sweep",
        ))

    found_section.notes.append(
        f"Checked {grand_total} site probe(s) across {len(handles)} handle(s); "
        f"{checked} returned an answer."
    )
    if len(handles) > 1 and full_scan_first:
        found_section.notes.append(
            f"'{handles[0]}' was checked against the full catalogue ({len(all_sites)} sites); "
            f"the other spellings were checked against {len(priority_sites)} major platforms."
        )

    sections = [found_section]
    if review_section.findings:
        review_section.notes.append(
            "These sites answered, but behind a login wall or a rate limit, so "
            "neither presence nor absence could be established automatically."
        )
        sections.append(review_section)
    if problem_section.findings:
        failures = sum(error_counts.values())
        top = sorted(error_counts.items(), key=lambda kv: -kv[1])[:5]
        problem_section.notes.append(
            f"{failures} probe(s) failed. Most common reasons: "
            + "; ".join(f"{reason} ({count})" for reason, count in top)
        )
        hidden = failures - len(problem_section.findings)
        if hidden > 0:
            problem_section.notes.append(
                f"Only the first {len(problem_section.findings)} are listed individually; "
                f"{hidden} more failed the same way."
            )
        if failures >= grand_total and grand_total:
            problem_section.notes.append(
                "Every probe failed. That usually means no internet connection, "
                "a proxy that is refusing requests, or a firewall - not that the "
                "subject has no accounts."
            )
        sections.append(problem_section)
    return sections
