import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAVE_TEXTUAL = importlib.util.find_spec("textual") is not None

if HAVE_TEXTUAL:
    from textual.widgets import OptionList

    from sta.discover import BackupVolume, Destination
    from sta.tui import app as tui


def fake_found():
    good = BackupVolume(
        "Good USB", "disk7s2", "/Volumes/Good USB", "USB", 281_700_000_000,
        ["2026-08-23-203105", "2026-09-18-194541"], True,
    )  # fmt: skip
    net = BackupVolume("Net image", "disk5s1", "/Volumes/N", "Disk Image", 5, [], False,
                       "Time Machine on a network image: not a supported source")  # fmt: skip
    return [net, good], ["Old_TM"]


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed (see README: terminal UI)")
class TestTui(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tick = tui.WelcomeScreen.TICK
        tui.WelcomeScreen.TICK = 0.001

    def tearDown(self):
        tui.WelcomeScreen.TICK = self._tick

    async def test_welcome_skips_on_any_key_and_lands_on_source(self):
        app = tui.StaApp(discover=fake_found)
        async with app.run_test(size=(120, 40)) as pilot:
            self.assertIsInstance(app.screen, tui.WelcomeScreen)
            await pilot.press("x")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.SourceScreen)

    async def test_welcome_moves_on_by_itself(self):
        app = tui.StaApp(discover=fake_found)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(1.5)
            self.assertIsInstance(app.screen, tui.SourceScreen)

    async def test_art_adapts_to_terminal_size(self):
        cases = ((120, 40, "stacked"), (70, 30, "stacked"), (120, 16, "big"), (50, 30, "small"))
        for width, height, expected in cases:
            app = tui.StaApp(discover=fake_found)
            async with app.run_test(size=(width, height)):
                self.assertEqual(app.screen.art_used, expected, (width, height))

    async def test_welcome_tells_how_to_skip_and_shows_credits_at_the_end(self):
        app = tui.StaApp(discover=fake_found)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.05)
            self.assertIn("press any key to skip", str(app.screen.query_one("#hint").render()))
            await pilot.pause(0.6)
            if isinstance(app.screen, tui.WelcomeScreen):
                self.assertIn("Claude", str(app.screen.query_one("#credits").render()))

    async def test_source_lists_only_usable_disks_as_selectable(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            ol = app.screen.query_one(OptionList)
            self.assertEqual(ol.option_count, 2)
            self.assertTrue(ol.get_option("disk5s1").disabled)
            self.assertFalse(ol.get_option("disk7s2").disabled)
            self.assertEqual(
                ol.highlighted, 0, "usable disks are listed first, cursor on the first"
            )
            self.assertIn("Old_TM", str(app.screen.query_one("#message").render()))

    async def test_enter_selects_the_source_and_moves_to_step_two(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(app.source.name, "Good USB")
            self.assertIsInstance(app.screen, tui.BackupsScreen)
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.SourceScreen)

    async def open_backups(self, pilot, app):
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.3)
        return app.screen.query_one("#dates")

    def six_backups(self):
        v = fake_found()[0][1]
        v.snapshots = [f"2026-09-0{i}-100000" for i in range(1, 7)]
        return [v], []

    async def test_step2_starts_with_nothing_marked_and_says_so(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            self.assertEqual(list(sl.selected), [])
            self.assertIn(
                "No backup marked (0 of 6)", str(app.screen.query_one("#summary").render())
            )

    async def test_step2_three_controls_one_by_one_mark_all_unmark_all(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            await pilot.press("space")  # the cursor starts on the first backup: one by one
            await pilot.pause(0.1)
            self.assertEqual(list(sl.selected), ["2026-09-01-100000"])
            await pilot.press("down", "down", "space")
            await pilot.pause(0.1)
            self.assertEqual(sorted(sl.selected), ["2026-09-01-100000", "2026-09-03-100000"])
            await pilot.press("space")  # unmark that same one again
            await pilot.pause(0.1)
            self.assertEqual(list(sl.selected), ["2026-09-01-100000"])
            await pilot.press("a")  # mark all
            await pilot.pause(0.1)
            self.assertEqual(len(sl.selected), 6)
            self.assertIn("6 of 6 backups marked", str(app.screen.query_one("#summary").render()))
            await pilot.press("n")  # unmark all
            await pilot.pause(0.1)
            self.assertEqual(list(sl.selected), [])

    async def test_step2_there_is_no_choosing_for_the_user(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_backups(pilot, app)
            keys = {b[0] for b in app.screen.BINDINGS}
            self.assertEqual(keys, {"a", "n", "c", "escape", "q"})

    async def test_step2_continue_stores_the_choice_and_needs_at_least_one(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_backups(pilot, app)
            await pilot.press("c")
            await pilot.pause(0.2)
            self.assertIsInstance(
                app.screen, tui.BackupsScreen, "cannot continue with nothing marked"
            )
            await pilot.press("a")
            await pilot.press("c")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.DestinationScreen)
            self.assertEqual(app.dates, ["2026-08-23-203105", "2026-09-18-194541"])

    def test_nice_date(self):
        self.assertEqual(tui.nice_date("2026-09-18-194541"), "2026-09-18  19:45:41")

    async def test_no_usable_disk_explains_what_to_do(self):
        app = tui.StaApp(discover=lambda: ([], []), skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            msg = str(app.screen.query_one("#message").render())
            self.assertIn("Connect the USB disk", msg)

    async def test_slow_discovery_shows_a_counter_and_then_the_disks(self):
        import time

        def slow():
            time.sleep(1.6)
            return fake_found()

        app = tui.StaApp(discover=slow, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(1.2)
            self.assertIn(
                "Looking for backup disks", str(app.screen.query_one("#message").render())
            )
            self.assertEqual(app.screen.query_one(OptionList).option_count, 0)
            await pilot.pause(1.2)
            self.assertEqual(app.screen.query_one(OptionList).option_count, 2)
            self.assertNotIn("Looking", str(app.screen.query_one("#message").render()))

    async def test_r_rescans(self):
        calls = []
        app = tui.StaApp(discover=lambda: calls.append(1) or fake_found(), skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("r")
            await pilot.pause(0.5)
            self.assertEqual(len(calls), 2)

    async def test_about_screen_credits_author_and_claude(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("a")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.AboutScreen)
            text = " ".join(str(w.render()) for w in app.screen.query("Static"))
            for needle in ("Humberto Barchini", "Claude", "Not affiliated"):
                self.assertIn(needle, text)
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.SourceScreen)

    def test_helpers(self):
        self.assertEqual(tui.fmt_bytes(281_700_000_000), "281.7 GB")
        self.assertEqual(tui.fmt_bytes(512), "512 B")
        self.assertEqual(tui.date_of("2026-09-18-194541"), "2026-09-18")


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed (see README: terminal UI)")
class TestDestinationStep(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tick = tui.WelcomeScreen.TICK
        tui.WelcomeScreen.TICK = 0.001
        self.tmp = tempfile.mkdtemp(prefix="sta_tui_")
        self.fast = os.path.join(self.tmp, "Fast")
        self.stick = os.path.join(self.tmp, "Stick")
        self.dji = os.path.join(self.tmp, "DJI")
        for d in (self.fast, self.stick, self.dji):
            os.makedirs(os.path.join(d, "Photos"))

    def tearDown(self):
        tui.WelcomeScreen.TICK = self._tick
        shutil.rmtree(self.tmp, ignore_errors=True)

    def destinations(self, sources):
        self.seen_sources = list(sources)
        return [
            Destination("Fast", self.fast, "apfs", 500_000_000_000, True),
            Destination("Stick", self.stick, "exfat", 300_000_000_000, False,
                        archive_dates=["2026-09-12-123902"]),
            Destination("DJI", self.dji, "apfs", 1_000_000_000_000, True, is_backup_source=True),
        ]  # fmt: skip

    def new_app(self):
        return tui.StaApp(discover=fake_found, skip_welcome=True, destinations=self.destinations)

    async def to_step3(self, pilot):
        await pilot.pause(0.5)
        await pilot.press("enter")  # pick the source disk
        await pilot.pause(0.3)
        await pilot.press("a", "c")  # mark all backups, continue
        await pilot.pause(0.6)

    async def test_nothing_is_chosen_for_the_user_and_the_source_disk_is_blocked(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.to_step3(pilot)
            self.assertIsInstance(app.screen, tui.DestinationScreen)
            ol = app.screen.query_one(OptionList)
            self.assertEqual(ol.option_count, 3)
            self.assertIsNone(app.destination, "nothing is selected until the user presses Enter")
            self.assertEqual(
                sorted(v.name for v in self.seen_sources),
                ["Good USB", "Net image"],
                "every Time Machine volume found (not only the chosen source) is protected",
            )
            self.assertTrue(ol.get_option(self.dji).disabled)
            self.assertFalse(ol.get_option(self.fast).disabled)
            for _ in range(5):  # the cursor skips the disabled source disk: it can never land on it
                await pilot.press("down")
                await pilot.pause(0.05)
                self.assertIn(ol.highlighted, (0, 1))
            self.assertIsNone(app.destination)

    async def test_disk_with_hard_links_goes_straight_to_the_folder_choice(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.to_step3(pilot)
            await pilot.press("enter")
            await pilot.pause(0.6)
            self.assertIsInstance(app.screen, tui.FolderScreen)
            await pilot.press("u")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.ScanScreen)
            self.assertEqual(app.dest_path, self.fast)
            self.assertFalse(app.dest_image)

    async def open_stick(self, pilot):
        await self.to_step3(pilot)
        await pilot.press("down", "enter")
        await pilot.pause(0.6)
        await pilot.press("u")
        await pilot.pause(0.3)

    async def test_no_hard_links_asks_before_creating_an_image_yes(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_stick(pilot)
            self.assertIsInstance(app.screen, tui.ImageChoice)
            await pilot.press("y")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.ScanScreen)
            self.assertEqual((app.dest_path, app.dest_image), (self.stick, True))

    async def test_no_hard_links_user_may_refuse_the_image(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_stick(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.ScanScreen)
            self.assertEqual((app.dest_path, app.dest_image), (self.stick, False))

    async def test_escape_in_the_image_question_goes_back_without_choosing(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_stick(pilot)
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.FolderScreen)
            self.assertIsNone(app.dest_path)

    async def test_user_can_create_a_folder_and_bad_names_are_refused(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.to_step3(pilot)
            await pilot.press("enter")
            await pilot.pause(0.6)
            await pilot.press("m")
            await pilot.pause(0.2)
            await pilot.press(*"a/b", "enter")  # slash: refused, dialog stays
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.NewFolder)
            await pilot.press("backspace", "backspace", "backspace", *"Archive", "enter")
            await pilot.pause(0.4)
            self.assertTrue(os.path.isdir(os.path.join(self.fast, "Archive")))
            self.assertIsInstance(app.screen, tui.FolderScreen)

    async def test_existing_archive_is_announced_when_the_folder_is_used(self):
        os.makedirs(os.path.join(self.stick, "_sta", "manifests"))
        open(os.path.join(self.stick, "_sta", "manifests", "2026-09-12-123902.sha256"), "w").close()
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_stick(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            notes = " ".join(str(n.message) for n in app._notifications)
            self.assertIn("archive is already here (1 dates)", notes)


if __name__ == "__main__":
    unittest.main()
