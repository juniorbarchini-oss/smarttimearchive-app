import contextlib
import io
import json
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sta import cli, core  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Fixture(unittest.TestCase):
    """Three fake snapshots the way Time Machine leaves them: unchanged files are
    hard links to the same inode, a modified file gets a new inode."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sta_test_")
        self.src, self.dst = os.path.join(self.tmp, "src"), os.path.join(self.tmp, "dst")
        os.makedirs(self.dst)
        home = lambda d: os.path.join(self.src, d, "Users", "bob")
        # d1
        write(home("d1") + "/Documents/keep.txt", "unchanged")
        write(home("d1") + "/Documents/edit.txt", "v1")
        write(home("d1") + "/Documents/deleted-later.txt", "gone soon")
        write(home("d1") + "/.zshrc", "hidden file")
        write(home("d1") + "/Library/Caches/junk.bin", "cache")
        os.makedirs(home("d1") + "/Empty")
        os.symlink("keep.txt", home("d1") + "/Documents/link")
        # d2: keep.txt hard-linked, edit.txt replaced (new inode)
        os.makedirs(home("d2") + "/Documents")
        os.link(home("d1") + "/Documents/keep.txt", home("d2") + "/Documents/keep.txt")
        os.link(home("d1") + "/.zshrc", home("d2") + "/.zshrc")
        write(home("d2") + "/Documents/edit.txt", "v2 longer")
        # d3: keep.txt still linked; edit.txt v3; new file
        os.makedirs(home("d3") + "/Documents")
        os.link(home("d1") + "/Documents/keep.txt", home("d3") + "/Documents/keep.txt")
        write(home("d3") + "/Documents/edit.txt", "v3!")
        write(home("d3") + "/Documents/new.txt", "new")
        self.source = core.DirSource(self.src)
        self.dates = ["d1", "d2", "d3"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_extract(self, opts=None, dates=None, cancel=lambda: False, dst=None):
        dst = dst or self.dst
        self.runs = getattr(self, "runs", 0) + 1  # fresh scan db per run, like the CLI
        conn, _ = core.scan(
            self.source,
            dates or self.dates,
            opts or core.Options(),
            os.path.join(self.tmp, f"scan{self.runs}.db"),
            log=lambda *_: None,
            use_cache=False,
        )
        ext = core.Extractor(
            self.source, dst, conn, dates or self.dates, log=lambda *_: None, cancel=cancel
        )
        try:
            return ext, ext.run()
        finally:
            conn.close()

    def path(self, date, rel):
        return os.path.join(self.dst, date, "Users", "bob", rel)


class TestExtract(Fixture):
    def test_content_per_date_and_hard_links(self):
        ext, status = self.run_extract()
        self.assertEqual(status, core.COMPLETED)
        contents = []
        for d in self.dates:
            with open(self.path(d, "Documents/edit.txt")) as f:
                contents.append(f.read())
        self.assertEqual(contents, ["v1", "v2 longer", "v3!"])
        inos = {os.lstat(self.path(d, "Documents/keep.txt")).st_ino for d in self.dates}
        self.assertEqual(len(inos), 1, "unchanged file must be one inode across dates")
        self.assertEqual(os.lstat(self.path("d1", "Documents/keep.txt")).st_nlink, 3)
        self.assertEqual(ext.report["dates"]["d2"]["linked"], 2)  # keep.txt + .zshrc

    def test_hidden_symlink_emptydir_included(self):
        self.run_extract()
        self.assertTrue(os.path.exists(self.path("d1", ".zshrc")))
        self.assertTrue(os.path.islink(self.path("d1", "Documents/link")))
        self.assertEqual(os.readlink(self.path("d1", "Documents/link")), "keep.txt")
        self.assertTrue(os.path.isdir(self.path("d1", "Empty")))

    def test_deleted_file_only_in_old_date(self):
        self.run_extract()
        self.assertTrue(os.path.exists(self.path("d1", "Documents/deleted-later.txt")))
        self.assertFalse(os.path.exists(self.path("d2", "Documents/deleted-later.txt")))

    def test_no_partial_left_and_manifest_written(self):
        self.run_extract()
        self.assertFalse([n for n in os.listdir(self.dst) if n.endswith(".partial")])
        with open(os.path.join(self.dst, "_sta", "manifests", "d1.sha256")) as f:
            man = f.read()
        self.assertIn("Users/bob/Documents/keep.txt", man)

    def test_excludes_match_components_not_substrings(self):
        self.run_extract(core.Options(excludes=["Library"]))
        self.assertFalse(os.path.exists(self.path("d1", "Library")))
        self.assertTrue(os.path.exists(self.path("d1", "Documents/keep.txt")))

    def test_folders_filter(self):
        self.run_extract(core.Options(folders=["Documents"]))
        self.assertTrue(os.path.exists(self.path("d1", "Documents")))
        self.assertFalse(os.path.exists(self.path("d1", ".zshrc")))
        self.assertFalse(os.path.exists(self.path("d1", "Library")))

    def test_source_never_modified(self):
        before = subprocess.run(
            ["find", self.src, "-type", "f", "-exec", "shasum", "{}", "+"],
            capture_output=True,
            text=True,
        ).stdout
        self.run_extract()
        after = subprocess.run(
            ["find", self.src, "-type", "f", "-exec", "shasum", "{}", "+"],
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(before, after)

    @unittest.skipIf(os.geteuid() == 0, "root can read mode 000 files")
    def test_unreadable_file_is_reported_not_hidden(self):
        bad = os.path.join(self.src, "d2", "Users", "bob", "Documents", "locked.txt")
        write(bad, "secret")
        os.chmod(bad, 0)
        try:
            ext, status = self.run_extract()
        finally:
            os.chmod(bad, 0o600)
        self.assertEqual(status, core.COMPLETED_WITH_ERRORS)
        errs = ext.report["dates"]["d2"]["errors"]
        self.assertTrue(any("locked.txt" in e[0] for e in errs))
        self.assertFalse(
            os.path.exists(self.path("d2", "Documents/locked.txt")),
            "no half-written file left behind",
        )
        self.assertTrue(
            os.path.exists(self.path("d3", "Documents/new.txt")), "keeps going after errors"
        )

    def test_cancel_leaves_partial_and_never_says_completed(self):
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 12  # stop somewhere inside the second date

        ext, status = self.run_extract(cancel=cancel)
        self.assertEqual(status, core.CANCELLED)
        self.assertTrue([n for n in os.listdir(self.dst) if n.endswith(".partial")])

    def test_rerun_skips_finished_dates_and_still_links(self):
        self.run_extract(dates=["d1", "d2"])
        ext, status = self.run_extract(dates=["d1", "d2", "d3"])
        self.assertEqual(status, core.COMPLETED)
        self.assertTrue(ext.report["dates"]["d1"]["skipped"])
        self.assertEqual(ext.report["dates"]["d3"]["linked"], 1)  # keep.txt -> earlier run's copy
        self.assertEqual(os.lstat(self.path("d3", "Documents/keep.txt")).st_nlink, 3)

    def test_xattr_preserved(self):
        f = os.path.join(self.src, "d1", "Users", "bob", "Documents", "keep.txt")
        subprocess.run(["xattr", "-w", "com.example.tag", "hello", f], check=True)
        self.run_extract()
        out = subprocess.run(
            ["xattr", "-p", "com.example.tag", self.path("d1", "Documents/keep.txt")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(out.stdout.strip(), "hello")

    def test_mtime_preserved(self):
        f = os.path.join(self.src, "d1", "Users", "bob", "Documents", "edit.txt")
        os.utime(f, (1_500_000_000, 1_500_000_000))
        self.run_extract()
        self.assertEqual(
            int(os.stat(self.path("d1", "Documents/edit.txt")).st_mtime), 1_500_000_000
        )


class TestManifest(Fixture):
    def test_names_with_newline_and_backslash_roundtrip(self):
        odd = ["::60AE::pro.usage.mac.heic", "a\\b.txt", "trailing\n", "cr\rinside", "Icon\r"]
        for name in odd:
            write(os.path.join(self.src, "d1", "Users", "bob", "Documents", "x\n" + name), "data")
        self.run_extract(dates=["d1"])
        with open(os.path.join(self.dst, "_sta", "manifests", "d1.sha256"), newline="\n") as f:
            lines = f.read().split("\n")[:-1]
        parsed = [core.parse_manifest_line(line + "\n") for line in lines]
        rels = {rel for _, rel in parsed}
        for name in odd:
            self.assertIn("Users/bob/Documents/x\n" + name, rels)
        self.assertEqual(len(lines), len(rels), "one manifest line per file")

    def test_line_helpers(self):
        for rel in ("plain.txt", "with\nnl", "with\\bs", "both\\\n", "cr\r", "Icon\r"):
            self.assertEqual(
                core.parse_manifest_line(core.manifest_line("a" * 64, rel)), ("a" * 64, rel)
            )
        self.assertEqual(core.parse_manifest_line(core.manifest_line(None, "x")), (None, "x"))


class TestCache(Fixture):
    def scan_rows(self, opts, n, **kw):
        conn, errs = core.scan(
            self.source,
            self.dates,
            opts,
            os.path.join(self.tmp, f"c{n}.db"),
            log=lambda *_: None,
            cdir=os.path.join(self.tmp, "cache"),
            **kw,
        )
        rows = conn.execute(
            "SELECT snap, rel, kind, ino, size, mtime_ns FROM files ORDER BY snap, rel"
        ).fetchall()
        conn.close()
        return rows

    def test_second_scan_reads_nothing_from_source(self):
        first = self.scan_rows(core.Options(), 1)
        real, calls = core.scan_snapshot, []
        core.scan_snapshot = lambda *a, **k: calls.append(1) or real(*a, **k)
        try:
            second = self.scan_rows(core.Options(), 2)
        finally:
            core.scan_snapshot = real
        self.assertEqual(calls, [])
        self.assertEqual(first, second)

    def test_filtered_run_served_from_full_cache_matches_direct_scan(self):
        opts = core.Options(folders=["Documents"], excludes=["*.bin"])
        direct = self.scan_rows(opts, 1, use_cache=False)
        self.scan_rows(core.Options(), 2)  # populates the full cache
        served = self.scan_rows(opts, 3)  # must come from it, filtered
        self.assertEqual(direct, served)

    def test_uncached_dates(self):
        cdir = os.path.join(self.tmp, "cache")
        self.assertEqual(
            core.uncached_dates(self.source, self.dates, core.Options(), cdir), self.dates
        )
        self.scan_rows(core.Options(), 1)
        self.assertEqual(core.uncached_dates(self.source, self.dates, core.Options(), cdir), [])


class TestPlan(Fixture):
    def test_sizes_with_and_without_links(self):
        conn, _ = core.scan(
            self.source,
            self.dates,
            core.Options(),
            os.path.join(self.tmp, "p.db"),
            log=lambda *_: None,
            use_cache=False,
        )
        plan = core.make_plan(conn, self.dst)
        self.assertLess(plan["bytes_with_links"], plan["bytes_without_links"])
        self.assertTrue(plan["dest_hardlinks"])
        self.assertTrue(plan["fits"])

    def test_select_dates(self):
        alld = ["2026-09-15-100000", "2026-09-15-230000", "2026-09-18-194541"]
        self.assertEqual(
            core.select_dates(alld, one_per_day=True), ["2026-09-15-230000", "2026-09-18-194541"]
        )
        self.assertEqual(core.select_dates(alld, last=1), ["2026-09-18-194541"])
        self.assertEqual(core.select_dates(alld, dates=["2026-09-15"]), alld[:2])


if __name__ == "__main__":
    unittest.main()


class TestDiskFull(Fixture):
    def test_enospc_stops_at_once_says_why_and_still_keeps_a_report(self):
        from unittest import mock

        events = []
        conn, _ = core.scan(self.source, self.dates, core.Options(),
                            os.path.join(self.tmp, "f.db"), log=lambda *_: None,
                            use_cache=False)  # fmt: skip
        calls = []

        def full(*a, **k):
            calls.append(1)
            raise OSError(errno.ENOSPC, "No space left on device")

        ext = core.Extractor(self.source, self.dst, conn, self.dates, log=lambda *_: None,
                             emit=events.append)  # fmt: skip
        with mock.patch.object(core, "_copy_with_retry", side_effect=full), mock.patch.object(
            core, "_copyfile", side_effect=full
        ):
            status = ext.run()
        self.assertEqual(status, core.FAILED)
        self.assertEqual(len(calls), 1, "no more attempts once the disk is full")
        self.assertIn("destination is full", ext.report["fatal"])
        self.assertTrue(any(e["event"] == "error" and "full" in e["message"] for e in events))
        self.assertTrue(os.path.exists(ext.report["report_file"]))

    def test_report_falls_back_when_the_destination_cannot_take_it(self):
        from unittest import mock

        conn, _ = core.scan(self.source, self.dates, core.Options(),
                            os.path.join(self.tmp, "g.db"), log=lambda *_: None,
                            use_cache=False)  # fmt: skip
        ext = core.Extractor(self.source, self.dst, conn, self.dates[:1], log=lambda *_: None)
        real = ext._write_report_to
        seen = []

        def flaky(base):
            seen.append(base)
            if len(seen) == 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(base)

        with mock.patch.object(ext, "_write_report_to", side_effect=flaky), mock.patch.object(
            core, "cache_dir", return_value=os.path.join(self.tmp, "cache")
        ):
            ext.run()
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1].startswith(os.path.join(self.tmp, "cache")))
        self.assertTrue(os.path.exists(ext.report["report_file"]))


class TestScanCache(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = os.path.join(tempfile.mkdtemp(prefix="sta_cache_"), "sta")
        os.makedirs(self.dir)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.dir), ignore_errors=True)

    def put(self, name, size):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(b"x" * size)

    def test_size_and_clear_touch_only_scan_files_never_a_fallback_report(self):
        self.put("aa_full.db", 1000)
        self.put("bb_full.db.tmp", 500)
        self.put("report_20260920-101010.txt", 50)
        self.assertEqual(core.cache_size(self.dir), 1500)
        self.assertEqual(core.clear_cache(self.dir), 1500)
        self.assertEqual(os.listdir(self.dir), ["report_20260920-101010.txt"])
        self.assertEqual(core.cache_size(self.dir), 0)

    def test_clear_removes_the_empty_folder_and_is_harmless_when_missing(self):
        self.put("aa_full.db", 10)
        core.clear_cache(self.dir)
        self.assertFalse(os.path.exists(self.dir))
        self.assertEqual(core.clear_cache(self.dir), 0)
        self.assertEqual(core.cache_size(self.dir), 0)

    def test_cli_cache_shows_and_clears(self):
        from unittest import mock

        self.put("aa_full.db", 2_000_000)
        out = io.StringIO()
        with mock.patch.object(core, "cache_dir", return_value=self.dir), \
                contextlib.redirect_stdout(out):  # fmt: skip
            self.assertEqual(cli.main(["cache"]), 0)
            self.assertIn("Scan cache: 0.00 GB", out.getvalue())
            self.assertTrue(os.path.exists(os.path.join(self.dir, "aa_full.db")), "show only")
            self.assertEqual(cli.main(["cache", "--clear"]), 0)
        self.assertFalse(os.path.exists(self.dir))


class TestUnsafePaths(Fixture):
    def test_safe_rel(self):
        for ok in ("Users/a/b.txt", "Users/a/..hidden", "a/b..c/d"):
            self.assertTrue(core.safe_rel(ok), ok)
        for bad in ("", "/etc/passwd", "../x", "Users/../../x", "a/../b", "a\0b"):
            self.assertFalse(core.safe_rel(bad), repr(bad))

    def test_a_tampered_scan_cannot_make_the_engine_write_outside_the_destination(self):
        conn, _ = core.scan(self.source, self.dates[:1], core.Options(),
                            os.path.join(self.tmp, "t.db"), log=lambda *_: None,
                            use_cache=False)  # fmt: skip
        date = self.dates[0]
        conn.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", (date, "../ESCAPED.txt", "f", 9, 3, 1))
        conn.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", (date, "/tmp/ESCAPED_ABS", "f", 8, 3, 1))
        conn.commit()
        ext = core.Extractor(self.source, self.dst, conn, self.dates[:1], log=lambda *_: None)
        status = ext.run()
        parent = os.path.dirname(self.dst)
        self.assertFalse(os.path.exists(os.path.join(parent, "ESCAPED.txt")))
        self.assertFalse(os.path.exists("/tmp/ESCAPED_ABS"))
        self.assertEqual(status, core.COMPLETED_WITH_ERRORS)
        errs = [e for e in ext.report["dates"][date]["errors"] if "unsafe path" in e[2]]
        self.assertEqual(len(errs), 2)
