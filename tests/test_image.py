import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sta import cli, core, image  # noqa: E402
from test_core import Fixture  # noqa: E402

SMALL = 100 * image.MIB


class TestImage(Fixture):
    backend = True  # diskutil image; the subclass below re-runs everything with hdiutil

    def setUp(self):
        super().setUp()
        self._saved = image.USE_DISKUTIL
        image.USE_DISKUTIL = self.backend
        if self.backend and not image._diskutil_image_available():
            self.skipTest("diskutil image not available on this macOS")

    def tearDown(self):
        image.USE_DISKUTIL = self._saved
        super().tearDown()

    def img(self):
        return os.path.join(self.tmp, image.IMAGE_NAME)

    def test_lifecycle_case_sensitive_links_and_reattach(self):
        with image.ImageMount(self.img(), SMALL) as im:
            self.assertTrue(im.created)
            a, b = (os.path.join(im.mountpoint, n) for n in ("A.txt", "a.txt"))
            for p, t in ((a, "upper"), (b, "lower")):
                with open(p, "w") as f:
                    f.write(t)
            os.link(a, os.path.join(im.mountpoint, "link.txt"))
            visible = sorted(n for n in os.listdir(im.mountpoint) if not n.startswith("."))
            self.assertEqual(visible, ["A.txt", "a.txt", "link.txt"])  # .fseventsd etc. are macOS's
            self.assertEqual(os.stat(a).st_nlink, 2)
        self.assertTrue(im.detached)
        self.assertIsNone(image.attached_mountpoint(self.img()))
        with image.ImageMount(self.img()) as again:  # no size needed: it exists
            self.assertFalse(again.created)
            self.assertIn("A.txt", os.listdir(again.mountpoint))

    def test_creates_missing_parent_folder(self):
        deep = os.path.join(self.tmp, "does", "not", "exist", image.IMAGE_NAME)
        with image.ImageMount(deep, SMALL) as im:
            self.assertTrue(im.created)
        self.assertTrue(os.path.isdir(deep))

    def test_error_message_is_never_empty(self):
        with self.assertRaises(Exception) as cm:
            image._hdiutil("attach", os.path.join(self.tmp, "nope.sparsebundle"))
        self.assertNotIn("failed: no message", str(cm.exception))
        self.assertGreater(len(str(cm.exception)), len("hdiutil attach failed: "))

    def test_missing_image_without_size_is_an_error(self):
        with self.assertRaises(Exception):
            with image.ImageMount(self.img()):
                pass

    def test_detaches_even_if_the_body_fails(self):
        with self.assertRaises(RuntimeError):
            with image.ImageMount(self.img(), SMALL):
                raise RuntimeError("boom")
        self.assertIsNone(image.attached_mountpoint(self.img()))

    def test_extract_into_image_keeps_hard_links(self):
        with image.ImageMount(self.img(), SMALL) as im:
            _, status = self.run_extract(dst=im.mountpoint)
            self.assertEqual(status, core.COMPLETED)
            keep = [
                os.path.join(im.mountpoint, d, "Users/bob/Documents/keep.txt") for d in self.dates
            ]
            self.assertEqual(len({os.lstat(p).st_ino for p in keep}), 1)
            self.assertEqual(os.lstat(keep[0]).st_nlink, 3)

    def test_plan_via_image_assumes_links_and_case_sensitivity(self):
        conn, _ = core.scan(
            self.source,
            self.dates,
            core.Options(),
            os.path.join(self.tmp, "p.db"),
            log=lambda *_: None,
            use_cache=False,
        )
        plan = core.make_plan(conn, self.dst, via_image=True)
        conn.close()
        self.assertTrue(plan["via_image"] and plan["dest_hardlinks"])
        self.assertFalse(plan["dest_case_insensitive"])
        self.assertEqual(plan["bytes_needed"], plan["bytes_with_links"])

    def test_cli_dest_image_end_to_end(self):
        code = cli.main(["extract", self.src, self.dst, "--source-type", "dir", "--dest-image"])
        self.assertEqual(code, 0)
        img = os.path.join(self.dst, image.IMAGE_NAME)
        self.assertTrue(os.path.isdir(img))
        self.assertIsNone(image.attached_mountpoint(img), "image must be detached at the end")
        with image.ImageMount(img) as im:
            self.assertEqual(
                open(os.path.join(im.mountpoint, "d3", "Users/bob/Documents/edit.txt")).read(),
                "v3!",
            )


class TestImageHdiutilFallback(TestImage):
    backend = False


class TestNoHalfMadeImage(Fixture):
    def test_failed_creation_leaves_nothing_behind(self):
        path = os.path.join(self.tmp, "bad", image.IMAGE_NAME)
        real = image._make_case_sensitive
        image._make_case_sensitive = lambda *a: (_ for _ in ()).throw(image.ApfsError("boom"))
        try:
            if not image._diskutil_image_available():
                self.skipTest("diskutil image not available")
            with self.assertRaises(image.ApfsError):
                image.create_image(path, SMALL)
        finally:
            image._make_case_sensitive = real
        self.assertFalse(os.path.exists(path), "half-made image must be removed")


if __name__ == "__main__":
    unittest.main()
