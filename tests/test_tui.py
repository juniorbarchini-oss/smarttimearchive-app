import asyncio
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
            self.assertIsInstance(app.screen, tui.PasswordScreen)
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
            shown = " ".join(str(w.render()) for w in app.screen.query("Static"))
            for key in ("[y]", "[n]", "[Esc]"):
                self.assertIn(key, shown, "the keys must be visible, not eaten as markup")
            await pilot.press("y")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.PasswordScreen)
            self.assertEqual((app.dest_path, app.dest_image), (self.stick, True))

    async def test_no_hard_links_user_may_refuse_the_image(self):
        app = self.new_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.open_stick(pilot)
            await pilot.press("n")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, tui.PasswordScreen)
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


class TestPrivileges(unittest.TestCase):
    def test_has_ticket_follows_sudo_n(self):
        from types import SimpleNamespace as R

        from sta.tui import privileges

        self.assertTrue(privileges.has_ticket(lambda *a, **k: R(returncode=0)))
        self.assertFalse(privileges.has_ticket(lambda *a, **k: R(returncode=1)))

        def missing(*a, **k):
            raise OSError

        self.assertFalse(privileges.has_ticket(missing))

    def test_keepalive_renews_and_stops(self):
        import time

        from sta.tui import privileges

        calls = []
        ka = privileges.Keepalive(run=lambda cmd, **k: calls.append(cmd), interval=0.01)
        ka.start()
        time.sleep(0.1)
        ka.stop()
        n = len(calls)
        time.sleep(0.1)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(calls[0], ["sudo", "-n", "-v"])
        self.assertLessEqual(len(calls) - n, 1)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TestPasswordScreen(unittest.IsolatedAsyncioTestCase):
    async def _run(self, ticket, accepted):
        from unittest import mock

        app = tui.StaApp(skip_welcome=True)
        app.source, app.dest_path = fake_found()[0][1], "/tmp/x"
        asked = []
        app.ask_password = lambda: asked.append(1) or accepted
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(tui.PasswordScreen())
            await pilot.pause(0.2)
            with mock.patch.object(tui.privileges, "has_ticket", side_effect=ticket):
                with mock.patch.object(type(app), "suspend", mock.MagicMock()):
                    await pilot.press("enter")
                    await pilot.pause(0.3)
            return type(app.screen).__name__, asked

    async def test_existing_ticket_skips_the_prompt(self):
        name, asked = await self._run([True], True)
        self.assertEqual((name, asked), ("ScanScreen", []))

    async def test_password_accepted_moves_on(self):
        name, asked = await self._run([False, True], True)
        self.assertEqual((name, len(asked)), ("ScanScreen", 1))

    async def test_wrong_or_cancelled_password_stays_and_lets_the_user_retry(self):
        name, asked = await self._run([False], False)
        self.assertEqual((name, len(asked)), ("PasswordScreen", 1))


class FakeEngine:
    """Stands in for the root subprocess: the test feeds it events by hand."""

    instances = []

    def __init__(self, args, on_event, on_exit, **kw):
        self.args, self.on_event, self.on_exit, self.kw = args, on_event, on_exit, kw
        self.paused, self.calls = False, []
        FakeEngine.instances.append(self)

    def start(self):
        self.calls.append("start")

    def cancel(self):
        self.calls.append("cancel")


PLAN = {
    "snapshots": 2, "files": 10, "unique_files": 4, "bytes_with_links": 4_000_000_000,
    "bytes_without_links": 9_000_000_000, "bytes_needed": 4_000_000_000, "bytes_needed_with_margin": 5_000_000_000,
    "dest_free": 50_000_000_000, "fits": True,
}  # fmt: skip


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TestScanScreen(unittest.IsolatedAsyncioTestCase):
    async def open(self, pilot_fn, pending=2, fits=True):
        FakeEngine.instances = []
        app = tui.StaApp(skip_welcome=True)
        app.source = fake_found()[0][1]
        app.dates, app.dest_path = list(app.source.snapshots), "/tmp/x"
        app.make_engine = FakeEngine
        app.scan_source = lambda mp: None
        from unittest import mock

        with mock.patch.object(
            tui.core, "uncached_dates", return_value=app.dates[:pending]
        ), mock.patch.object(tui.privileges.Keepalive, "start"), mock.patch.object(
            tui.privileges, "has_ticket", return_value=True
        ):
            async with app.run_test(size=(120, 40)) as pilot:
                app.push_screen(tui.ScanScreen())
                await pilot.pause(0.4)
                await pilot_fn(app, pilot)

    def body(self, app):
        return str(app.screen.query_one("#body").render())

    async def test_warns_about_time_and_does_nothing_until_yes(self):
        async def go(app, pilot):
            self.assertIn("never scanned", self.body(app))
            self.assertEqual(FakeEngine.instances, [])
            await pilot.press("c")  # keys that mean nothing yet
            self.assertEqual(FakeEngine.instances, [])
            await pilot.press("y")
            await pilot.pause(0.2)
            self.assertEqual(FakeEngine.instances[0].calls, ["start"])
            self.assertEqual(FakeEngine.instances[0].args[0], "plan")

        await self.open(go)

    async def test_no_goes_back_without_starting(self):
        async def go(app, pilot):
            await pilot.press("n")
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, tui.ScanScreen)
            self.assertEqual(FakeEngine.instances, [])

        await self.open(go)

    async def test_all_cached_says_it_is_quick(self):
        async def go(app, pilot):
            self.assertIn("quick", self.body(app))

        await self.open(go, pending=0)


    async def test_cancel_needs_confirmation_and_n_keeps_going(self):
        async def go(app, pilot):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            await pilot.press("c")
            self.assertIn("Cancel the scan?", self.body(app))
            self.assertNotIn("cancel", eng.calls)
            await pilot.press("n")
            await pilot.pause(0.2)
            self.assertNotIn("Cancel the scan?", self.body(app))
            await pilot.press("c", "y")
            await pilot.pause(0.2)
            self.assertIn("cancel", eng.calls)
            await asyncio.to_thread(eng.on_exit, 130, [])
            await pilot.pause(0.2)
            self.assertIn("Nothing was copied", self.body(app))

        await self.open(go)

    async def test_plan_fits_and_does_not_fit(self):
        async def go(app, pilot):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            await asyncio.to_thread(eng.on_event, {"event": "plan", **PLAN})
            await asyncio.to_thread(eng.on_exit, 0, [])
            await pilot.pause(0.2)
            self.assertIn("It fits", self.body(app))
            self.assertIn("WITH hard links", self.body(app))
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.CopyScreen)

        await self.open(go)

        async def go2(app, pilot):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            await asyncio.to_thread(eng.on_event, {"event": "plan", **PLAN, "fits": False, "dest_free": 1_000_000_000})
            await asyncio.to_thread(eng.on_exit, 2, [])
            await pilot.pause(0.2)
            self.assertIn("does NOT fit", self.body(app))
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.ScanScreen, "no space: cannot continue")

        await self.open(go2)

    async def test_engine_failure_shows_the_reason(self):
        async def go(app, pilot):
            await pilot.press("y")
            await asyncio.to_thread(FakeEngine.instances[0].on_exit, 1, ["error: something broke"])
            await pilot.pause(0.2)
            self.assertIn("scan failed", self.body(app))
            self.assertIn("something broke", self.body(app))

        await self.open(go)

    async def test_cannot_quit_or_leave_while_running(self):
        async def go(app, pilot):
            await pilot.press("y")
            await pilot.press("q", "escape")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.ScanScreen)

        await self.open(go)


class TestEngineRun(unittest.TestCase):
    def test_relays_events_tracks_pid_and_signals_through_sudo(self):
        import io
        from types import SimpleNamespace as R

        from sta.tui import engine

        proc = R(stdout=io.StringIO('{"event": "started", "pid": 77}\nboom\n{"event": "plan"}\n'),
                 wait=lambda: 0)  # fmt: skip
        events, exits, sent = [], [], []
        run = engine.EngineRun(
            ["plan", "/v", "/d"], events.append, lambda c, t: exits.append((c, t)),
            popen=lambda cmd, **k: proc, run=lambda cmd, **k: sent.append(cmd),
        )  # fmt: skip
        run.start()
        run._thread_done = None
        import time

        for _ in range(50):
            if exits:
                break
            time.sleep(0.02)
        self.assertEqual([e["event"] for e in events], ["started", "plan"])
        self.assertEqual(exits, [(0, ["boom"])])
        run.cancel()
        self.assertEqual(sent, [["sudo", "-n", "kill", "-INT", "77"]])


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TestCopyScreen(unittest.IsolatedAsyncioTestCase):
    async def open(self, pilot_fn, image=True, ticket=True, tmux=""):
        from unittest import mock

        FakeEngine.instances = []
        app = tui.StaApp(skip_welcome=True)
        app.source = fake_found()[0][1]
        app.dates, app.dest_path, app.dest_image = list(app.source.snapshots), "/tmp/x", image
        app.plan = dict(PLAN, bytes_needed=4_000_000_000)
        app.destination = Destination("USB", "/tmp/x", "exfat", 1, False)
        app.make_engine = FakeEngine
        asked = []
        app.ask_password = lambda: asked.append(1) or True
        with mock.patch.dict(os.environ, {"TMUX": tmux}), mock.patch.object(
            tui.privileges, "has_ticket", side_effect=lambda *a: ticket
        ), mock.patch.object(tui.privileges.Keepalive, "start"), mock.patch.object(
            type(app), "suspend", mock.MagicMock()
        ):
            async with app.run_test(size=(120, 40)) as pilot:
                app.push_screen(tui.CopyScreen())
                await pilot.pause(0.4)
                await pilot_fn(app, pilot, asked)

    def body(self, app):
        return str(app.screen.query_one("#body").render())

    async def test_confirm_first_keep_awake_is_off_and_tmux_tip(self):
        async def go(app, pilot, asked):
            text = self.body(app)
            self.assertIn("( ) k", text)
            self.assertIn("not inside tmux", text)
            self.assertEqual(FakeEngine.instances, [])
            await pilot.press("c")
            self.assertEqual(FakeEngine.instances, [])
            await pilot.press("k")
            self.assertIn("(x) k", self.body(app))
            await pilot.press("y")
            await pilot.pause(0.2)
            eng = FakeEngine.instances[0]
            self.assertEqual(eng.args[0], "extract")
            self.assertIn("--dest-image", eng.args)
            self.assertEqual(eng.kw, {"keep_awake": True})

        await self.open(go)

    async def test_default_does_not_keep_awake_and_no_tip_inside_tmux(self):
        async def go(app, pilot, asked):
            self.assertNotIn("not inside tmux", self.body(app))
            await pilot.press("y")
            await pilot.pause(0.2)
            eng = FakeEngine.instances[0]
            self.assertEqual(eng.kw, {"keep_awake": False})
            self.assertIn("--yes", eng.args)
            self.assertNotIn("--dest-image", eng.args)

        await self.open(go, image=False, tmux="/tmp/tmux")

    async def test_progress_done_and_totals(self):
        async def go(app, pilot, asked):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            feed = lambda ev: asyncio.to_thread(eng.on_event, ev)  # noqa: E731
            await feed({"event": "extract_start", "date": "2026-08-23-203105", "i": 1, "n": 2,
                        "entries": 100})  # fmt: skip
            await feed({"event": "extract_progress", "done": 40, "total": 100,
                        "bytes_copied": 2_000_000_000, "linked": 7, "errors": 0})  # fmt: skip
            await pilot.pause(0.2)
            text = self.body(app)
            self.assertIn("Backup 1 of 2", text)
            self.assertIn("40 of 100", text)
            self.assertIn("2.0 GB copied", text)
            await feed({"event": "date_done", "bytes_copied": 3_000_000_000, "errors": 0})
            await feed({"event": "finished", "status": "COMPLETED", "report_file": "/r.txt"})
            await asyncio.to_thread(eng.on_exit, 0, [])
            await pilot.pause(0.2)
            text = self.body(app)
            self.assertIn("Done.", text)
            self.assertIn("3.0 GB", text)
            self.assertIn("/r.txt", text)

        await self.open(go)

    async def test_cancel_keeps_finished_dates(self):
        async def go(app, pilot, asked):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            await pilot.press("p")  # there is no pause: the key does nothing
            await pilot.press("c", "y")
            await pilot.pause(0.2)
            self.assertIn("cancel", eng.calls)
            await asyncio.to_thread(eng.on_exit, 130, [])
            await pilot.pause(0.2)
            self.assertIn(".partial", self.body(app))

        await self.open(go)

    async def test_expired_ticket_asks_again_and_never_starts_silently(self):
        async def go(app, pilot, asked):
            await pilot.press("y")
            await pilot.pause(0.2)
            self.assertEqual(len(asked), 1)
            self.assertEqual(FakeEngine.instances, [], "no valid ticket after asking: no start")

        await self.open(go, ticket=False)


class TestEngineCommand(unittest.TestCase):
    def test_caffeinate_only_when_asked_and_present(self):
        from unittest import mock

        from sta.tui import engine

        base = engine.EngineRun(["extract"], None, None).command()
        self.assertNotIn(engine.CAFFEINATE, base)
        awake = engine.EngineRun(["extract"], None, None, keep_awake=True)
        with mock.patch.object(engine.os.path, "exists", return_value=True):
            cmd = awake.command()
        self.assertEqual(cmd[:4], ["sudo", "-n", engine.CAFFEINATE, "-i"])
        with mock.patch.object(engine.os.path, "exists", return_value=False):
            self.assertNotIn(engine.CAFFEINATE, awake.command())



@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TestReportScreen(unittest.IsolatedAsyncioTestCase):
    FINISHED = {"event": "finished", "status": "COMPLETED", "report_file": "/r.txt"}

    async def open(self, pilot_fn, finished=None, errors=0):
        from unittest import mock

        FakeEngine.instances = []
        app = tui.StaApp(skip_welcome=True)
        app.source = fake_found()[0][1]
        app.dest_path = "/Volumes/X/test"
        app.make_engine = FakeEngine
        with mock.patch.object(tui.privileges.Keepalive, "start"), mock.patch.object(
            tui.privileges, "has_ticket", return_value=True
        ):
            async with app.run_test(size=(120, 40)) as pilot:
                app.push_screen(
                    tui.ReportScreen(finished or self.FINISHED, 79_500_000_000, 203_000, errors, 2)
                )
                await pilot.pause(0.4)
                await pilot_fn(app, pilot)

    def body(self, app):
        return str(app.screen.query_one("#body").render())

    async def test_summary_then_asks_and_warns_about_time_and_starts_nothing(self):
        async def go(app, pilot):
            text = self.body(app)
            for part in ("COMPLETED", "79.5 GB", "/r.txt", "can take a", "y = yes"):
                self.assertIn(part, text)
            await pilot.press("c", "enter", "escape")
            self.assertEqual(FakeEngine.instances, [], "nothing runs until the user says yes")
            self.assertIsInstance(app.screen, tui.ReportScreen)

        await self.open(go)

    async def test_no_skips_and_says_not_verified(self):
        async def go(app, pilot):
            await pilot.press("n")
            self.assertIn("Not verified", self.body(app))
            self.assertEqual(FakeEngine.instances, [])

        await self.open(go)

    async def test_m_starts_over_only_when_nothing_is_running(self):
        async def go(app, pilot):
            await pilot.press("m")  # asked to verify: not an end state yet
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.ReportScreen)
            await pilot.press("n", "m")
            await pilot.pause(0.6)
            self.assertIsInstance(app.screen, tui.SourceScreen)
            self.assertEqual(
                (app.source, app.dates, app.dest_path, app.plan), (None, [], None, None)
            )
            self.assertEqual(len(app.screen_stack), 2, "no stale screens left behind")

        await self.open(go)

    async def test_m_is_ignored_while_verifying(self):
        async def go(app, pilot):
            await pilot.press("y", "m")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, tui.ReportScreen)

        await self.open(go)

    async def test_errors_are_reported_not_hidden(self):
        async def go(app, pilot):
            self.assertIn("could not be copied", self.body(app))
            self.assertIn("12 files", self.body(app))

        await self.open(
            go, dict(self.FINISHED, status="COMPLETED_WITH_ERRORS"), errors=12
        )

    async def test_verify_runs_shows_progress_and_a_clean_result(self):
        async def go(app, pilot):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            self.assertEqual(eng.args, ["verify", "/Volumes/X/test"])
            await asyncio.to_thread(
                eng.on_event, {"event": "verify_progress", "done": 40, "total": 100}
            )
            await pilot.pause(0.2)
            self.assertIn("40 of 100", self.body(app))
            await asyncio.to_thread(
                eng.on_event,
                {"event": "verified", "status": "VERIFIED", "entries": 100, "hashed": 60,
                 "mismatches": 0, "missing": 0, "unreadable": 0, "no_checksum": 0},
            )  # fmt: skip
            await asyncio.to_thread(eng.on_exit, 0, [])
            await pilot.pause(0.2)
            self.assertIn("Every file matches", self.body(app))

        await self.open(go)

    async def test_verify_problems_are_listed_and_cancel_works(self):
        async def go(app, pilot):
            await pilot.press("y", "c", "n")  # cancel asked, then declined: keeps going
            self.assertNotIn("cancel", FakeEngine.instances[0].calls)
            await pilot.press("c", "y")
            self.assertIn("cancel", FakeEngine.instances[0].calls)
            await asyncio.to_thread(FakeEngine.instances[0].on_exit, 130, [])
            await pilot.pause(0.2)
            self.assertIn("Verification cancelled", self.body(app))

        await self.open(go)

    async def test_differences_are_shown(self):
        async def go(app, pilot):
            await pilot.press("y")
            eng = FakeEngine.instances[0]
            await asyncio.to_thread(
                eng.on_event,
                {"event": "verified", "status": "FAILED", "entries": 100, "hashed": 60,
                 "mismatches": 2, "missing": 1, "unreadable": 0, "no_checksum": 0,
                 "examples": ["2026-08-23-203105/Users/a/b.txt"]},
            )  # fmt: skip
            await asyncio.to_thread(eng.on_exit, 1, [])
            await pilot.pause(0.2)
            text = self.body(app)
            self.assertIn("Different: 2", text)
            self.assertIn("b.txt", text)
            self.assertNotIn("Every file matches", text)

        await self.open(go)
