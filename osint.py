#!/usr/bin/env python3
"""OSINT Lookup - command line interface.

The GUI needs a display. This does not, so it runs anywhere Python does:
a headless server, WSL, a Raspberry Pi, or iSH on an iPhone. It drives exactly
the same engine as the desktop app and writes the same .txt report.

    python3 osint.py alfredredbird
    python3 osint.py "jane.doe@example.com" --save
    python3 osint.py "+44 20 7946 0958"
    python3 osint.py "Jane Doe" --type name --threads 8
    python3 osint.py --history 20
    python3 osint.py --clear-history

Nothing needs installing. requests, phonenumbers and dnspython make it better
but are all optional - without them it falls back to the standard library and
says so in the output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import textwrap
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osintapp import APP_NAME, __version__
from osintapp.core import report
from osintapp.core.engine import run_search
from osintapp.core.history import SearchHistory
from osintapp.core.models import CONFIRMED, ERROR, LIKELY, PIVOT, POSSIBLE
from osintapp.core.query import TYPE_LABELS, detect_type
from osintapp.core.settings import Settings
from osintapp.core.timefmt import absolute, relative_age

# Terminal colour, switched off when stdout is redirected or the terminal
# cannot handle it (iSH under some setups reports no TTY).
class C:
    enabled = sys.stdout.isatty()

    @classmethod
    def _w(cls, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls.enabled else text

    @classmethod
    def green(cls, t): return cls._w("32", t)
    @classmethod
    def cyan(cls, t): return cls._w("36", t)
    @classmethod
    def yellow(cls, t): return cls._w("33", t)
    @classmethod
    def red(cls, t): return cls._w("31", t)
    @classmethod
    def grey(cls, t): return cls._w("90", t)
    @classmethod
    def bold(cls, t): return cls._w("1", t)


def terminal_width(default: int = 80) -> int:
    try:
        return max(48, min(120, shutil.get_terminal_size((default, 24)).columns))
    except Exception:  # noqa: BLE001
        return default


def wrap(text: str, indent: int) -> list:
    """Wrap *text* to the terminal, indented. Empty text yields no lines."""
    text = (text or "").strip()
    if not text:
        return []
    pad = " " * indent
    return textwrap.wrap(text, width=terminal_width(), initial_indent=pad,
                         subsequent_indent=pad, break_long_words=False,
                         break_on_hyphens=False) or [pad + text]


MARKERS = {
    CONFIRMED: ("+", C.green),
    LIKELY: ("~", C.cyan),
    POSSIBLE: ("?", C.yellow),
    PIVOT: (">", C.grey),
    ERROR: ("!", C.red),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osint.py",
        description=f"{APP_NAME} v{__version__} - search public sources for a "
                    "username, email address, phone number or real name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python3 osint.py alfredredbird
  python3 osint.py "jane.doe@example.com" --save
  python3 osint.py "+44 20 7946 0958"
  python3 osint.py "Jane Doe" --type name --threads 8
  python3 osint.py --history 20
  python3 osint.py --clear-history

Searches are recorded in the same history database the desktop app uses, and
that history is never trimmed.""",
    )
    parser.add_argument("query", nargs="?", help="what to search for")
    parser.add_argument("-t", "--type", dest="qtype",
                        choices=["auto", "username", "email", "phone", "name"],
                        default="auto", help="override the detected query type")

    out = parser.add_argument_group("output")
    out.add_argument("-s", "--save", action="store_true",
                     help="write the full report to a .txt file")
    out.add_argument("-o", "--out", metavar="DIR",
                     help="folder for the saved report (default: your reports folder)")
    out.add_argument("--json", action="store_true",
                     help="print the result as JSON instead of text")
    out.add_argument("-a", "--all", action="store_true",
                     help="show every finding, including follow-up links")
    out.add_argument("-q", "--quiet", action="store_true",
                     help="no progress output")

    scan = parser.add_argument_group("scanning")
    scan.add_argument("--threads", type=int, metavar="N",
                      help="concurrent requests (default: matches this machine)")
    scan.add_argument("--timeout", type=float, metavar="SEC",
                      help="seconds to wait per site (default 8)")
    scan.add_argument("--nsfw", action="store_true",
                      help="include adult sites from the catalogue")
    scan.add_argument("--no-details", action="store_true",
                      help="skip reading display name/bio from hits (faster)")
    scan.add_argument("--proxy", metavar="URL", help="e.g. http://127.0.0.1:8080")

    hist = parser.add_argument_group("history")
    hist.add_argument("--history", nargs="?", type=int, const=25, metavar="N",
                      help="list the N most recent searches (default 25)")
    hist.add_argument("--clear-history", action="store_true",
                      help="delete the entire search history")
    hist.add_argument("--no-record", action="store_true",
                      help="do not add this search to the history")
    return parser


def show_history(limit: int) -> int:
    with SearchHistory() as history:
        total = history.count()
        if not total:
            print("No searches recorded yet.")
            return 0

        print(C.bold(f"{APP_NAME} - search history")
              + C.grey(f"   ({total} total, never trimmed)"))
        print()
        for entry in history.page(0, limit):
            kind = TYPE_LABELS.get(entry.query_type, entry.query_type)
            found = f"{entry.hit_count} found" if entry.hit_count else "nothing found"
            print(f"  {C.bold(entry.query)}")
            print(C.grey(f"      {kind}  ·  {relative_age(entry.created_at)}"
                         f"  ·  {absolute(entry.created_at)}  ·  {found}"))
        if total > limit:
            print()
            print(C.grey(f"  ... {total - limit} older searches. "
                         f"Use --history {total} to see them all."))
        return 0


def clear_history() -> int:
    with SearchHistory() as history:
        total = history.count()
        if not total:
            print("History is already empty.")
            return 0
        answer = input(
            f"Delete all {total} searches from your history? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 1
        removed = history.clear()
    print(f"Removed {removed} searches.")
    return 0


def print_result(result, show_all: bool) -> None:
    print()
    print(C.bold(f"  {result.query}"))
    print(C.grey(f"  {TYPE_LABELS.get(result.query_type, result.query_type)}"
                 f"  ·  {result.duration:.1f}s"
                 f"  ·  {result.total_findings} findings"
                 f"  ·  {result.hit_count} accounts/records"))
    if result.cancelled:
        print(C.yellow("  STOPPED EARLY - these results are incomplete"))
    print()

    for section in result.sections:
        findings = section.sorted_findings()
        if not show_all:
            # Follow-up links are useful in the report but noise on a terminal.
            findings = [f for f in findings if f.confidence != PIVOT]
        if not findings:
            continue

        print(C.bold(f"  {section.name}"))
        for finding in findings:
            symbol, paint = MARKERS.get(finding.confidence, ("i", C.grey))
            print(f"    [{paint(symbol)}] {finding.title}")
            if finding.url:
                print(C.grey(f"        {finding.url}"))
            # The detail line carries the actual explanation - why a site
            # counts as a hit, or which package is missing. Dropping it would
            # leave findings like "Reduced capability" meaningless.
            for line in wrap(finding.detail, 8):
                print(C.grey(line))
            for key, value in finding.attributes.items():
                if value:
                    for line in wrap(f"{key}: {value}", 8):
                        print(C.grey(line))
        for note in section.notes:
            for line in wrap(f"note: {note}", 4):
                print(C.grey(line))
        print()

    if not show_all:
        pivots = sum(1 for s in result.sections for f in s.findings if f.confidence == PIVOT)
        if pivots:
            print(C.grey(f"  ({pivots} follow-up links hidden - use --all to show them, "
                         "or --save to get them in the report)"))
            print()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.clear_history:
        return clear_history()
    if args.history is not None:
        return show_history(args.history)
    if not args.query:
        parser.print_help()
        return 1

    settings = Settings()
    if args.threads is not None:
        settings.set("threads", args.threads)
    if args.timeout is not None:
        settings.set("timeout", args.timeout)
    if args.nsfw:
        settings.set("include_nsfw", True)
    if args.no_details:
        settings.set("fetch_profile_details", False)
    if args.proxy:
        settings.set("proxy", args.proxy)

    forced = None if args.qtype == "auto" else args.qtype
    detected = forced or detect_type(args.query)

    if not args.quiet:
        print(C.grey(f"Searching as {TYPE_LABELS.get(detected, detected)} ..."), file=sys.stderr)

    cancel = threading.Event()

    def on_sigint(_sig, _frame):
        if not cancel.is_set():
            cancel.set()
            print(C.yellow("\nStopping - finishing the checks already in flight..."),
                  file=sys.stderr)

    signal.signal(signal.SIGINT, on_sigint)

    last = [0.0]

    # Progress is drawn with carriage returns, which only make sense on a real
    # terminal. Piped or redirected, it would just pollute the stream.
    show_progress = not args.quiet and sys.stderr.isatty()

    def progress(done: int, total: int, message: str) -> None:
        if not show_progress or not total:
            return
        now = time.time()
        # Throttle: iSH redraws a terminal slowly, and one line per probe would
        # cost more time than the probes themselves.
        if now - last[0] < 0.2 and done < total:
            return
        last[0] = now
        pct = done * 100 // total
        bar_len = 24
        filled = bar_len * done // total
        bar = "#" * filled + "-" * (bar_len - filled)
        line = f"  [{bar}] {pct:3d}%  {done}/{total}  {message[:38]}"
        print(f"\r{line:<86}", end="", file=sys.stderr, flush=True)

    result = run_search(args.query, forced_type=forced, settings=settings,
                        cancel=cancel, on_progress=progress)

    if show_progress:
        print("\r" + " " * 88 + "\r", end="", file=sys.stderr, flush=True)

    if not args.no_record:
        try:
            with SearchHistory() as history:
                history.add(result)
        except Exception as exc:  # noqa: BLE001 - a history failure must not lose the result
            print(C.yellow(f"  (could not record this search in history: {exc})"),
                  file=sys.stderr)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print_result(result, args.all)

    if args.save:
        folder = Path(args.out).expanduser() if args.out else settings.reports_dir()
        try:
            path = report.save(result, folder)
            print(C.green(f"  Report saved to {path}"))
        except OSError as exc:
            print(C.red(f"  Could not write the report: {exc}"), file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
