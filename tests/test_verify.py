import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sta import cli, core, image, verify  # noqa: E402
from test_core import Fixture  # noqa: E402


class TestVerify(Fixture):
    def setUp(self):
        super().setUp()
        _, status = self.run_extract()
        self.assertEqual(status, core.COMPLETED)

    def test_clean_archive_is_verified_and_links_are_hashed_once(self):
        rep = verify.verify_archive(self.dst)
        self.assertEqual(rep["status"], verify.VERIFIED)
        self.assertEqual((rep["mismatches"], rep["missing"], rep["unreadable"]), ([], [], []))
        self.assertEqual(rep["dates"], 3)
        self.assertLess(rep["hashed"], rep["entries"], "hard links must be checked once")

    def test_corrupted_file_is_detected(self):
        p = self.path("d2", "Documents/edit.txt")
        with open(p, "ab") as f:
            f.write(b"x")
        rep = verify.verify_archive(self.dst)
        self.assertEqual(rep["status"], verify.FAILED)
        self.assertTrue(any("edit.txt" in rel for _, rel in rep["mismatches"]))

    def test_deleted_file_is_detected(self):
        os.remove(self.path("d1", "Documents/deleted-later.txt"))
        rep = verify.verify_archive(self.dst)
        self.assertEqual(rep["status"], verify.FAILED)
        self.assertEqual(rep["missing"], [("d1", "Users/bob/Documents/deleted-later.txt")])

    @unittest.skipIf(os.geteuid() == 0, "root can read everything")
    def test_permission_protected_file_is_a_warning_not_a_failure(self):
        p = self.path("d3", "Documents/new.txt")
        os.chmod(p, 0)
        try:
            rep = verify.verify_archive(self.dst)
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(rep["status"], verify.VERIFIED_WITH_WARNINGS)
        self.assertEqual(len(rep["unreadable"]), 1)

    def test_cancel(self):
        rep = verify.verify_archive(self.dst, cancel=lambda: True)
        self.assertEqual(rep["status"], verify.CANCELLED)

    def test_not_an_archive(self):
        with self.assertRaises(Exception):
            verify.verify_archive(self.tmp)

    def test_archive_inside_an_image(self):
        code = cli.main(["extract", self.src, os.path.join(self.tmp, "img"), "--source-type", "dir",
                         "--dest-image"])  # fmt: skip
        self.assertEqual(code, 0)
        folder = os.path.join(self.tmp, "img")
        rep = verify.verify_archive(folder)  # a folder that holds the image
        self.assertEqual(rep["status"], verify.VERIFIED)
        self.assertIsNone(image.attached_mountpoint(os.path.join(folder, image.IMAGE_NAME)))

    def test_cli_exit_codes(self):
        self.assertEqual(cli.main(["verify", self.dst]), 0)
        with open(self.path("d1", "Documents/keep.txt"), "ab") as f:
            f.write(b"!")
        self.assertEqual(cli.main(["verify", self.dst]), 1)


if __name__ == "__main__":
    unittest.main()


class TestFindArchiveImages(unittest.TestCase):
    def test_folder_with_one_or_several_images(self):
        import tempfile

        from sta import verify
        from sta.apfs import ApfsError

        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "SmartTimeArchive_20260920-100000.sparsebundle"))
            self.assertEqual(
                verify.find_archive(d)[1],
                os.path.join(d, "SmartTimeArchive_20260920-100000.sparsebundle"),
            )
            os.makedirs(os.path.join(d, "SmartTimeArchive.sparsebundle"))  # an old-style name
            with self.assertRaises(ApfsError):
                verify.find_archive(d)
