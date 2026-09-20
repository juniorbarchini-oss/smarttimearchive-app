import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAVE_TEXTUAL = importlib.util.find_spec("textual") is not None

if HAVE_TEXTUAL:
    from textual.widgets import OptionList

    from sta.discover import BackupVolume
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

    async def test_art_adapts_to_terminal_width(self):
        for width, expected in ((120, "big"), (70, "small")):
            app = tui.StaApp(discover=fake_found)
            async with app.run_test(size=(width, 30)):
                self.assertEqual(app.screen.art_used, expected)

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

    async def test_step2_starts_with_every_backup_checked(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            self.assertEqual(sorted(sl.selected), ["2026-08-23-203105", "2026-09-18-194541"])
            self.assertIn("2 of 2 backups selected", str(app.screen.query_one("#summary").render()))

    def six_backups(self):
        v = fake_found()[0][1]
        v.snapshots = [f"2026-09-0{i}-100000" for i in range(1, 7)]
        return [v], []

    async def test_step2_all_and_none(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            await pilot.press("n")
            await pilot.pause(0.1)
            self.assertEqual(list(sl.selected), [])
            self.assertIn("No backup selected", str(app.screen.query_one("#summary").render()))
            await pilot.press("a")
            await pilot.pause(0.1)
            self.assertEqual(len(sl.selected), 6)

    async def test_step2_oldest_n_default_is_half_and_enter_applies(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            await pilot.press("o")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.OldestDialog)
            await pilot.press("enter")  # default = 6 // 2 = 3
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.BackupsScreen)
            self.assertEqual(
                sorted(sl.selected), ["2026-09-01-100000", "2026-09-02-100000", "2026-09-03-100000"]
            )

    async def test_step2_oldest_n_typed_and_invalid_numbers_are_refused(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            await pilot.press("o")
            await pilot.pause(0.2)
            await pilot.press("backspace", "9", "9", "enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.OldestDialog, "99 > 6 keeps the dialog open")
            await pilot.press("backspace", "backspace", "2", "enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.BackupsScreen)
            self.assertEqual(sorted(sl.selected), ["2026-09-01-100000", "2026-09-02-100000"])

    async def test_step2_escape_cancels_the_dialog_without_changing_anything(self):
        app = tui.StaApp(discover=self.six_backups, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            sl = await self.open_backups(pilot, app)
            await pilot.press("o")
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.BackupsScreen)
            self.assertEqual(len(sl.selected), 6)

    async def test_step2_continue_stores_the_choice_and_needs_at_least_one(self):
        app = tui.StaApp(discover=fake_found, skip_welcome=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_backups(pilot, app)
            await pilot.press("n")
            await pilot.press("c")
            await pilot.pause(0.2)
            self.assertIsInstance(
                app.screen, tui.BackupsScreen, "cannot continue with nothing checked"
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

    async def test_welcome_shows_the_credit_line(self):
        app = tui.StaApp(discover=fake_found)
        async with app.run_test(size=(120, 40)):
            self.assertIn("Claude", str(app.screen.query_one("#credits").render()))

    def test_helpers(self):
        self.assertEqual(tui.fmt_bytes(281_700_000_000), "281.7 GB")
        self.assertEqual(tui.fmt_bytes(512), "512 B")
        self.assertEqual(tui.date_of("2026-09-18-194541"), "2026-09-18")


if __name__ == "__main__":
    unittest.main()
