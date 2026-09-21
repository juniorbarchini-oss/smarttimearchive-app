"""Sparsebundle disk images: an APFS volume (with hard links) inside any destination.

Used when the destination filesystem cannot hold hard links (exFAT, NTFS, FAT32,
SMB shares): the archive is written inside a case-sensitive APFS image instead.

`diskutil image` is used where it exists (hdiutil create/attach/detach are deprecated);
older macOS versions fall back to hdiutil.
"""

import os
import plistlib
import shutil
import subprocess
import time

from .apfs import ApfsError
from .util import own, own_tree

IMAGE_NAME = "SmartTimeArchive.sparsebundle"
VOLUME_NAME = "SmartTimeArchive"
MIB = 1024 * 1024
CAPACITY_FRACTION = 0.90
USE_DISKUTIL = None  # None = detect; tests can force True/False


def _run(cmd, plist=False):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        text = (r.stderr + r.stdout).decode(errors="replace").splitlines()
        text = [ln for ln in text if ln.strip() and "WARNING" not in ln]
        raise ApfsError(f"{' '.join(cmd[:3])} failed: {' | '.join(text) or 'no message'}")
    return plistlib.loads(r.stdout) if plist else r.stdout.decode()


def _hdiutil(*args, plist=False):
    return _run(["hdiutil", *args] + (["-plist"] if plist else []), plist)


def _diskutil_image_available():
    global USE_DISKUTIL
    if USE_DISKUTIL is None:
        try:
            r = subprocess.run(["diskutil", "image", "--help"], capture_output=True)
            USE_DISKUTIL = r.returncode == 0
        except OSError:
            USE_DISKUTIL = False
    return USE_DISKUTIL


def image_capacity(free_bytes):
    """Size of a new image: 90% of the free space of the disk it lives on (10% kept as a safety
    margin). The image is sparse, so it only takes what is really written into it."""
    return int(free_bytes * CAPACITY_FRACTION) // MIB * MIB


def new_image_path(dest, now=None):
    """A path for a brand-new image: every copy gets its own, an existing one is never reused."""
    stamp = time.strftime("%Y%m%d-%H%M%S", now or time.localtime())
    path = os.path.join(dest, f"SmartTimeArchive_{stamp}.sparsebundle")
    n = 2
    while os.path.exists(path):
        path = os.path.join(dest, f"SmartTimeArchive_{stamp}-{n}.sparsebundle")
        n += 1
    return path


def _entity(data, hint):
    for ent in data.get("system-entities", []):
        if ent.get("content-hint") == hint:
            return ent.get("dev-entry")
    return None


def _mountpoint(data):
    for ent in data.get("system-entities", []):
        if ent.get("mount-point"):
            return ent["mount-point"]
    return None


def _whole_disk(data):
    dev = _entity(data, "GUID_partition_scheme")
    return dev if dev is None or dev.startswith("/dev/") else "/dev/" + dev


def _make_case_sensitive(path, volname):
    """diskutil creates a case-insensitive volume; replace it with a case-sensitive one."""
    data = _run(["diskutil", "image", "attach", "--plist", "--nobrowse", path], plist=True)
    whole = _whole_disk(data)
    try:
        container = _entity(data, "Apple_APFS_Container")
        volume = _entity(data, "Apple_APFS_Volume")
        if not (container and volume):
            raise ApfsError("new image has no APFS container/volume")
        _run(["diskutil", "apfs", "deleteVolume", volume])
        _run(["diskutil", "apfs", "addVolume", container, "Case-sensitive APFS", volname])
    finally:
        if whole:
            detach_image(whole)


def create_image(path, size_bytes, volname=VOLUME_NAME):
    """Creates a case-sensitive APFS sparsebundle at `path` (must not exist)."""
    if os.path.exists(path):
        raise ApfsError(f"{path} already exists")
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent)
        own(parent)
    size = max(size_bytes // MIB, 64) * MIB
    if _diskutil_image_available():
        _run(
            [
                "diskutil", "image", "create", "blank",
                "--format", "UDSB", "--size", str(size),
                "--volumeName", volname, "--fs", "APFS", path,
            ]
        )  # fmt: skip
        try:
            _make_case_sensitive(path, volname)
        except ApfsError:
            shutil.rmtree(path, ignore_errors=True)  # never leave a half-made image
            raise
    else:
        _hdiutil(
            "create", "-type", "SPARSEBUNDLE", "-fs", "Case-sensitive APFS",
            "-volname", volname, "-size", f"{size // MIB}m", path,
        )  # fmt: skip
    own_tree(path)


def attached_mountpoint(path):
    """Mount point of the image if it is already attached, else None."""
    try:
        info = _hdiutil("info", plist=True)
    except (ApfsError, OSError):
        return None
    real = os.path.realpath(path)
    for img in info.get("images", []):
        if os.path.realpath(img.get("image-path", "")) == real:
            mp = _mountpoint(img)
            if mp:
                return mp
    return None


def attach_image(path):
    """Attaches the image. Returns (mount_point, whole_disk_device).

    Ownership is honoured only when running as root (the extraction): a normal user cannot
    chown anyway, and a root-owned volume root would just get in the way.
    """
    owners = os.geteuid() == 0
    if _diskutil_image_available():
        cmd = ["diskutil", "image", "attach", "--plist", "--nobrowse"]
        cmd += ["--mountOptions", "owners"] if owners else []
        data = _run(cmd + [path], plist=True)
    else:
        cmd = ["attach", "-nobrowse"] + (["-owners", "on"] if owners else [])
        data = _hdiutil(*cmd, path, plist=True)
    mp = _mountpoint(data)
    if not mp:
        raise ApfsError(f"{path} attached but no volume was mounted")
    return mp, _whole_disk(data)


def detach_image(target, tries=5):
    """Detaches politely (Spotlight/fseventsd may hold the volume for a moment)."""
    if _diskutil_image_available():
        cmd = ["diskutil", "eject", target]
    else:
        cmd = ["hdiutil", "detach", target]
    for i in range(tries):
        try:
            _run(cmd)
            return True
        except ApfsError:
            time.sleep(1 + i)
    return False


class ImageMount:
    """Context manager: create the image if missing, attach it, always detach at the end."""

    def __init__(self, path, size_bytes=None):
        self.path, self.size_bytes = path, size_bytes
        self.mountpoint = None
        self.created = False
        self._whole = None
        self._ours = False
        self.detached = None

    def __enter__(self):
        if not os.path.exists(self.path):
            if not self.size_bytes:
                raise ApfsError("image does not exist and no size was given")
            create_image(self.path, self.size_bytes)
            self.created = True
        existing = attached_mountpoint(self.path)
        if existing:
            self.mountpoint = existing
        else:
            (self.mountpoint, self._whole), self._ours = attach_image(self.path), True
        if self.created:
            own(self.mountpoint)  # volume root belongs to whoever ran sudo, not to root
        return self

    def __exit__(self, *exc):
        if self._ours and self.mountpoint:
            self.detached = detach_image(self._whole or self.mountpoint)
        return False
