"""Runs the engine as a root subprocess (sudo -n) and relays its JSON events to the TUI."""

import json
import os
import subprocess
import sys
import threading

CAFFEINATE = "/usr/bin/caffeinate"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EngineRun:
    def __init__(
        self, args, on_event, on_exit, keep_awake=False, popen=subprocess.Popen, run=subprocess.run
    ):
        self.args, self.on_event, self.on_exit = args, on_event, on_exit
        self.keep_awake = keep_awake
        self._popen, self._run = popen, run
        self.pid = None
        self.tail = []  # non-JSON lines (Python errors, sys.exit messages), for the error screen

    def command(self):
        # caffeinate ships with macOS; if it is somehow missing the copy simply runs without it
        awake = [CAFFEINATE, "-i"] if self.keep_awake and os.path.exists(CAFFEINATE) else []
        return ["sudo", "-n", *awake, sys.executable, "-m", "sta", *self.args, "--json"]

    def start(self):
        self.proc = self._popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=PROJECT_ROOT,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                self.tail = (self.tail + [line.rstrip()])[-8:]
                continue
            if ev.get("event") == "started":
                self.pid = ev["pid"]
            self.on_event(ev)
        self.on_exit(self.proc.wait(), self.tail)

    def _signal(self, name):
        if self.pid:
            self._run(["sudo", "-n", "kill", f"-{name}", str(self.pid)], capture_output=True)

    def cancel(self):
        self._signal("INT")  # the engine unwinds cleanly: unmounts and reports CANCELLED
