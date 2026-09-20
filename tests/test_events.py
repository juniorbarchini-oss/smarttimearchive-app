import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sta import cli, core  # noqa: E402
from test_core import Fixture  # noqa: E402


class TestEvents(Fixture):
    def test_scan_and_extract_emit_structured_events(self):
        events = []
        conn, _ = core.scan(
            self.source, self.dates, core.Options(), os.path.join(self.tmp, "e.db"),
            log=lambda *_: None, use_cache=False, emit=events.append,
        )  # fmt: skip
        ext = core.Extractor(self.source, self.dst, conn, self.dates, log=lambda *_: None,
                             emit=events.append)  # fmt: skip
        ext.run()
        conn.close()
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds.count("scan_snapshot"), 3)
        self.assertEqual(kinds.count("extract_start"), 3)
        self.assertEqual(kinds.count("date_done"), 3)
        done = [e for e in events if e["event"] == "date_done"]
        self.assertEqual([e["date"] for e in done], self.dates)
        self.assertEqual(done[1]["linked"], 2)
        self.assertTrue(all(e["i"] <= e["n"] == 3 for e in done))

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_json_mode_is_pure_json_lines(self):
        code, text = self.run_cli("extract", self.src, self.dst, "--source-type", "dir", "--json")
        self.assertEqual(code, 0)
        events = [json.loads(line) for line in text.splitlines()]  # every line must parse
        kinds = [e["event"] for e in events]
        for k in ("scan_needed", "plan", "extract_start", "date_done", "finished"):
            self.assertIn(k, kinds)
        self.assertEqual(events[-1]["event"], "finished")
        self.assertEqual(events[-1]["status"], core.COMPLETED)
        plan = next(e for e in events if e["event"] == "plan")
        self.assertIn("bytes_with_links", plan)

    def test_json_mode_reports_errors_as_events(self):
        real = core.make_plan
        core.make_plan = lambda *a, **k: {**real(*a, **k), "fits": False}
        try:
            code, text = self.run_cli(
                "extract", self.src, self.dst, "--source-type", "dir", "--json"
            )
        finally:
            core.make_plan = real
        self.assertEqual(code, 2)
        last = json.loads(text.splitlines()[-1])
        self.assertEqual(last["event"], "error")
        self.assertIn("free space", last["message"])

    def test_text_mode_is_unchanged(self):
        code, text = self.run_cli("plan", self.src, self.dst, "--source-type", "dir")
        self.assertIn("Size WITH hard links", text)
        self.assertNotIn('{"event"', text)


if __name__ == "__main__":
    unittest.main()
