"""Smoke tests for the command line interface.

The CLI is the only way to use this on a machine with no display, so it needs
to keep working without one - these run anywhere, GUI or not.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import osint as cli  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import os

        self._old = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "HOME", "APPDATA")}
        os.environ["XDG_DATA_HOME"] = str(Path(self.tmp.name) / "data")
        os.environ["HOME"] = self.tmp.name
        os.environ["APPDATA"] = str(Path(self.tmp.name) / "data")

    def tearDown(self):
        import os

        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, buffer.getvalue()

    # A phone lookup needs no network, so it is the one query that is
    # deterministic in a sandbox with no outbound access.
    def test_phone_search_prints_a_result(self):
        code, out = self._run(["+44 20 7946 0958", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("+44 20 7946 0958", out)
        self.assertIn("Phone number", out)

    def test_json_output_is_valid_json(self):
        code, out = self._run(["+44 20 7946 0958", "--quiet", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["query_type"], "phone")
        self.assertTrue(payload["sections"])

    def test_save_writes_a_report(self):
        folder = Path(self.tmp.name) / "reports"
        code, out = self._run(["+44 20 7946 0958", "--quiet", "--save", "--out", str(folder)])
        self.assertEqual(code, 0)
        files = list(folder.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("+44 20 7946 0958", files[0].read_text(encoding="utf-8"))

    def test_search_is_recorded_in_history(self):
        self._run(["+44 20 7946 0958", "--quiet"])
        _code, out = self._run(["--history", "5"])
        self.assertIn("+44 20 7946 0958", out)

    def test_no_record_skips_history(self):
        self._run(["+1 415 555 0132", "--quiet", "--no-record"])
        _code, out = self._run(["--history", "5"])
        self.assertNotIn("415", out)

    def test_forced_type_overrides_detection(self):
        code, out = self._run(["1234567890", "--quiet", "--type", "username"])
        self.assertEqual(code, 0)
        self.assertIn("Username", out)

    def test_no_query_prints_help(self):
        code, out = self._run([])
        self.assertEqual(code, 1)
        self.assertIn("usage:", out)

    def test_follow_up_links_hidden_until_asked_for(self):
        _c, plain = self._run(["+44 20 7946 0958", "--quiet"])
        _c, everything = self._run(["+44 20 7946 0958", "--quiet", "--all"])
        self.assertIn("follow-up links hidden", plain)
        self.assertGreater(len(everything), len(plain))
        self.assertIn("google.com", everything.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
