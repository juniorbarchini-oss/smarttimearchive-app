"""Re-check an extracted archive against its sha256 manifests (read-only)."""

import hashlib
import os
import time

from .apfs import ApfsError
from .core import REPORT_DIR, parse_manifest_line
from .image import IMAGE_NAME, ImageMount

VERIFIED = "VERIFIED"
VERIFIED_WITH_WARNINGS = "VERIFIED_WITH_WARNINGS"
FAILED = "FAILED"
CANCELLED = "CANCELLED"


def find_archive(path):
    """(folder, image) for a path that is an archive folder, an image, or holds an image."""
    path = os.path.abspath(path)
    if os.path.isdir(os.path.join(path, REPORT_DIR, "manifests")):
        return path, None
    if path.endswith(".sparsebundle") and os.path.isdir(path):
        return None, path
    image = os.path.join(path, IMAGE_NAME)
    if os.path.isdir(image):
        return None, image
    raise ApfsError(f"no SmartTimeArchive archive found at {path}")


def _read_manifests(root):
    mdir = os.path.join(root, REPORT_DIR, "manifests")
    out = {}
    for name in sorted(os.listdir(mdir)):
        if not name.endswith(".sha256"):
            continue
        with open(mdir + "/" + name, newline="\n", encoding="utf-8", errors="surrogateescape") as f:
            lines = f.read().split("\n")
        out[name[: -len(".sha256")]] = [parse_manifest_line(ln) for ln in lines if ln]
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _verify_tree(root, emit, cancel):
    manifests = _read_manifests(root)
    total = sum(len(v) for v in manifests.values())
    rep = {"dates": len(manifests), "entries": total, "hashed": 0, "mismatches": [],
           "missing": [], "unreadable": [], "no_checksum": 0, "status": None}  # fmt: skip
    seen, done, t0 = {}, 0, time.time()
    for date, entries in manifests.items():
        for sha, rel in entries:
            if cancel():
                rep["status"] = CANCELLED
                return rep
            done += 1
            if done % 2000 == 0:
                emit({"event": "verify_progress", "done": done, "total": total,
                      "seconds": round(time.time() - t0, 1)})  # fmt: skip
            path = os.path.join(root, date, rel)
            try:
                ino = os.lstat(path).st_ino
            except PermissionError:
                rep["unreadable"].append((date, rel))
                continue
            except OSError:
                rep["missing"].append((date, rel))
                continue
            if sha is None:
                rep["no_checksum"] += 1
                continue
            if ino in seen:  # another hard link of a file already checked
                if seen[ino] != sha:
                    rep["mismatches"].append((date, rel))
                continue
            seen[ino] = sha
            try:
                if _sha256(path) != sha:
                    rep["mismatches"].append((date, rel))
                else:
                    rep["hashed"] += 1
            except PermissionError:
                rep["unreadable"].append((date, rel))
            except OSError:
                rep["missing"].append((date, rel))
    if rep["mismatches"] or rep["missing"]:
        rep["status"] = FAILED
    elif rep["unreadable"] or rep["no_checksum"]:
        rep["status"] = VERIFIED_WITH_WARNINGS
    else:
        rep["status"] = VERIFIED
    return rep


def verify_archive(path, emit=lambda ev: None, cancel=lambda: False):
    """Verifies every manifest entry of the archive at `path`. Never writes to it."""
    folder, image = find_archive(path)
    if image:
        with ImageMount(image) as im:
            return _verify_tree(im.mountpoint, emit, cancel)
    return _verify_tree(folder, emit, cancel)
