"""Core logic tests. No network, no GUI - these run anywhere.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osintapp.core import query, naming, sites, username_scan  # noqa: E402
from osintapp.core.history import SearchHistory  # noqa: E402
from osintapp.core.models import CONFIRMED, Finding, SearchResult  # noqa: E402
from osintapp.core.report import render, save  # noqa: E402
from osintapp.core.settings import Settings  # noqa: E402


class TestQueryDetection(unittest.TestCase):
    def test_detects_each_type(self):
        cases = {
            "jane.doe@example.com": query.EMAIL,
            "user+tag@sub.example.co.uk": query.EMAIL,
            "+1 (415) 555-0132": query.PHONE,
            "4155550132": query.PHONE,
            "00442079460958": query.PHONE,
            "Jane Doe": query.NAME,
            "Jean-Luc van der Berg": query.NAME,
            "alfredredbird": query.USERNAME,
            "abc-123": query.USERNAME,
            "": query.USERNAME,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(query.detect_type(text), expected)

    def test_handle_with_digits_is_not_a_phone(self):
        # The classic false positive: a handle that is mostly digits.
        self.assertEqual(query.detect_type("user2024"), query.USERNAME)
        self.assertEqual(query.detect_type("x1234567"), query.USERNAME)

    def test_surname_particles_stay_with_the_surname(self):
        self.assertEqual(query.split_name("Jean-Luc van der Berg"),
                         ("Jean-Luc", [], "van der Berg"))

    def test_candidates_are_ordered_and_unique(self):
        candidates = query.username_candidates("Jane Doe")
        self.assertEqual(candidates[0], "janedoe")
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertTrue(all(len(c) >= 3 for c in candidates))

    def test_accents_are_folded(self):
        self.assertIn("anagonzalez", query.username_candidates("Ana Gonzalez"))
        self.assertIn("anagonzalez", query.username_candidates("Ana Gonzálvez".replace("v", "")))

    def test_email_derived_handles(self):
        self.assertEqual(
            query.usernames_from_email("jane.doe+shopping@example.com"),
            ["jane.doe", "janedoe", "jane"],
        )


class TestFilenameSafety(unittest.TestCase):
    def test_path_traversal_is_neutralised(self):
        for hostile in ("../../../etc/passwd", r"..\..\Windows\System32", "x/../../y"):
            stem = naming.safe_stem(hostile)
            with self.subTest(hostile=hostile):
                self.assertNotIn("..", stem)
                self.assertNotIn("/", stem)
                self.assertNotIn("\\", stem)

    def test_windows_reserved_names_are_escaped(self):
        for reserved in ("CON", "nul.txt", "COM1", "LPT9", "aux"):
            with self.subTest(reserved=reserved):
                stem = naming.safe_stem(reserved)
                self.assertNotEqual(stem.split(".")[0].upper(), reserved.split(".")[0].upper())

    def test_no_trailing_dot_or_space(self):
        for text in ("file.", "file ", "file. . "):
            with self.subTest(text=text):
                stem = naming.safe_stem(text)
                self.assertFalse(stem.endswith((".", " ")))

    def test_non_latin_queries_stay_distinct(self):
        self.assertNotEqual(naming.safe_stem("李明"), naming.safe_stem("田中"))

    def test_empty_falls_back(self):
        self.assertEqual(naming.safe_stem(""), "search")


class TestSiteCatalogue(unittest.TestCase):
    def test_catalogue_loads(self):
        catalogue = sites.load_sites()
        self.assertGreater(len(catalogue), 200)
        self.assertTrue(all(s.url_prefix.startswith("http") for s in catalogue))

    def test_nsfw_filtering(self):
        self.assertLess(len(sites.select_sites(include_nsfw=False)),
                        len(sites.select_sites(include_nsfw=True)))

    def test_probe_url_encodes_injection(self):
        site = sites.load_sites()[0]
        url = site.probe_url("a b&c=d#e?f")
        for char in (" ", "#", "?", "&"):
            self.assertNotIn(char, url[len(site.url_prefix):])


class TestSoftFourOhFourDetection(unittest.TestCase):
    """The accuracy improvement over upstream's status-code-only check."""

    def test_curly_quote_versus_html_entity(self):
        self.assertTrue(username_scan.body_contains_error(
            "<p>This account doesn&#39;t exist</p>", "This account doesn’t exist"))

    def test_message_split_by_markup(self):
        self.assertTrue(username_scan.body_contains_error(
            "that<span> </span>content is unavailable", "that content is unavailable"))

    def test_error_string_inside_script_does_not_count(self):
        # A live profile page whose JS bundle happens to contain the 404 string
        # must not be reported as missing.
        self.assertFalse(username_scan.body_contains_error(
            '<script>var m={"e":"User not found"}</script><h1>Jane</h1>', "User not found"))

    def test_absent_message(self):
        self.assertFalse(username_scan.body_contains_error(
            "<h1>Welcome Jane</h1>", "This account doesn't exist"))

    def test_homepage_bounce(self):
        self.assertTrue(username_scan._is_homepage_bounce("https://x.com/jane", "https://x.com/"))
        self.assertFalse(username_scan._is_homepage_bounce("https://x.com/jane", "https://x.com/jane"))

    def test_login_bounce(self):
        self.assertTrue(username_scan._is_login_bounce(
            "https://instagram.com/accounts/login/?next=/jane/"))

    def test_profile_detail_extraction(self):
        details = username_scan.extract_profile_details(
            '<title>Jane Doe (@janedoe)</title>'
            '<meta property="og:description" content="Designer &amp; photographer">'
            '<meta content="https://cdn/a.jpg" property="og:image">'
        )
        self.assertEqual(details["Display name"] if "Display name" in details
                         else details["Page title"], "Jane Doe (@janedoe)")
        self.assertEqual(details["Bio"], "Designer & photographer")
        self.assertEqual(details["Avatar"], "https://cdn/a.jpg")


class TestProfileFieldHints(unittest.TestCase):
    """The vendored fields.json is used for its field NAMES, not its selectors."""

    def test_known_platform_lists_extra_attributes(self):
        extras = sites.extra_profile_fields("about.me")
        self.assertIn("location", extras)
        self.assertIn("linkedin", extras)

    def test_www_prefixed_keys_are_normalised(self):
        # fields.json stores "www.artstation.com"; site domains drop the www.
        self.assertTrue(sites.extra_profile_fields("artstation.com"))

    def test_fields_we_already_read_are_not_repeated(self):
        for domain in sites.load_profile_fields():
            with self.subTest(domain=domain):
                self.assertNotIn("username", sites.extra_profile_fields(domain))
                self.assertNotIn("handle", sites.extra_profile_fields(domain))

    def test_unknown_platform_returns_nothing(self):
        self.assertEqual(sites.extra_profile_fields("no-such-site.example"), [])
        self.assertEqual(sites.extra_profile_fields(""), [])

    def test_every_key_is_lowercase_and_www_free(self):
        for domain in sites.load_profile_fields():
            with self.subTest(domain=domain):
                self.assertEqual(domain, domain.lower())
                self.assertFalse(domain.startswith("www."))


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.history = SearchHistory(Path(self.dir.name) / "h.db")

    def tearDown(self):
        self.history.close()
        self.dir.cleanup()

    def _add(self, name: str, ago_days: float = 0.0) -> int:
        result = SearchResult(query=name, query_type="username",
                              started_at=time.time() - ago_days * 86400)
        result.section("Accounts found").add(Finding(title="github.com", confidence=CONFIRMED))
        return self.history.add(result)

    def test_nothing_is_trimmed_by_age_or_count(self):
        for index in range(250):
            self._add(f"user{index}", ago_days=index * 10)  # out to ~7 years
        self.assertEqual(self.history.count(), 250)
        oldest = self.history.page(offset=249, limit=1)[0]
        self.assertEqual(oldest.query, "user249")

    def test_paging_walks_the_whole_table(self):
        for index in range(120):
            self._add(f"user{index}")
        seen, offset = [], 0
        while True:
            page = self.history.page(offset=offset, limit=25)
            if not page:
                break
            seen.extend(page)
            offset += len(page)
        self.assertEqual(len(seen), 120)
        self.assertEqual(len({e.id for e in seen}), 120)

    def test_snapshot_round_trip(self):
        entry_id = self._add("subject")
        restored = self.history.snapshot(entry_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.query, "subject")
        self.assertEqual(restored.sections[0].findings[0].title, "github.com")

    def test_filtering(self):
        self._add("alpha_one")
        self._add("beta_two")
        self._add("alpha_three")
        self.assertEqual(self.history.count("alpha"), 2)
        self.assertEqual(len(self.history.page(search="ALPHA")), 2)

    def test_delete_and_clear(self):
        first = self._add("a")
        self._add("b")
        self.history.delete(first)
        self.assertEqual(self.history.count(), 1)
        self.assertEqual(self.history.clear(), 1)
        self.assertEqual(self.history.count(), 0)


class TestReport(unittest.TestCase):
    def _result(self) -> SearchResult:
        result = SearchResult(query="janedoe", query_type="username",
                              started_at=time.time() - 5, finished_at=time.time())
        section = result.section("Accounts found")
        section.add(Finding(title="github.com", url="https://github.com/janedoe",
                            detail="account found", confidence=CONFIRMED, status=200,
                            attributes={"Display name": "Jane Doe"}))
        section.notes.append("Checked 243 sites.")
        return result

    def test_render_contains_the_essentials(self):
        text = render(self._result())
        for expected in ("janedoe", "github.com", "https://github.com/janedoe",
                         "Jane Doe", "Checked 243 sites.", "DISCLAIMER", "LEGEND"):
            self.assertIn(expected, text)

    def test_save_writes_crlf_and_returns_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = save(self._result(), Path(folder))
            self.assertTrue(path.exists())
            self.assertIn(b"\r\n", path.read_bytes())

    def test_repeat_saves_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self._result()
            first = save(result, Path(folder), "same.txt")
            second = save(result, Path(folder), "same.txt")
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists() and second.exists())


class TestSettings(unittest.TestCase):
    def test_out_of_range_values_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / "s.json")
            settings.set("threads", 99999)
            self.assertEqual(settings.get("threads"), 24)

    def test_corrupt_file_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            path.write_text("{ not json at all")
            settings = Settings(path)
            self.assertEqual(settings.get("threads"), 24)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            Settings(path).update(threads=8, include_nsfw=True)
            self.assertEqual(Settings(path).get("threads"), 8)
            self.assertTrue(Settings(path).get("include_nsfw"))


class TestModels(unittest.TestCase):
    def test_json_round_trip(self):
        result = SearchResult(query="q", query_type="username")
        result.section("S").add(Finding(title="t", url="u", attributes={"k": "v"}))
        restored = SearchResult.from_dict(json.loads(json.dumps(result.to_dict())))
        self.assertEqual(restored.sections[0].findings[0].attributes["k"], "v")

    def test_unknown_keys_in_old_snapshots_are_ignored(self):
        raw = {"title": "t", "url": "u", "some_future_field": 1}
        self.assertEqual(Finding.from_dict(raw).title, "t")


if __name__ == "__main__":
    unittest.main(verbosity=2)
