"""SmartTimeArchive terminal UI: a linear wizard on top of the engine."""

import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from .. import AUTHOR, DISCLAIMER, REPO, SUPPORT, __version__, discover
from . import art

STEPS = ["Source", "Backups", "Destination", "Scan", "Run", "Report"]


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1000


def date_of(snapshot_name):
    return snapshot_name[:10]


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

    TICK = 0.04  # seconds per animation frame (tests make it tiny)
    HOLD_TICKS = 32  # pause after everything is on screen before moving on

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Static("", id="art")
            yield Static("", id="tagline")
            yield Static("", id="subline")
            yield Static("", id="hint")
            yield Static(f"by {AUTHOR}  -  built with Claude", id="credits")

    def on_mount(self):
        width = self.app.size.width
        self.art_used = "big" if width >= art.BIG_WIDTH + 4 else "small"
        self.lines = (
            (art.ART_BIG if self.art_used == "big" else art.ART_SMALL).strip("\n").split("\n")
        )
        self.tick = 0
        self.done = False
        self.timer = self.set_interval(self.TICK, self._frame)

    def _frame(self):
        self.tick += 1
        shown = min(len(self.lines), self.tick // 3)
        self.query_one("#art", Static).update("\n".join(self.lines[:shown]))
        typed = max(0, (self.tick - 3 * len(self.lines)) * 2)
        self.query_one("#tagline", Static).update(art.TAGLINE[:typed])
        if typed >= len(art.TAGLINE):
            self.query_one("#subline", Static).update(art.SUBLINE)
            blink = (self.tick // 12) % 2 == 0
            self.query_one("#hint", Static).update("press any key" if blink else "")
            if self.tick - (3 * len(self.lines) + len(art.TAGLINE) // 2) > self.HOLD_TICKS:
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
                f"{DISCLAIMER}\n\n[Esc] close"
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
    """Step 3 (placeholder until the next iteration)."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield StepBar(3)
        yield Static(f"{len(self.app.dates)} backups selected", classes="heading")
        yield Static("Choosing the destination comes next.", classes="dim")
        yield Footer()


class StaApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "SmartTimeArchive"

    def __init__(self, discover=discover_all, skip_welcome=False):
        super().__init__()
        self.discover = discover
        self.skip_welcome = skip_welcome
        self.source = None
        self.dates = []

    def on_mount(self):
        self.push_screen(SourceScreen() if self.skip_welcome else WelcomeScreen())


def run():
    StaApp().run()
