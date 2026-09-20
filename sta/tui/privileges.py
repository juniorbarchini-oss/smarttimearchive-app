"""Administrator access for the engine. The TUI never sees the password: sudo asks for it itself."""

import subprocess
import threading

RENEW_SECONDS = 60


def has_ticket(run=subprocess.run):
    """True when sudo will not ask for a password right now."""
    try:
        return run(["sudo", "-n", "true"], capture_output=True).returncode == 0
    except OSError:
        return False


def ask(password, run=subprocess.run):
    """Hand the password to `sudo -v` through stdin (never argv) and forget it.

    The ticket belongs to the terminal this program runs in, so the engine's later
    `sudo -n` calls find it. Nothing here stores or logs the password.
    """
    try:
        r = run(
            ["sudo", "-S", "-v", "-p", ""],
            input=password + "\n",
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except OSError:
        return False


class Keepalive:
    """Renews the sudo ticket while the engine runs, so long jobs never ask again."""

    def __init__(self, run=subprocess.run, interval=RENEW_SECONDS):
        self._run, self._interval = run, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self._interval):
            self._run(["sudo", "-n", "-v"], capture_output=True)
