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

from .. import AUTHOR, DISCLAIMER, REPO, SUPPORT, __version__, discover
from . import art, privileges

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
        ("a", "about", "About"),
        ("question_mark", "help", "Help"),
        ("q", "app.quit", "Quit"),
    ]

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
        if dates:
            self.notify(f"An archive is already here ({len(dates)} dates): those are skipped.")
        self.app.push_screen(PasswordScreen())


class PasswordScreen(Screen):
    """Step 4a: explain why administrator access is needed, then let sudo ask for it."""

    BINDINGS = [
        ("enter", "continue", "Continue"),
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield StepBar(4)
        yield Static("Administrator access", classes="heading")
        yield Static(
            "The engine needs your administrator password to mount the Time Machine\n"
            "snapshots and to read every user's files.\n\n"
            "  - This program keeps running as your normal user.\n"
            "  - macOS asks for the password itself; this program never sees or stores it.\n"
            "  - Nothing is written to, or deleted from, your Time Machine disk.\n"
            "  - You can cancel or go back at any moment.",
            classes="dim",
        )
        yield Static("", id="result", classes="note")
        yield Footer()

    def action_continue(self):
        result = self.query_one("#result", Static)
        if privileges.has_ticket():
            self.app.push_screen(ScanScreen())
            return
        with self.app.suspend():
            print("\nSmartTimeArchive needs your administrator password (asked by sudo).\n")
            ok = self.app.ask_password()
        if ok and privileges.has_ticket():
            self.app.push_screen(ScanScreen())
        else:
            result.update("The password was not accepted or was cancelled. Press Enter to try again.")


class ScanScreen(Screen):
    """Step 4 (placeholder until the next iteration)."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        app = self.app
        yield StepBar(4)
        yield Static("Ready to check the space", classes="heading")
        yield Static(
            f"From:  {app.source.name}  ({len(app.dates)} backups)\n"
            f"To:    {app.dest_path}"
            + ("   (inside an APFS disk image)" if app.dest_image else ""),
            classes="dim",
        )
        yield Static("The scan and the size check come next.", classes="dim")
        yield Footer()


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

    def ask_password(self):
        return privileges.ask()

    def on_mount(self):
        self.push_screen(SourceScreen() if self.skip_welcome else WelcomeScreen())


def run():
    StaApp().run()
