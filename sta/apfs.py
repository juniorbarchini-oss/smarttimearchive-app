"""APFS / Time Machine plumbing: snapshot listing and read-only mounting."""

import atexit
import glob
import os
import plistlib
import re
import subprocess
import tempfile

SNAP_RE = re.compile(r"^com\.apple\.TimeMachine\.(\d{4}-\d{2}-\d{2}-\d{6})\.backup$")
MOUNT_PREFIX = "sta_mnt_"


class ApfsError(RuntimeError):
    pass


def _plist(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ApfsError(f"{' '.join(cmd[:3])} did not answer in {timeout}s (disk busy or asleep?)")
    if r.returncode != 0:
        raise ApfsError(f"{' '.join(cmd)} failed: {r.stderr.decode(errors='replace').strip()}")
    return plistlib.loads(r.stdout)


def volume_info(path):
    """Returns diskutil info for the volume that contains `path`."""
    return _plist(["diskutil", "info", "-plist", path])


def device_for(path):
    return "/dev/" + volume_info(path)["DeviceIdentifier"]


def list_snapshots(volume_path):
    """Time Machine snapshots of a mounted APFS backup volume, oldest first.

    Returns [(date_name, snapshot_name, snapshot_uuid)], e.g. ("2026-09-15-115449",
    "com.apple.TimeMachine.2026-09-15-115449.backup", "D02F0310-...").
    """
    data = _plist(["diskutil", "apfs", "listSnapshots", "-plist", volume_path])
    out = []
    for snap in data.get("Snapshots", []):
        name = snap.get("SnapshotName", "")
        m = SNAP_RE.match(name)
        if m:
            out.append((m.group(1), name, snap.get("SnapshotUUID", "")))
    return sorted(out)


def cleanup_stale_mounts():
    """Unmounts leftovers from a previous crashed run (mounts named sta_mnt_*)."""
    res = subprocess.run(["mount"], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        m = re.search(r" on (\S*" + MOUNT_PREFIX + r"\S*/mnt) \(", line)
        if m:
            subprocess.run(["umount", m.group(1)], capture_output=True)


def existing_mountpoint(device, snapshot_name):
    """Where the system already mounted this snapshot (e.g. the newest one, which
    Time Machine keeps mounted under /Volumes/.timemachine), or None."""
    res = subprocess.run(["mount"], capture_output=True, text=True)
    prefix = f"{snapshot_name}@{device} on "
    for line in res.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].rsplit(" (", 1)[0]
    return None


class SnapshotMount:
    """Context manager: mounts one snapshot read-only in a private 0700 dir.

    If the system already has it mounted, that mount is reused and left alone.
    """

    def __init__(self, device, snapshot_name):
        self.device = device
        self.snapshot_name = snapshot_name
        self._tmp = None
        self.mountpoint = None
        self._borrowed = False

    def __enter__(self):
        existing = existing_mountpoint(self.device, self.snapshot_name)
        if existing:
            self.mountpoint, self._borrowed = existing, True
            return self
        self._tmp = tempfile.mkdtemp(prefix=MOUNT_PREFIX)  # mode 0700
        mp = os.path.join(self._tmp, "mnt")
        os.mkdir(mp)
        r = subprocess.run(
            ["mount_apfs", "-o", "rdonly,nobrowse", "-s", self.snapshot_name, self.device, mp],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            self._rm()
            raise ApfsError(f"cannot mount {self.snapshot_name}: {r.stderr.strip()}")
        self.mountpoint = mp
        atexit.register(self._umount)
        return self

    def _umount(self):
        if self._borrowed:
            self.mountpoint = None  # not ours: never unmount the system's mount
            return
        if self.mountpoint:
            subprocess.run(["umount", self.mountpoint], capture_output=True)
            self.mountpoint = None
        self._rm()

    def _rm(self):
        if self._tmp:
            for p in (os.path.join(self._tmp, "mnt"), self._tmp):
                try:
                    os.rmdir(p)
                except OSError:
                    pass
            self._tmp = None

    def __exit__(self, *exc):
        self._umount()
        return False


def find_data_root(mountpoint):
    """Locates <mount>/<date>.backup/.../Data inside a mounted snapshot."""
    for depth in (0, 1, 2):
        pattern = os.path.join(mountpoint, *(["*"] * depth), "Data", "Users")
        hits = glob.glob(pattern)
        if hits:
            return os.path.dirname(hits[0])  # .../Data
    return None
