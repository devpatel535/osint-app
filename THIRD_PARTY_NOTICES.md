# Third-party notices

## Tookie-OSINT

This application is built on the site database published by
[Tookie-OSINT](https://github.com/Alfredredbird/tookie-osint) by Alfredredbird,
used under the MIT License.

Vendored files:

| File in this repo                  | Upstream path        | Used for |
|------------------------------------|----------------------|----------|
| `osintapp/data/sites.json`         | `sites/sites.json`   | The 260+ site probe list: URL prefix, NSFW flag, and the "profile does not exist" error string for each site. |
| `osintapp/data/profile_fields.json`| `sites/fields.json`  | Per-domain profile field definitions. Only the **field names** are used here, to tell the analyst which extra attributes a platform publishes on a hit (`about.me` exposes location and linked socials, `7cups.com` exposes rank and last-active). The XPath/CSS selectors are not used, because this app does not drive a browser. |
| `osintapp/data/TOOKIE-LICENSE.txt` | `LICENSE`            | Upstream licence text, shipped with the binary. |

The upstream licence is reproduced in `osintapp/data/TOOKIE-LICENSE.txt` and is
displayed in the application under **Help -> About**.

### What was changed

The scanning logic here is a rewrite rather than a copy of upstream's
`modules/modules.py`:

* Upstream decides "found" from the HTTP status code alone, and only uses
  `errorMessage` in its optional Selenium web-scraper. This app checks the
  response **body** against `errorMessage` on every probe using plain HTTP, so
  soft-404s (a 200 response that renders a "user not found" page) are caught
  without needing a browser or ChromeDriver.
* Redirect-to-homepage detection was added as a second soft-404 signal.
* Profile enrichment (`<title>`, Open Graph tags) is done by regex over the
  already-downloaded body instead of by driving Selenium. Where upstream's
  `fields.json` records further attributes for a domain, those field names are
  listed on the finding so the analyst knows which profiles reward a manual
  look - the selectors themselves are unused, since there is no browser.
* Email, phone, and real-name lookups, the history store, the report writer,
  and the entire GUI are original to this project.
