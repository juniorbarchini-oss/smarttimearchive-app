"""SmartTimeArchive terminal UI: a linear wizard on top of the engine."""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import discover
from . import art

STEPS = ["Source", "Contents", "Destination", "Scan", "Run", "Report"]


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
        self.query_one("#message", Static).update("Looking for backup disks...")
        self.run_worker(self._discover, thread=True, exclusive=True)

    def _discover(self):
        found = self.app.discover()
        self.app.call_from_thread(self._show, *found)

    def _show(self, volumes, legacy):
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
            t.append(f"\n  not available: {v.note}", style="#ffb000")
        return t

    def on_option_list_option_selected(self, event):
        volume = self.volumes.get(event.option_id)
        if volume and volume.supported:
            self.app.source = volume
            self.app.push_screen(ContentsScreen())

    def action_help(self):
        self.notify(
            "Up/Down move, Enter selects, r rescans the disks, q quits.\n"
            "Only local USB disks with Time Machine snapshots can be a source.",
            title="Help",
        )


class ContentsScreen(Screen):
    """Step 2 (placeholder until the next iteration)."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield StepBar(2)
        src = self.app.source
        yield Static(f"Source: {src.name}  ({len(src.snapshots)} snapshots)", classes="heading")
        yield Static("Choosing dates and folders comes next.", classes="dim")
        yield Footer()


class StaApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "SmartTimeArchive"

    def __init__(self, discover=discover_all, skip_welcome=False):
        super().__init__()
        self.discover = discover
        self.skip_welcome = skip_welcome
        self.source = None

    def on_mount(self):
        self.push_screen(SourceScreen() if self.skip_welcome else WelcomeScreen())


def run():
    StaApp().run()
