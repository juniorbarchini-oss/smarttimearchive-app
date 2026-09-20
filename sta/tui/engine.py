"""Runs the engine as a root subprocess (sudo -n) and relays its JSON events to the TUI."""

import json
import os
import subprocess
import sys
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EngineRun:
    def __init__(self, args, on_event, on_exit, popen=subprocess.Popen, run=subprocess.run):
        self.args, self.on_event, self.on_exit = args, on_event, on_exit
        self._popen, self._run = popen, run
        self.pid = None
        self.paused = False
        self.tail = []  # non-JSON lines (Python errors, sys.exit messages), for the error screen

    def start(self):
        self.proc = self._popen(
            ["sudo", "-n", sys.executable, "-m", "sta", *self.args, "--json"],
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

    def pause(self):
        self._signal("STOP")
        self.paused = True

    def resume(self):
        self._signal("CONT")
        self.paused = False

    def cancel(self):
        self._signal("INT")
        if self.paused:  # a stopped process cannot see the signal until it runs again
            self.resume()
