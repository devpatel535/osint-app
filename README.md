# OSINT Lookup

A Windows desktop application for public-footprint research. Type a **username,
email address, phone number or real name**, and it checks that subject against
260+ public sites, reads whatever the found profiles publish about themselves,
and presents the result in a window you can save as a `.txt` report.

Built on the site database from
[Tookie-OSINT](https://github.com/Alfredredbird/tookie-osint) (MIT), with the
scanning engine, enrichment, history store and GUI written for this project.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## What it does

| You type | What happens |
|---|---|
| `alfredredbird` | Sweeps 243 sites (260+ with adult sites enabled) for that handle, and pulls the display name, bio and avatar off every profile it finds. |
| `jane.doe@example.com` | Gravatar profile (real name, location, and **the owner's own list of linked accounts**), MX/DNS checks, free vs. corporate vs. burner domain, role-address detection, breach exposure, plus a site sweep of handles derived from the local part. |
| `+44 20 7946 0958` | Validity, country, region, line type (mobile/landline/VoIP), assigned carrier and time zone, every written form of the number, and targeted searches for where it has been published. |
| `Jane Doe` | Builds the handles that person plausibly uses (`janedoe`, `jane.doe`, `jdoe`, …), sweeps them, and emits site-scoped searches for LinkedIn, Facebook, company pages, documents, court records and news. |

Every result window offers a checkbox:

> ☑ **Save these results to a .txt file when I close this window**

Ticked, the report is written **when you close the window** — not before.
Unticked, nothing is ever written. That holds for every way of closing the
window: the X button, the Close button, Escape, or quitting the app with the
window still open.

All searches are recorded in a **search history** that works like Instagram's:
newest first, click a row to run it again, `×` on a row to remove just that one,
**Clear all** to wipe the lot. **The history is never trimmed.** There is no row
cap and no age-based pruning anywhere in the code — a search from three years
ago is still there, and you reach it by scrolling. The list pages in more rows
as you scroll, so it stays fast whether you have fifty entries or fifty
thousand.

---

## Running it

### Option A — download and run (nothing to install, recommended)

**[⬇ Download OSINT-Lookup.exe](https://github.com/devpatel535/osint-app/releases/latest/download/OSINT-Lookup.exe)**

Double-click it. That is the whole process — no Python, no installer, no
dependencies. Everything, including the site catalogue, is inside the one file.

Every push to `main` rebuilds it on a Windows runner and republishes it at that
same link, so it is always current. `SHA256SUMS.txt` on the
[releases page](https://github.com/devpatel535/osint-app/releases/latest) lets
you verify the download.

> **First run:** Windows SmartScreen may warn, because the binary is not
> code-signed (signing requires a paid certificate). Choose
> **More info → Run anyway**. Some antivirus engines also look twice at any
> unsigned OSINT tool; UPX packing is deliberately disabled in the build to
> avoid the most common false-positive trigger.

### Option B — build the `.exe` yourself

```
git clone https://github.com/devpatel535/osint-app.git
cd osint-app
build_windows.bat
```

The result is **`dist\OSINT-Lookup.exe`**. `build_windows.bat` installs the
dependencies and PyInstaller for you; you need
[Python 3.9+](https://www.python.org/downloads/) installed to *build*, with
"Add python.exe to PATH" ticked during setup.

### Option C — run from source

```
install_requirements.bat     (once)
run.bat
```

`run.bat` uses `pythonw.exe` when available, so no console window sits behind
the app.

### Linux / macOS

```
pip install -r requirements.txt
python3 run.py
```

On Linux you also need Tk: `sudo apt install python3-tk`.

---

## Dependencies

Only `requests` is really needed, and even that is optional — the app falls
back to `urllib` from the standard library if it is missing.

| Package | Without it |
|---|---|
| `requests` | Works, via a slower `urllib` fallback. |
| `phonenumbers` | Phone searches lose validity, line type, carrier and region; you still get the country from the prefix. |
| `dnspython` | Email searches lose the MX/A record checks. |

Whenever one is missing the app says so in the results, under **Notes** —
it never silently degrades.

---

## How the detection works

Upstream Tookie's fast path decides "this account exists" from the HTTP status
code alone, and only inspects the site's error text when you opt into its
Selenium scraper. A large share of sites answer **HTTP 200 with a rendered
"user not found" page**, so status-only checking produces false positives.

This app keeps the plain-HTTP speed and adds the body check, giving four
signals per probe and no browser or ChromeDriver anywhere:

| Observation | Verdict |
|---|---|
| Status ≥ 400 | No account |
| Status 2xx/3xx **and** the site's recorded "not found" text is in the page | No account (soft 404) |
| Status 2xx/3xx **and** it redirected to the site homepage | No account |
| Status 2xx/3xx **and** it redirected to a login wall | Unverifiable — flagged for manual review |
| Status 401/403/405/429 | Site refused to answer — flagged, not counted as absence |
| Otherwise | **Account found** |

Matching the error text is done on the page with script and style blocks
stripped (a site's JS bundle often contains its own 404 string), across
HTML-entity and curly-quote variants, and with a punctuation-free second pass
so a message broken up by inline markup still matches.

Findings are graded so you can tell evidence from inference:

- **Confirmed** — verified by reading the page, not just the status code
- **Likely** — the site answered positively but had no signature to verify against
- **Possible** — derived from your query (e.g. a handle guessed from a name); confirm the identity yourself
- **Manual check** — a link for you to follow
- **Check failed** — the probe could not be completed

---

## Where things are stored

| What | Windows | Linux/macOS |
|---|---|---|
| History + settings | `%APPDATA%\OSINT Lookup\` | `~/.local/share/OSINT Lookup/` |
| Saved `.txt` reports | `Documents\OSINT Lookup Reports\` | `~/OSINT Lookup Reports/` |

The reports folder is configurable in **File → Settings**, or per-window with
**Change folder…**. History lives in SQLite (`history.db`) and stores a full
snapshot of each result, so **View saved** on an old row shows exactly what was
found at the time rather than silently re-running the search.

---

## Performance

A sweep is network-bound, not CPU-bound, so the tuning is all about not waiting:

- **Concurrency adapts to your machine.** The default (`0` = auto) resolves to
  `cpu_count x 4`, clamped to 16–64. An 8-core desktop runs 32 probes at once;
  a 16-core one runs 64. Override it in Settings if you want.
- **Error pages are never downloaded.** Any 4xx/5xx is decided by the status
  code alone, so the body is not streamed. Most of a sweep's responses are
  "no account", and those pages are frequently 100 KB+. Measured over 200
  probes against a 290 KB error page: **50 MB of transfer eliminated entirely.**
- **Connect and read timeouts are budgeted separately.** A dead or firewalled
  host fails its handshake in 4s instead of consuming the full read budget,
  which is what otherwise drags out the tail of a scan.
- **The connection pool is sized to the worker count.** urllib3's default
  caches 10 host pools; a sweep touches 243 distinct hosts, so the default
  thrashes. This mostly helps name and email searches, which pass over the same
  site list several times.

## Settings

**File → Settings**

- **Concurrent requests** (default `0` = auto-match your machine) — higher is faster but more conspicuous
- **Timeout per site** (default 8s)
- **Handles per name search** (default 4)
- **Include adult (NSFW) sites** — off by default; they are in the catalogue
- **Read profile details** — pull display name/bio/avatar off hits
- **Verify TLS certificates** — leave on unless you are using an intercepting proxy
- **Proxy URL** — e.g. `http://127.0.0.1:8080`
- **HaveIBeenPwned API key** — optional and paid. Without one, email searches link to the manual HIBP page instead of pretending to have checked.

---

## Tests

```
python -m unittest discover -s tests -t .
```

69 tests, run on Windows in CI on every push as well as locally. The scanner
ones run against a local mock HTTP server that reproduces every response
pattern the classifier has to handle — real profile, soft 404, hard 404,
homepage bounce, login wall, rate limit, dead host — so they do not depend on
anyone else's uptime. The GUI tests drive the real Tk widgets; they need a
display and skip automatically without one.

---

## Scope and responsible use

This tool reads **public** information: whether a public profile URL resolves,
and the metadata a page serves to any visitor. It does not break into anything,
bypass any login, or reach private data. It cannot produce things like home
addresses or government identifiers, and it does not try.

Automated checks produce false positives. A handle matching on a site is not
proof that your subject owns it — verify before relying on anything here.

You are responsible for using this lawfully: research only people and accounts
you have a legitimate reason to research, and follow the rules that apply where
you are.

## Licence

MIT — see [LICENSE](LICENSE). The bundled site database is MIT-licensed from
Tookie-OSINT; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
