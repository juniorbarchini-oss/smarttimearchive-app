"""Find what the user can pick: Time Machine backup disks (sources) and destinations.

Nothing here needs root and nothing scans file contents. Volumes that cannot be used
are still returned, with a `note` saying why, so the UI can explain instead of hiding them.
"""

import os
import plistlib
import subprocess
from dataclasses import dataclass, field

from .apfs import ApfsError, list_snapshots

HARDLINK_FS = {"apfs", "hfs"}
NETWORK_FS = {"smbfs", "afpfs", "nfs", "webdav", "cifs"}


@dataclass
class BackupVolume:
    name: str
    device: str
    mountpoint: str = ""
    bus: str = ""
    used_bytes: int = 0
    snapshots: list = field(default_factory=list)  # date names, oldest first
    supported: bool = False
    note: str = ""


@dataclass
class Destination:
    name: str
    path: str
    fs: str = ""
    free_bytes: int = 0
    hardlinks: bool = False
    network: bool = False
    archive_dates: list = field(default_factory=list)  # dates already extracted here
    has_image: bool = False  # a SmartTimeArchive image already lives here
    is_backup_source: bool = False


def _plist(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return plistlib.loads(r.stdout)
    except Exception:
        return None


def _volume_info(device):
    return _plist(["diskutil", "info", "-plist", device]) or {}


def find_backup_volumes(apfs_list=None, info=_volume_info, snapshots=list_snapshots):
    """APFS volumes with the Backup role: local USB disks are usable sources."""
    data = apfs_list if apfs_list is not None else _plist(["diskutil", "apfs", "list", "-plist"])
    out = []
    for container in (data or {}).get("Containers", []):
        for vol in container.get("Volumes", []):
            if "Backup" not in vol.get("Roles", []):
                continue
            dev = vol["DeviceIdentifier"]
            i = info(dev)
            bv = BackupVolume(
                name=vol.get("Name") or i.get("VolumeName", dev),
                device=dev,
                mountpoint=i.get("MountPoint", ""),
                bus=i.get("BusProtocol", ""),
                used_bytes=int(vol.get("CapacityInUse", 0)),
            )
            if vol.get("Locked"):
                bv.note = "encrypted and locked: unlock it in Finder first"
            elif not bv.mountpoint:
                bv.note = "not mounted"
            elif bv.bus == "Disk Image":
                bv.note = "network image: not a supported source (copy the .sparsebundle instead)"
            else:
                try:
                    bv.snapshots = [d for d, _, _ in snapshots(bv.mountpoint)]
                except ApfsError as e:
                    bv.note = f"cannot read snapshots: {e}"
                else:
                    if bv.snapshots:
                        bv.supported = True
                    else:
                        bv.note = "no Time Machine snapshots found"
            out.append(bv)
    return out


def find_legacy_backups(volumes_dir="/Volumes", is_mount=os.path.ismount):
    """Mounted volumes holding an old-style (HFS+) Time Machine backup: unsupported."""
    out = []
    for name in sorted(_safe_listdir(volumes_dir)):
        path = os.path.join(volumes_dir, name)
        if name.startswith((".", "com.apple.")) or not is_mount(path):
            continue
        if os.path.isdir(os.path.join(path, "Backups.backupdb")):
            out.append(name)
    return out


def _is_internal(path):
    """True for the Mac's own system volumes (Recovery, ...). Network mounts have no info."""
    info = _plist(["diskutil", "info", "-plist", path]) or {}
    return bool(info.get("Internal", False))


def _mount_table():
    """{mount_point: fstype} from mount(8)."""
    res = subprocess.run(["mount"], capture_output=True, text=True)
    table = {}
    for line in res.stdout.splitlines():
        if " on " in line and " (" in line:
            left, _, right = line.partition(" on ")
            mp, _, opts = right.rpartition(" (")
            table[mp] = opts.split(",")[0].strip()
    return table


def existing_archive(path):
    """Dates already extracted into `path` (from its manifests) and whether an image is there."""
    mdir = os.path.join(path, "_sta", "manifests")
    dates = []
    try:
        dates = sorted(n[: -len(".sha256")] for n in os.listdir(mdir) if n.endswith(".sha256"))
    except OSError:
        pass
    has_image = os.path.isdir(os.path.join(path, "SmartTimeArchive.sparsebundle")) or any(
        n.endswith(".sparsebundle") for n in _safe_listdir(path)
    )
    return dates, has_image


def _safe_listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def find_destinations(
    volumes_dir="/Volumes",
    home=None,
    mounts=None,
    sources=(),
    is_mount=os.path.ismount,
    internal=_is_internal,
):
    """Places an archive can go: every mounted volume plus the user's home folder."""
    home = home or os.path.expanduser("~")
    mounts = mounts if mounts is not None else _mount_table()
    source_mounts = {s.mountpoint for s in sources if s.mountpoint}
    out = []

    def add(name, path, is_home=False):
        fs = mounts.get(path, "apfs" if is_home else "")
        try:
            st = os.statvfs(path)
        except OSError:
            return
        dates, has_image = existing_archive(path)
        out.append(
            Destination(
                name=name,
                path=path,
                fs=fs,
                free_bytes=st.f_bavail * st.f_frsize,
                hardlinks=fs in HARDLINK_FS,
                network=fs in NETWORK_FS,
                archive_dates=dates,
                has_image=has_image,
                is_backup_source=path in source_mounts,
            )
        )

    add("This Mac (home folder)", home, is_home=True)
    for name in sorted(_safe_listdir(volumes_dir)):
        path = os.path.join(volumes_dir, name)
        if name.startswith(".") or os.path.islink(path) or not is_mount(path):
            continue
        if internal(path):  # the Mac's own system volumes; its storage is the "home folder" entry
            continue
        add(name, path)
    return out
