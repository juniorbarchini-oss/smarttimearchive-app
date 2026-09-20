"""SmartTimeArchive terminal UI: a linear wizard on top of the engine."""

import os
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DirectoryTree, Footer, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from .. import AUTHOR, DISCLAIMER, REPO, SUPPORT, __version__, core, discover
from . import art, engine, privileges

STEPS = ["Source", "Backups", "Destination", "Scan", "Run", "Report"]


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1000


def date_of(snapshot_name):
    return snapshot_name[:10]


def discover_destinations(sources=()):
    return discover.find_destinations(sources=sources)


def discover_all():
    """(backup volumes, legacy HFS+ backup names): what the source screen lists."""
    return discover.find_backup_volumes(), discover.find_legacy_backups()


class StepBar(Static):
    def __init__(self, current):
        text = Text()
        for i, name in enumerate(STEPS, 1):
            style = "bold #eaffea on #124d20" if i == current else "#1f8f3f"
            text.append(f" {i} {name} ", style=style)
            text.append("  ")
        super().__init__(text, id="steps")


class WelcomeScreen(Screen):
    """Typed-out banner. Any key skips it; it also moves on by itself."""

    TICK = 0.05  # seconds per animation frame (tests make it tiny)
    REVEAL_TICKS = 4  # frames per line of the banner
    HOLD_TICKS = 80  # pause (about 4 s) after everything is on screen before moving on

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Static("", id="art")
            yield Static("", id="tagline")
            yield Static("", id="subline")
            yield Static("", id="hint")
            yield Static("", id="credits")

    def on_mount(self):
        size = self.app.size
        if size.width >= art.STACKED_WIDTH + 6 and size.height >= art.STACKED_HEIGHT + 10:
            self.art_used, banner = "stacked", art.ART_STACKED
        elif size.width >= art.BIG_WIDTH + 4:
            self.art_used, banner = "big", art.ART_BIG
        else:
            self.art_used, banner = "small", art.ART_SMALL
        lines = banner.strip("\n").split("\n")
        art_width = max(len(line) for line in lines)
        box_width = min(
            max(74, art_width), max(art_width, size.width)
        )  # never wider than the terminal
        self.query_one("#welcome-box").styles.width = box_width
        pad = " " * ((box_width - art_width) // 2)  # centre the banner as one block
        self.lines = [pad + line for line in lines]
        self.tick = 0
        self.done = False
        self.query_one("#hint", Static).update("press any key to skip")
        self.timer = self.set_interval(self.TICK, self._frame)

    def _frame(self):
        self.tick += 1
        art_ticks = self.REVEAL_TICKS * len(self.lines)
        shown = min(len(self.lines), self.tick // self.REVEAL_TICKS)
        self.query_one("#art", Static).update("\n".join(self.lines[:shown]))
        typed = max(0, self.tick - art_ticks)  # one letter per frame
        self.query_one("#tagline", Static).update(art.TAGLINE[:typed])
        if typed >= len(art.TAGLINE):
            self.query_one("#subline", Static).update(art.SUBLINE)
            self.query_one("#credits", Static).update(f"by {AUTHOR}  -  built with Claude")
            if typed - len(art.TAGLINE) > self.HOLD_TICKS:
                self.finish()

    def on_key(self, event):
        event.stop()
        self.finish()

    def finish(self):
        if not self.done:
            self.done = True
            self.timer.stop()
            self.app.switch_screen(SourceScreen())


class SourceScreen(Screen):
    BINDINGS = [
        ("r", "rescan", "Rescan disks"),
        ("d", "cache", "Clear scan cache"),
        ("a", "about", "About"),
        ("question_mark", "help", "Help"),
        ("q", "app.quit", "Quit"),
    ]

    def action_cache(self):
        self.app.push_screen(CacheModal())

    def compose(self) -> ComposeResult:
        yield StepBar(1)
        yield Static("Time Machine backups found", classes="heading")
        yield OptionList(id="volumes")
        yield Static("", id="message", classes="note")
        yield Footer()

    def on_mount(self):
        self.volumes = {}
        self.action_rescan()

    def action_rescan(self):
        self._t0 = time.time()
        self._looking = True
        self._tick()
        self._timer = self.set_interval(1, self._tick)
        self.run_worker(self._discover, thread=True, exclusive=True)

    def _tick(self):
        if self._looking:
            secs = int(time.time() - self._t0)
            text = f"Looking for backup disks... {secs}s"
            if secs >= 4:
                text += (
                    "\n(a disk that is asleep or busy with a Time Machine copy can take a while)"
                )
            self.query_one("#message", Static).update(text)

    def _discover(self):
        found = self.app.discover()
        self.app.call_from_thread(self._show, *found)

    def _show(self, volumes, legacy):
        self._looking = False
        self._timer.stop()
        ol = self.query_one("#volumes", OptionList)
        ol.clear_options()
        volumes = sorted(volumes, key=lambda v: not v.supported)  # usable disks first
        self.app.backup_volumes = list(volumes)  # step 3 must never write into any of them
        self.volumes = {v.device: v for v in volumes}
        for v in volumes:
            ol.add_option(Option(self._prompt(v), id=v.device, disabled=not v.supported))
        usable = [i for i, v in enumerate(volumes) if v.supported]
        if usable:
            ol.highlighted = usable[0]
            ol.focus()
        notes = []
        if not usable:
            notes.append(
                "No usable Time Machine disk found. Connect the USB disk that holds your "
                "backup, unlock it if it is encrypted, and press r."
            )
        if legacy:
            notes.append(
                f"Old-style (HFS+) Time Machine backup on: {', '.join(legacy)} - not supported."
            )
        self.query_one("#message", Static).update("\n".join(notes))

    @staticmethod
    def _prompt(v):
        t = Text()
        t.append(v.name, style="bold")
        if v.supported:
            first, last = date_of(v.snapshots[0]), date_of(v.snapshots[-1])
            t.append(f"\n  {v.bus} - {fmt_bytes(v.used_bytes)} used - {len(v.snapshots)} snapshots")
            t.append(f" - {first} to {last}", style="#66ff99")
        else:
            t.append(f"\n  can't be used: {v.note}", style="#ffb000")
        return t

    def on_option_list_option_selected(self, event):
        volume = self.volumes.get(event.option_id)
        if volume and volume.supported:
            self.app.source = volume
            self.app.push_screen(BackupsScreen())

    def action_about(self):
        self.app.push_screen(AboutScreen())

    def action_help(self):
        self.notify(
            "Up/Down move, Enter selects, r rescans the disks, q quits.\n"
            "Only local USB disks with Time Machine snapshots can be a source.",
            title="Help",
        )


class AboutScreen(ModalScreen):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("a", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="about-box"):
            yield Static(f"SmartTimeArchive  {__version__}", id="about-title")
            yield Static(
                f"\nCreated by {AUTHOR}\n"
                "Built together with Claude (Anthropic), using Claude Code\n\n"
                f"{REPO}\nSupport the project: {SUPPORT}\n\n"
                f"{DISCLAIMER}\n\n[Esc] close",
                markup=False,
            )


def nice_date(name):
    """'2026-09-18-194541' -> '2026-09-18  19:45:41'"""
    return f"{name[:10]}  {name[11:13]}:{name[13:15]}:{name[15:17]}"


class BackupsScreen(Screen):
    """Step 2: which backups (dated folders) to archive. The user chooses; nothing starts marked."""

    BINDINGS = [
        ("a", "all", "Mark all"),
        ("n", "none", "Unmark all"),
        ("c", "continue", "Continue"),
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        src = self.app.source
        yield StepBar(2)
        yield Static(f"Which backups do you want to archive from {src.name}?", classes="heading")
        yield Static(
            "Your Time Machine disk is not touched: this only makes a copy.\n"
            "Space bar marks or unmarks one backup.  a marks all,  n unmarks all.",
            classes="dim",
        )
        yield SelectionList(*[Selection(nice_date(d), d, False) for d in src.snapshots], id="dates")
        yield Static("", id="summary", classes="note")
        yield Footer()

    def on_mount(self):
        self.query_one("#dates", SelectionList).focus()
        self._summary()

    def on_selection_list_selected_changed(self, event):
        self._summary()

    @property
    def dates(self):
        return self.query_one("#dates", SelectionList)

    def _summary(self):
        chosen = sorted(self.dates.selected)
        total = len(self.app.source.snapshots)
        if chosen:
            span = f"  ({chosen[0][:10]} to {chosen[-1][:10]})"
            text = f"{len(chosen)} of {total} backups marked{span}"
        else:
            text = f"No backup marked (0 of {total}) - mark at least one to continue"
        self.query_one("#summary", Static).update(text)

    def action_all(self):
        self.dates.select_all()

    def action_none(self):
        self.dates.deselect_all()

    def action_continue(self):
        chosen = sorted(self.dates.selected)
        if not chosen:
            self.notify("Mark at least one backup first.", severity="warning")
            return
        self.app.dates = chosen
        self.app.push_screen(DestinationScreen())


class DestinationScreen(Screen):
    """Step 3: which disk or network unit receives the archive. Nothing is chosen for the user."""

    BINDINGS = [
        ("r", "rescan", "Refresh"),
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield StepBar(3)
        yield Static("Where do you want to put the archive?", classes="heading")
        yield Static(
            "Pick a disk or network unit; you choose the folder inside it next.\n"
            "Your Time Machine disk is never written to.",
            classes="dim",
        )
        yield OptionList(id="dests")
        yield Static("", id="message", classes="note")
        yield Footer()

    def on_mount(self):
        self.dests = {}
        self.action_rescan()

    def action_rescan(self):
        self.query_one("#message", Static).update("Looking for disks...")
        self.run_worker(self._discover, thread=True, exclusive=True)

    def _discover(self):
        sources = self.app.backup_volumes or ([self.app.source] if self.app.source else [])
        found = self.app.destinations(sources)
        self.app.call_from_thread(self._show, found)

    def _show(self, dests):
        ol = self.query_one("#dests", OptionList)
        ol.clear_options()
        dests = sorted(dests, key=lambda d: d.is_backup_source)  # usable ones first
        self.dests = {d.path: d for d in dests}
        for d in dests:
            ol.add_option(Option(self._prompt(d), id=d.path, disabled=d.is_backup_source))
        if dests:
            ol.highlighted = 0
            ol.focus()
        self.query_one("#message", Static).update("" if dests else "No disks found.")

    @staticmethod
    def _prompt(d):
        t = Text()
        t.append(d.name, style="bold")
        kind = "network" if d.network else (d.fs or "?")
        t.append(f"\n  {kind} - {fmt_bytes(d.free_bytes)} free")
        if d.is_backup_source:
            t.append(
                "\n  this is the Time Machine source: it cannot receive the archive",
                style="#ffb000",
            )
            return t
        if not d.hardlinks:
            t.append(" - no hard links: an APFS disk image can be created inside", style="#ffb000")
        if d.archive_dates:
            t.append(
                f"\n  an archive is already here ({len(d.archive_dates)} dates)", style="#66ff99"
            )
        return t

    def on_option_list_option_selected(self, event):
        dest = self.dests.get(event.option_id)
        if dest and not dest.is_backup_source:
            self.app.destination = dest
            self.app.push_screen(FolderScreen(dest))


class DirsOnly(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith(".")]


class ImageChoice(ModalScreen):
    """The destination cannot hold hard links: ask, do not decide."""

    BINDINGS = [
        ("y", "yes", "Create image"),
        ("n", "no", "Store in full"),
        ("escape", "back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-box"):
            yield Static("This destination cannot keep hard links", classes="dialog-title")
            yield Static(
                "\nWithout them every backup is stored in full and the archive takes much more space.\n"
                "An APFS disk image created inside this folder keeps the space savings.\n\n"
                "[y] create the disk image (recommended)\n"
                "[n] store everything in full\n"
                "[Esc] go back",
                markup=False,  # otherwise Textual eats "[y]" as a style tag
            )

    def action_yes(self):
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)

    def action_back(self):
        self.dismiss(None)


class NewFolder(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, parent_path):
        super().__init__()
        self.parent_path = parent_path

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-box"):
            yield Static("New folder inside", classes="dialog-title")
            yield Static(self.parent_path, classes="dim")
            yield Input(placeholder="folder name", id="folder-name")
            yield Static("Enter creates it - Esc cancels", classes="dim")

    def on_mount(self):
        self.query_one("#folder-name", Input).focus()

    def on_input_submitted(self, event):
        name = event.value.strip()
        target = os.path.join(self.parent_path, name)
        if not name or "/" in name or name.startswith("."):
            self.notify("Type a plain folder name (no slashes, not hidden).", severity="warning")
        elif os.path.exists(target):
            self.notify("That folder already exists.", severity="warning")
        else:
            self.dismiss(name)

    def action_cancel(self):
        self.dismiss(None)


class FolderScreen(Screen):
    """Step 3b: the folder inside the chosen disk that will hold the dated backups."""

    BINDINGS = [
        ("u", "use", "Use this folder"),
        ("m", "mkdir", "New folder"),
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, dest):
        super().__init__()
        self.dest = dest

    def compose(self) -> ComposeResult:
        yield StepBar(3)
        yield Static(f"Which folder inside {self.dest.name}?", classes="heading")
        yield Static(
            "Enter opens a folder.  u uses the highlighted one (the top line is the disk itself).\n"
            "m creates a new folder inside the highlighted one.",
            classes="dim",
        )
        yield DirsOnly(self.dest.path, id="tree")
        yield Static("", id="chosen", classes="note")
        yield Footer()

    def on_mount(self):
        self.query_one("#tree", DirsOnly).focus()
        self._show_highlight()

    def _highlighted_path(self):
        node = self.query_one("#tree", DirsOnly).cursor_node
        data = getattr(node, "data", None)
        return str(data.path) if data else self.dest.path

    def _show_highlight(self):
        self.query_one("#chosen", Static).update(f"Highlighted: {self._highlighted_path()}")

    def on_tree_node_highlighted(self, event):
        self._show_highlight()

    def action_mkdir(self):
        self.app.push_screen(NewFolder(self._highlighted_path()), self._made)

    def _made(self, name):
        if name:
            path = os.path.join(self._highlighted_path(), name)
            os.makedirs(path)
            self.query_one("#tree", DirsOnly).reload()
            self.notify(f"Created {name}")

    def action_use(self):
        path = self._highlighted_path()
        if self.dest.hardlinks:
            self._finish(path, False)
        else:
            self.app.push_screen(ImageChoice(), lambda choice: self._image(path, choice))

    def _image(self, path, choice):
        if choice is not None:
            self._finish(path, choice)

    def _finish(self, path, image):
        self.app.dest_path, self.app.dest_image = path, image
        dates, _ = discover.existing_archive(path)
        if dates and not image:  # an image is always a new one: nothing to skip inside it
            self.notify(f"An archive is already here ({len(dates)} dates): those are skipped.")
        self.app.push_screen(ScanScreen() if privileges.has_ticket() else PasswordScreen())


class CacheModal(ModalScreen):
    """Offers to delete the scan cache. It only speeds up the next scan: never touches archives."""

    BINDINGS = [("y", "yes", "Yes"), ("n", "no", "No"), ("escape", "no", "Cancel")]

    def compose(self) -> ComposeResult:
        size = core.cache_size()
        with Vertical(classes="dialog-box"):
            yield Static("Scan cache", classes="dialog-title")
            if size:
                yield Static(
                    f"\nThe scan cache takes {fmt_bytes(size)}.\n"
                    "It only makes the next scan faster and can be rebuilt at any time.\n"
                    "Your archives are not touched.\n\n"
                    "Delete it?  y = yes   n = no",
                    markup=False,
                )
            else:
                yield Static("\nThere is no scan cache: nothing to delete.\n\nEsc closes.")

    def action_yes(self):
        if core.cache_size():
            self.app.notify(f"Deleted the scan cache ({fmt_bytes(core.clear_cache())} freed).")
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)


class PasswordEntry:
    """Masked password field shared by the step and the pop-up. The password goes to sudo and is
    dropped: it is not kept in a variable of ours, not shown and not written anywhere."""

    def _field(self):
        return Input(password=True, placeholder="administrator password", id="pw")

    def on_input_submitted(self, event):
        if event.input.id != "pw":
            return
        password, event.input.value = event.value, ""
        if not password:
            return
        event.input.disabled = True
        self.query_one("#result", Static).update("Checking...")
        self.run_worker(lambda: self._try(password), thread=True, exclusive=True)

    def _try(self, password):
        ok = self.app.ask_password(password) and privileges.has_ticket()
        self.app.call_from_thread(self._answered, ok)

    def _answered(self, ok):
        field = self.query_one("#pw", Input)
        field.disabled = False
        if ok:
            self._granted()
        else:
            self.query_one("#result", Static).update("That password was not accepted. Try again.")
            field.focus()


class PasswordScreen(PasswordEntry, Screen):
    """Step 4a: explain why administrator access is needed and ask for it, right here."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield StepBar(4)
        yield Static("Administrator access", classes="heading")
        yield Static(
            "The engine needs your administrator password to mount the Time Machine\n"
            "snapshots and to read every user's files.\n\n"
            "  - It is only handed to sudo to unlock the engine: this program does not\n"
            "    keep it or write it anywhere.\n"
            "  - This program keeps running as your normal user.\n"
            "  - Nothing is written to, or deleted from, your Time Machine disk.\n"
            "  - Esc goes back at any moment.",
            classes="dim",
        )
        yield self._field()
        yield Static("Type it and press Enter.", id="result", classes="note")
        yield Footer()

    def on_mount(self):
        self.query_one("#pw", Input).focus()

    def _granted(self):
        self.app.push_screen(ScanScreen())


class PasswordModal(PasswordEntry, ModalScreen):
    """The sudo ticket expired in the middle of the wizard: ask again without leaving the TUI."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-box"):
            yield Static("Administrator password needed again", classes="dialog-title")
            yield Static("The previous authorization expired.", classes="dim")
            yield self._field()
            yield Static("Type it and press Enter - Esc cancels", id="result", classes="dim")

    def on_mount(self):
        self.query_one("#pw", Input).focus()

    def _granted(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)


class ScanScreen(Screen):
    """Step 4b: warn about the time, scan with progress (cancel), show the plan."""

    BINDINGS = [
        ("y", "yes", "Yes"),
        ("n", "no", "No"),
        ("c", "cancel", "Cancel"),
        ("enter", "next", "Continue"),
        ("escape", "back", "Back"),
        ("q", "quit_app", "Quit"),
    ]

    STEP = 4
    TITLE = "Checking the disk space"
    NOUN = "scan"
    CANCELLED_TEXT = (
        "Cancelled. Nothing was copied and your Time Machine disk was not changed.\n"
        "Esc goes back."
    )

    def __init__(self):
        super().__init__()
        self.state = "checking"  # checking > confirm > running > cancelling > plan/failed/cancelled
        self.run_ = None
        self.keepalive = None
        self.t0 = 0.0
        self.progress = {}
        self.ask_cancel = False

    def compose(self) -> ComposeResult:
        app = self.app
        yield StepBar(self.STEP)
        yield Static(self.TITLE, classes="heading")
        yield Static(
            f"From:  {app.source.name}  ({len(app.dates)} backups)\n"
            f"To:    {app.dest_path}"
            + ("   (inside an APFS disk image)" if app.dest_image else ""),
            classes="dim",
        )
        yield Static("", id="body", classes="note")
        yield Footer()

    def on_mount(self):
        self.set_interval(1.0, self._refresh)
        self._set("Looking at what was scanned before...")
        self.run_worker(self._check, thread=True, exclusive=True)

    def _set(self, text):
        self.query_one("#body", Static).update(text)

    # -- before: warn and ask ------------------------------------------------
    def _check(self):
        try:
            src = self.app.scan_source(self.app.source.mountpoint)
            pending = core.uncached_dates(src, self.app.dates, core.Options())
        except Exception:  # unknown: assume the worst rather than promise it is quick
            pending = list(self.app.dates)
        self.app.call_from_thread(self._ask, len(pending))

    def _ask(self, pending):
        total = len(self.app.dates)
        self.state = "confirm"
        if pending:
            text = (
                f"{pending} of {total} backups were never scanned. Reading every file's\n"
                "information can take several minutes per backup on an old or slow disk.\n"
                "It happens once: the result is kept for next time.\n"
                "While it runs you can cancel with c."
            )
        else:
            text = f"All {total} backups were scanned before, so this should be quick."
        self._set(text + "\n\nContinue?  y = yes   n = no")

    # -- running -------------------------------------------------------------
    def _args(self):
        app = self.app
        args = ["plan", app.source.mountpoint, app.dest_path, "--dates", ",".join(app.dates)]
        return args + (["--dest-image"] if app.dest_image else [])

    def action_yes(self):
        if self.state == "confirm":
            self._start()
        elif self.ask_cancel:
            self.ask_cancel = False
            self.state = "cancelling"
            self.run_.cancel()
            self._paint()
        # y means nothing anywhere else: never act by accident

    def action_no(self):
        if self.state == "confirm":
            self.app.pop_screen()
        elif self.ask_cancel:
            self.ask_cancel = False
            self._paint()

    def _start(self):
        if not privileges.has_ticket():  # the sudo ticket may have expired since the last step
            self.app.push_screen(PasswordModal(), lambda ok: ok and self._start())
            return
        self.state, self.t0 = "running", time.time()
        self.app.plan = None
        self.keepalive = privileges.Keepalive()
        self.keepalive.start()
        self.run_ = self.app.make_engine(
            self._args(), self._on_event, self._on_exit, **self._engine_kw()
        )
        self.run_.start()
        self._paint()

    def _engine_kw(self):
        return {}

    def _on_event(self, ev):
        self.app.call_from_thread(self._event, ev)

    def _event(self, ev):
        kind = ev.get("event")
        if kind in ("scan_snapshot", "scan_progress", "scan_eta"):
            self.progress.update(ev)
        elif kind == "plan":
            self.app.plan = ev
        elif kind == "error":
            self.tail = [ev.get("message", "")]
        self._paint()

    def _on_exit(self, code, tail):
        self.app.call_from_thread(self._exit, code, tail)

    def _exit(self, code, tail):
        if self.keepalive:
            self.keepalive.stop()
        if self.state == "cancelling" or code == 130:
            self.state = "cancelled"
        elif code in (0, 2) and getattr(self.app, "plan", None):
            self.state = "plan"
        else:
            self.state = "failed"
            self.tail = getattr(self, "tail", None) or tail
        self._paint()

    def _refresh(self):
        if self.state in ("running", "cancelling"):
            self._paint()

    def action_cancel(self):
        if self.state == "running":
            self.ask_cancel = True
            self._paint()

    def action_next(self):
        if self.state == "plan" and self.app.plan["fits"]:
            self.app.push_screen(CopyScreen())

    def action_back(self):
        if self.state in ("confirm", "plan", "failed", "cancelled"):
            self.app.pop_screen()

    def action_quit_app(self):
        if self.state not in ("running", "cancelling"):
            self.app.exit()
        else:
            self.notify(f"Cancel first (c): a {self.NOUN} is running.", severity="warning")

    def _paint(self):
        st = self.state
        if st == "running":
            p = self.progress
            mins = int((time.time() - self.t0) // 60)
            secs = int((time.time() - self.t0) % 60)
            lines = [f"Scanning...  {mins}:{secs:02d} elapsed"]
            if p.get("date"):
                lines.append(f"Backup {p.get('i', '?')} of {p.get('n', '?')}: {nice_date(p['date'])}")
            if p.get("entries"):
                lines.append(f"{p['entries']:,} entries read in this backup")
            if p.get("minutes_left"):
                lines.append(f"About {p['minutes_left']} minutes left")
            if self.ask_cancel:
                lines.append(f"\nCancel the {self.NOUN}?  y = yes, cancel   n = no, keep going")
            else:
                lines.append("\nc cancels")
            self._set("\n".join(lines))
        elif st == "cancelling":
            self._set("Cancelling... unmounting the backups safely, one moment.")
        elif st == "cancelled":
            self._set(self.CANCELLED_TEXT)
        elif st == "failed":
            detail = "\n".join(getattr(self, "tail", None) or ["(no details)"])
            self._set(f"The {self.NOUN} failed.\n\n{detail}\n\nEsc goes back.")
        elif st == "plan":
            self._set(plan_text(self.app.plan))

    def on_unmount(self):
        if self.keepalive:
            self.keepalive.stop()


def plan_text(plan):
    need, free = plan["bytes_needed_with_margin"], plan["dest_free"]
    lines = [
        f"Backups selected:        {plan['snapshots']}",
        f"Files:                   {plan['files']:,}  ({plan['unique_files']:,} unique)",
        f"Size WITH hard links:    {fmt_bytes(plan['bytes_with_links'])}",
        f"Size WITHOUT hard links: {fmt_bytes(plan['bytes_without_links'])}",
        f"Space needed (+margin):  {fmt_bytes(need)}",
        f"Free on the destination: {fmt_bytes(free)}",
        "",
    ]
    if plan.get("image_capacity"):
        size = fmt_bytes(plan["image_capacity"])
        lines.insert(-1, f"New disk image size:    {size} (adaptive: it only takes what is written)")
    if plan["fits"]:
        lines.append("It fits.  Enter continues to the copy.  Esc goes back.")
    else:
        lines.append(
            f"It does NOT fit: {fmt_bytes(need - free)} more space is needed.\n"
            "Nothing can continue from here. Esc goes back to pick another destination."
        )
    return "\n".join(lines)


class CopyScreen(ScanScreen):
    """Step 5: the real copy. Same cancel control as the scan; nothing starts by itself."""

    BINDINGS = ScanScreen.BINDINGS + [("k", "awake", "Keep Mac awake")]
    STEP = 5
    TITLE = "Copying the archive"
    NOUN = "copy"
    CANCELLED_TEXT = (
        "Cancelled. The backup being copied was left as a .partial folder; the ones already\n"
        "finished are kept. Your Time Machine disk was not changed.  Esc goes back."
    )

    def __init__(self):
        super().__init__()
        self.awake = False  # the user turns it on; it is never chosen for them
        self.done_bytes = self.linked = self.errors = 0
        self.finished = None
        self.tail = []

    def on_mount(self):
        self.set_interval(1.0, self._refresh)
        self._ask(0)

    def _ask(self, _pending=0):
        self.state = "confirm"
        self._confirm_text()

    def _confirm_text(self):
        plan = self.app.plan
        lines = [
            f"{plan['snapshots']} backups, about {fmt_bytes(plan['bytes_needed'])} will be written.",
            "Your Time Machine disk is only read, never changed.",
            "A large archive can take hours.",
            "",
            f"({'x' if self.awake else ' '}) k  Keep this Mac awake while copying (uses caffeinate,"
            " built into macOS)",
        ]
        if not os.environ.get("TMUX"):
            lines += [
                "",
                "Tip: you are not inside tmux. Closing this terminal window stops the copy.",
            ]
        lines += ["", "Start copying?  y = yes   n = no"]
        self._set("\n".join(lines))

    def action_awake(self):
        if self.state == "confirm":
            self.awake = not self.awake
            self._confirm_text()

    def _engine_kw(self):
        return {"keep_awake": self.awake}

    def _args(self):
        app = self.app
        args = ["extract", app.source.mountpoint, app.dest_path, "--dates", ",".join(app.dates)]
        if app.dest_image:
            args.append("--dest-image")
        elif not app.destination.hardlinks:
            args.append("--yes")  # the user chose "store everything in full" for this disk
        return args

    def _event(self, ev):
        kind = ev.get("event")
        p = self.progress
        if kind in ("extract_start", "date_skipped"):
            p.update(ev)
            p["done"], p["total"], p["bytes_copied"] = 0, ev.get("entries", 0), 0
        elif kind == "extract_progress":
            p.update(ev)
        elif kind == "date_done":
            self.done_bytes += ev.get("bytes_copied", 0)
            self.linked += ev.get("linked", 0)
            self.errors += ev.get("errors", 0)
            p.update(ev)
            p["bytes_copied"] = 0  # already counted in done_bytes
        elif kind == "image":
            p["image"] = ev["path"]
        elif kind == "warning":
            p["warning"] = ev.get("message")
        elif kind == "finished":
            self.finished = ev
        elif kind == "error":
            self.tail = [ev.get("message", "")]
        self._paint()

    def _exit(self, code, tail):
        if self.keepalive:
            self.keepalive.stop()
        if self.state == "cancelling" or code == 130:
            self.state = "cancelled"
        elif code in (0, 3) and self.finished:
            self.state = "done"
        else:
            self.state = "failed"
            self.tail = self.tail or tail
        self._paint()

    def _paint(self):
        if self.state == "confirm":
            return self._confirm_text()
        if self.state != "running" and self.state != "done":
            return super()._paint()
        p = self.progress
        if self.state == "done":
            f = self.finished
            ok = f["status"] == "COMPLETED"
            self._set(
                ("Done." if ok else "Done, with some files that could not be copied.")
                + f"\nStatus: {f['status']}\n"
                f"Copied: {fmt_bytes(self.done_bytes)}\n"
                f"Report: {f.get('report_file')}\n\n"
                "Enter continues."
            )
            return
        secs = int(time.time() - self.t0)
        lines = [f"Copying...  {secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d} elapsed"]
        if p.get("date"):
            lines.append(f"Backup {p.get('i', '?')} of {p.get('n', '?')}: {nice_date(p['date'])}")
            lines.append(f"{p.get('done', 0):,} of {p.get('total', 0):,} entries")
            lines.append(
                f"{fmt_bytes(self.done_bytes + p.get('bytes_copied', 0))} copied, "
                f"{p.get('linked', 0):,} linked in this backup"
            )
            if p.get("errors"):
                lines.append(f"{p['errors']:,} files could not be copied (they go in the report)")
        else:
            lines.append("Preparing (reading the scan)...")
        if p.get("warning"):
            lines.append(f"Warning: {p['warning']}")
        if self.ask_cancel:
            lines.append("\nCancel the copy?  y = yes, cancel   n = no, keep going")
        else:
            lines.append("\nc cancels")
        self._set("\n".join(lines))

    def action_back(self):
        if self.state in ("confirm", "failed", "cancelled"):
            self.app.pop_screen()

    def action_next(self):
        if self.state == "done":
            self.app.push_screen(
                ReportScreen(
                    self.finished, self.done_bytes, self.linked, self.errors, len(self.app.dates)
                )
            )


class ReportScreen(ScanScreen):
    """Step 6: the outcome, and an optional verification. The user decides; it can take a while."""

    BINDINGS = ScanScreen.BINDINGS + [("m", "restart", "Start over"), ("d", "cache", "Clear cache")]
    STEP = 6
    TITLE = "Report"
    NOUN = "verification"
    CANCELLED_TEXT = "Verification cancelled. The copy itself is untouched.  m starts over,  q quits."
    ENDS = ("skipped", "verified", "failed", "cancelled")

    def __init__(self, finished, copied_bytes, linked, errors, backups):
        super().__init__()
        self.finished, self.copied_bytes = finished, copied_bytes
        self.linked, self.errors, self.backups = linked, errors, backups
        self.result = None

    def on_mount(self):
        self.set_interval(1.0, self._refresh)
        self._ask(0)

    def _summary(self):
        f = self.finished
        ok = f["status"] == "COMPLETED"
        lines = [
            "The copy finished." if ok else "The copy finished, but some files could not be copied.",
            f"Status:   {f['status']}",
            f"Backups:  {self.backups}",
            f"Copied:   {fmt_bytes(self.copied_bytes)}   ({self.linked:,} files linked, not copied)",
        ]
        if self.errors:
            lines.append(f"Errors:   {self.errors:,} files - the list is in the report")
        if f.get("image"):
            lines.append(f"Image:    {f['image']}")
        lines.append(f"Report:   {f.get('report_file')}")
        cache = core.cache_size()
        if cache:
            lines.append(
                f"\nThe scan cache takes {fmt_bytes(cache)}. Press d to delete it if you will not "
                "use the tool again soon."
            )
        return lines

    def _ask(self, _pending=0):
        self.state = "confirm"
        self._paint()

    def _args(self):
        return ["verify", self.finished.get("image") or self.app.dest_path]

    def _event(self, ev):
        kind = ev.get("event")
        if kind == "verify_progress":
            self.progress.update(ev)
        elif kind == "verified":
            self.result = ev
        elif kind == "error":
            self.tail = [ev.get("message", "")]
        self._paint()

    def _exit(self, code, tail):
        if self.keepalive:
            self.keepalive.stop()
        if self.state == "cancelling" or code == 130:
            self.state = "cancelled"
        elif self.result:
            self.state = "verified"
        else:
            self.state = "failed"
            self.tail = self.tail or tail
        self._paint()

    def action_no(self):
        if self.state == "confirm":
            self.state = "skipped"
            self._paint()
        elif self.ask_cancel:
            self.ask_cancel = False
            self._paint()

    def action_back(self):
        pass

    def action_next(self):
        pass

    def action_cache(self):
        if self.state not in ("running", "cancelling"):
            self.app.push_screen(CacheModal(), lambda _: self._paint())

    def action_restart(self):
        if self.state in self.ENDS:  # never while a verification is running
            self.app.call_later(self.app.restart)

    def _paint(self):
        st = self.state
        lines = self._summary()
        if st == "confirm":
            lines += [
                "",
                "Do you want to verify the copy?",
                "It re-reads every file and compares it with its checksum. This can take a",
                "while: a fast SSD with 80 GB took about 4 minutes; a slow or network disk",
                "can take much longer. You can cancel it with c.",
                "",
                "Verify?  y = yes   n = no",
            ]
        elif st == "skipped":
            lines += ["", "Not verified.  m starts over,  q quits."]
        elif st == "running":
            p = self.progress
            secs = int(time.time() - self.t0)
            lines += ["", f"Verifying...  {secs // 60}:{secs % 60:02d} elapsed"]
            if p.get("total"):
                lines.append(f"{p['done']:,} of {p['total']:,} entries checked")
            lines.append(
                f"\nCancel the verification?  y = yes   n = no" if self.ask_cancel else "\nc cancels"
            )
        elif st == "cancelling":
            lines += ["", "Cancelling..."]
        elif st == "cancelled":
            lines = [self.CANCELLED_TEXT]
        elif st == "failed":
            detail = "\n".join(self.tail or ["(no details)"])
            lines += ["", f"The verification failed.\n{detail}", "", "m starts over,  q quits."]
        elif st == "verified":
            r = self.result
            lines += ["", f"Verification: {r['status']}",
                      f"{r['hashed']:,} files re-read, {r['entries']:,} entries checked."]  # fmt: skip
            if r["status"] == "VERIFIED":
                lines.append("Every file matches its checksum.")
            else:
                lines.append(
                    f"Different: {r['mismatches']}   missing: {r['missing']}   "
                    f"not readable: {r['unreadable']}"
                )
                lines += [f"  {e}" for e in r.get("examples", [])]
            lines += ["", "m starts over,  q quits."]
        self._set("\n".join(lines))


class StaApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "SmartTimeArchive"

    def __init__(
        self, discover=discover_all, skip_welcome=False, destinations=discover_destinations
    ):
        super().__init__()
        self.discover = discover
        self.destinations = destinations
        self.skip_welcome = skip_welcome
        self.source = None
        self.backup_volumes = []
        self.dates = []
        self.destination = None
        self.dest_path = None
        self.dest_image = False
        self.plan = None

    def restart(self):
        """Back to step 1 with nothing carried over (another disk, or more folders elsewhere)."""
        self.source, self.dates, self.destination = None, [], None
        self.dest_path, self.dest_image, self.plan = None, False, None
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(SourceScreen())

    def scan_source(self, mountpoint):
        return core.ApfsSource(mountpoint)

    def make_engine(self, args, on_event, on_exit, **kw):
        return engine.EngineRun(args, on_event, on_exit, **kw)

    def ask_password(self, password):
        return privileges.ask(password)

    def on_mount(self):
        self.push_screen(SourceScreen() if self.skip_welcome else WelcomeScreen())


def run():
    StaApp().run()
