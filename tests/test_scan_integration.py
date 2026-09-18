"""End-to-end scanner tests against a local server.

The site catalogue points at 260 real services, which cannot be used as a test
fixture - they change, they rate-limit, and a test suite must not depend on
someone else's uptime. Instead this spins up a throwaway HTTP server that
reproduces every response pattern the classifier has to handle, including the
two that plain status-code checking gets wrong.
"""

from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osintapp.core.net import Fetcher  # noqa: E402
from osintapp.core.sites import Site  # noqa: E402
from osintapp.core.username_scan import probe_site, scan_username  # noqa: E402

NOT_FOUND_TEXT = "This account doesn’t exist"

PROFILE_PAGE = (
    "<html><head><title>Jane Doe (@{user}) / Demo</title>"
    '<meta property="og:title" content="Jane Doe">'
    '<meta property="og:description" content="Designer &amp; photographer">'
    "</head><body><h1>Jane Doe</h1></body></html>"
)

# HTTP 200 carrying a "no such user" page - the case a status-code-only
# scanner reports as a false positive.
SOFT_404_PAGE = (
    "<html><head><title>Not found</title></head><body>"
    "<div class=\"err\">This account doesn&#39;t exist</div></body></html>"
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # keep test output clean
        pass

    def _send(self, code: int, body: str = "", headers=None):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's API
        path = self.path

        if path.startswith("/good/"):
            self._send(200, PROFILE_PAGE.format(user=path.rsplit("/", 1)[-1]))
        elif path.startswith("/soft404/"):
            self._send(200, SOFT_404_PAGE)
        elif path.startswith("/hard404/"):
            self._send(404, "<html><body>Not Found</body></html>")
        elif path.startswith("/bounce/"):
            self._send(302, "", {"Location": "/"})
        elif path.startswith("/login/"):
            self._send(302, "", {"Location": "/accounts/login/?next=" + path})
        elif path.startswith("/blocked/"):
            self._send(403, "<html><body>Forbidden</body></html>")
        elif path.startswith("/ratelimited/"):
            self._send(429, "<html><body>Slow down</body></html>")
        elif path.startswith("/accounts/login/"):
            self._send(200, "<html><body>Please sign in</body></html>")
        elif path == "/":
            self._send(200, "<html><body>Homepage</body></html>")
        else:
            self._send(404, "Not Found")


class ScannerTestCase(unittest.TestCase):
    server = None
    thread = None
    base = ""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # An ambient HTTPS_PROXY must not be applied to 127.0.0.1.
        self.fetcher = Fetcher(timeout=10)
        if self.fetcher._session is not None:
            self.fetcher._session.trust_env = False

    def tearDown(self):
        self.fetcher.close()

    def _site(self, path: str, error_message: str = NOT_FOUND_TEXT) -> Site:
        return Site(url_prefix=f"{self.base}{path}", nsfw=False,
                    error_message=error_message, domain="127.0.0.1")

    # -- the cases ------------------------------------------------------
    def test_real_profile_is_found_and_enriched(self):
        hit = probe_site(self._site("/good/"), "janedoe", self.fetcher)
        self.assertTrue(hit.found)
        self.assertTrue(hit.verified)
        self.assertEqual(hit.status, 200)
        self.assertEqual(hit.details.get("Display name"), "Jane Doe")
        self.assertEqual(hit.details.get("Bio"), "Designer & photographer")

    def test_soft_404_is_rejected(self):
        """HTTP 200 + 'no such user' page must NOT count as found."""
        hit = probe_site(self._site("/soft404/"), "janedoe", self.fetcher)
        self.assertFalse(hit.found)
        self.assertTrue(hit.verified)
        self.assertIn("not found", hit.reason)

    def test_soft_404_would_fool_a_status_only_scanner(self):
        # Guards the premise: the same URL really does answer 200.
        response = self.fetcher.get(f"{self.base}/soft404/janedoe")
        self.assertEqual(response.status, 200)

    def test_hard_404(self):
        hit = probe_site(self._site("/hard404/"), "janedoe", self.fetcher)
        self.assertFalse(hit.found)
        self.assertEqual(hit.status, 404)

    def test_homepage_redirect_is_not_a_hit(self):
        hit = probe_site(self._site("/bounce/"), "janedoe", self.fetcher)
        self.assertFalse(hit.found)
        self.assertIn("homepage", hit.reason)

    def test_login_wall_is_flagged_for_review(self):
        hit = probe_site(self._site("/login/"), "janedoe", self.fetcher)
        self.assertFalse(hit.found)
        self.assertTrue(hit.needs_review)

    def test_blocked_and_ratelimited_are_flagged_not_denied(self):
        for path in ("/blocked/", "/ratelimited/"):
            with self.subTest(path=path):
                hit = probe_site(self._site(path), "janedoe", self.fetcher)
                self.assertTrue(hit.needs_review)
                self.assertFalse(hit.found)

    def test_site_without_error_signature_is_only_likely(self):
        hit = probe_site(self._site("/good/", error_message=""), "janedoe", self.fetcher)
        self.assertTrue(hit.found)
        self.assertFalse(hit.verified)  # nothing to verify against

    def test_unreachable_host_reports_an_error(self):
        site = Site(url_prefix="http://127.0.0.1:1/", nsfw=False,
                    error_message="", domain="dead")
        hit = probe_site(site, "janedoe", self.fetcher)
        self.assertTrue(hit.error)
        self.assertFalse(hit.found)

    def test_concurrent_sweep_classifies_everything(self):
        catalogue = (
            [self._site("/good/") for _ in range(8)]
            + [self._site("/soft404/") for _ in range(8)]
            + [self._site("/hard404/") for _ in range(4)]
        )
        hits = scan_username("janedoe", catalogue, self.fetcher, threads=8)
        self.assertEqual(len(hits), 20)
        self.assertEqual(sum(1 for h in hits if h.found), 8)

    def test_cancellation_stops_early(self):
        cancel = threading.Event()
        cancel.set()  # cancelled before it starts
        hits = scan_username("janedoe", [self._site("/good/")] * 50,
                             self.fetcher, threads=4, cancel=cancel)
        self.assertLess(len(hits), 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
