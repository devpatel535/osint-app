# Third-party notices

## Tookie-OSINT

This application is built on the site database published by
[Tookie-OSINT](https://github.com/Alfredredbird/tookie-osint) by Alfredredbird,
used under the MIT License.

Vendored files:

| File in this repo                  | Upstream path        | Used for |
|------------------------------------|----------------------|----------|
| `osintapp/data/sites.json`         | `sites/sites.json`   | The 260+ site probe list: URL prefix, NSFW flag, and the "profile does not exist" error string for each site. |
| `osintapp/data/profile_fields.json`| `sites/fields.json`  | Per-domain field selectors, used here as a hint list of which profile attributes are worth extracting. |
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
  already-downloaded body instead of by driving Selenium.
* Email, phone, and real-name lookups, the history store, the report writer,
  and the entire GUI are original to this project.
