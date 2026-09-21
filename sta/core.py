"""Scan + extract engine. No UI, no prompts: everything is driven by arguments.

Flow: scan every selected snapshot once (metadata only) into a temp sqlite db,
derive the plan (sizes, space check) from it, then copy each snapshot using the
db as the map. Source content is read once; checksums are computed on the
destination copy.
"""

import collections
import contextlib
import ctypes
import errno
import fnmatch
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time

from .apfs import ApfsError, SnapshotMount, find_data_root, list_snapshots, volume_info
from .image import image_capacity
from .util import as_invoking_user
from .util import own as _own

COMPLETED = "COMPLETED"
COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
CANCELLED = "CANCELLED"
FAILED = "FAILED"

SPACE_MARGIN = 0.02  # extra fraction of required space
SPACE_MARGIN_BYTES = 512 * 1024 * 1024
REPORT_DIR = "_sta"

# copyfile(3) flags (copyfile.h)
_ACL, _STAT, _XATTR, _DATA = 1, 2, 4, 8
_EXCL, _NOFOLLOW_SRC, _NOFOLLOW_DST = 1 << 17, 1 << 18, 1 << 19
_COPY_ALL = _ACL | _STAT | _XATTR | _DATA | _EXCL | _NOFOLLOW_SRC | _NOFOLLOW_DST
_COPY_META = _ACL | _STAT | _XATTR | _NOFOLLOW_SRC | _NOFOLLOW_DST

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
_libc.copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]


def manifest_line(sha, rel):
    """One manifest line, GNU sha256sum style: names with backslash/newline are escaped
    and the line is prefixed with a backslash, so one entry is always one line."""
    prefix = ""
    if "\\" in rel or "\n" in rel or "\r" in rel:
        rel = rel.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
        prefix = "\\"
    return f"{prefix}{sha or '-' * 64}  {rel}\n"


def parse_manifest_line(line):
    line = line[:-1] if line.endswith("\n") else line
    escaped = line.startswith("\\")
    if escaped:
        line = line[1:]
    sha, _, name = line.partition("  ")
    if escaped:
        out, i = [], 0
        while i < len(name):
            if name[i] == "\\" and i + 1 < len(name):
                out.append({"n": "\n", "r": "\r"}.get(name[i + 1], name[i + 1]))
                i += 2
            else:
                out.append(name[i])
                i += 1
        name = "".join(out)
    return (None if sha.startswith("-") else sha), name


class Options:
    def __init__(self, users=None, folders=None, excludes=None):
        self.users = users or []  # empty = all users
        self.folders = folders or []  # top-level folders of a home; empty = everything
        self.excludes = excludes or []  # fnmatch globs matched against each path component


# ---------------------------------------------------------------- sources

Snap = collections.namedtuple("Snap", "date opener key")  # key: stable id for the scan cache


class _ApfsOpen:
    def __init__(self, device, snapshot):
        self._mount = SnapshotMount(device, snapshot)

    def __enter__(self):
        self._mount.__enter__()
        root = find_data_root(self._mount.mountpoint)
        if not root:
            self._mount.__exit__(None, None, None)
            raise ApfsError("Data/Users not found inside the snapshot")
        return root

    def __exit__(self, *exc):
        return self._mount.__exit__(*exc)


class ApfsSource:
    """A mounted Time Machine APFS backup volume (e.g. /Volumes/DJI Fly OJO)."""

    def __init__(self, volume_path):
        self.volume_path = volume_path
        info = volume_info(volume_path)
        self.device = "/dev/" + info["DeviceIdentifier"]
        self.volume_uuid = info.get("VolumeUUID", self.device)

    def snapshots(self):
        return [
            Snap(date, lambda s=snap: _ApfsOpen(self.device, s), f"{self.volume_uuid}:{uuid}")
            for date, snap, uuid in list_snapshots(self.volume_path)
        ]


class DirSource:
    """Plain folders <base>/<date>/Users/... standing in for snapshots (tests)."""

    def __init__(self, base):
        self.base = base

    def snapshots(self):
        out = []
        for name in sorted(os.listdir(self.base)):
            root = os.path.join(self.base, name)
            if os.path.isdir(os.path.join(root, "Users")):
                out.append(Snap(name, lambda r=root: contextlib.nullcontext(r), root))
        return out


def select_dates(all_dates, dates=None, last=None, one_per_day=False):
    sel = list(all_dates)
    if dates:
        sel = [d for d in sel if any(d.startswith(p) for p in dates)]
    if one_per_day:
        by_day = {}
        for d in sel:
            by_day[d[:10]] = d  # sorted ascending: last one of the day wins
        sel = sorted(by_day.values())
    if last:
        sel = sel[-last:]
    return sel


# ---------------------------------------------------------------- scanning


def _included(parts, opts):
    if len(parts) >= 2 and opts.users and parts[1] not in opts.users:
        return False
    if len(parts) >= 3 and opts.folders and parts[2] not in opts.folders:
        return False
    return not any(fnmatch.fnmatchcase(p, pat) for p in parts[1:] for pat in opts.excludes)


def _open_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files(
            snap TEXT, rel TEXT, kind TEXT, ino INT, size INT, mtime_ns INT);
        CREATE INDEX IF NOT EXISTS files_snap ON files(snap, rel);
        CREATE TABLE IF NOT EXISTS done(
            ino INT, size INT, mtime_ns INT, snap TEXT, rel TEXT, sha TEXT,
            PRIMARY KEY(ino, size, mtime_ns));
    """)
    return conn


BATCH = 50_000


def scan_snapshot(conn, date, data_root, opts, progress=None):
    """Metadata walk of <data_root>/Users into the db. Returns list of errors."""
    errors, rows, total = [], [(date, "Users", "d", 0, 0, 0)], 0
    stack = [(os.path.join(data_root, "Users"), ("Users",))]

    def flush():
        nonlocal rows, total
        conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?)", rows)
        total += len(rows)
        rows = []
        if progress:
            progress(total)

    while stack:
        abs_dir, parts = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError as e:
            errors.append(("/".join(parts), e.errno, f"cannot list directory: {e.strerror}"))
            continue
        for e in entries:
            p = parts + (e.name,)
            if not _included(p, opts):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                errors.append(("/".join(p), ex.errno, f"cannot stat: {ex.strerror}"))
                continue
            rel = "/".join(p)
            if stat.S_ISDIR(st.st_mode):
                rows.append((date, rel, "d", 0, 0, 0))
                stack.append((e.path, p))
            elif stat.S_ISREG(st.st_mode):
                rows.append((date, rel, "f", st.st_ino, st.st_size, st.st_mtime_ns))
            elif stat.S_ISLNK(st.st_mode):
                rows.append((date, rel, "l", 0, 0, 0))
            else:
                rows.append((date, rel, "o", 0, 0, 0))
        if len(rows) >= BATCH:
            flush()
    flush()
    conn.commit()
    return errors


# Snapshots never change, so what we learn about one is valid forever. One cache
# file per snapshot; a scan without filters serves every later filtered run too.


def cache_dir():
    home = os.path.expanduser("~")
    uid = os.environ.get("SUDO_UID")
    if os.geteuid() == 0 and uid:
        import pwd

        home = pwd.getpwuid(int(uid)).pw_dir
    return os.path.join(home, "Library", "Caches", "sta")


def _cache_files(cdir):
    """Only our scan files: the folder may also hold a fallback report, which is not cache."""
    try:
        return [
            os.path.join(cdir, n) for n in os.listdir(cdir) if n.endswith((".db", ".db.tmp"))
        ]
    except OSError:
        return []


def cache_size(cdir=None):
    """Bytes the scan cache takes on disk."""
    total = 0
    for path in _cache_files(cdir or cache_dir()):
        with contextlib.suppress(OSError):
            total += os.path.getsize(path)
    return total


def clear_cache(cdir=None):
    """Deletes the scan cache (it is only a speed-up: the next scan rebuilds it). Returns bytes freed."""
    cdir = cdir or cache_dir()
    freed = cache_size(cdir)
    for path in _cache_files(cdir):
        with contextlib.suppress(OSError):
            os.unlink(path)
    with contextlib.suppress(OSError):
        os.rmdir(cdir)  # only succeeds when nothing else is left in it
    return freed


def _opts_key(opts):
    if not (opts.users or opts.folders or opts.excludes):
        return "full"
    raw = json.dumps([sorted(opts.users), sorted(opts.folders), sorted(opts.excludes)])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _cache_file(cdir, snap, okey):
    return os.path.join(cdir, f"{hashlib.sha1(snap.key.encode()).hexdigest()[:16]}_{okey}.db")


def _find_cache(cdir, snap, opts):
    """(path, needs_filtering) for the best usable cache file, or None."""
    exact = _cache_file(cdir, snap, _opts_key(opts))
    if os.path.exists(exact):
        return exact, False
    full = _cache_file(cdir, snap, "full")
    if os.path.exists(full):
        return full, True
    return None


def uncached_dates(source, dates, opts, cdir=None):
    cdir = cdir or cache_dir()
    return [
        s.date
        for s in source.snapshots()
        if s.date in set(dates) and not _find_cache(cdir, s, opts)
    ]


def _private_copy(path):
    """Copy a cache file (read with the user's rights) into a folder only root can enter, and hand
    back the copy: root never opens a file the user could swap under its feet."""
    tmpdir = tempfile.mkdtemp(prefix="sta_cache_")  # mode 0700
    dst = os.path.join(tmpdir, "c.db")
    with as_invoking_user():
        shutil.copyfile(path, dst)
    return tmpdir, dst


def _load_cached(conn, path, opts, needs_filter):
    tmpdir, path = _private_copy(path)
    try:
        return _attach_and_load(conn, path, opts, needs_filter)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _attach_and_load(conn, path, opts, needs_filter):
    conn.execute("ATTACH DATABASE ? AS c", (path,))
    try:
        errors = json.loads(conn.execute("SELECT v FROM c.meta WHERE k='errors'").fetchone()[0])
        if needs_filter:
            rows = [
                r
                for r in conn.execute("SELECT * FROM c.files")
                if _included(tuple(r[1].split("/")), opts)
            ]
            conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?)", rows)
        else:
            conn.execute("INSERT INTO files SELECT * FROM c.files")
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE c")
    return [tuple(e) for e in errors]


def _scan_to_cache(snap, opts, cdir, log, emit):
    """Scans one snapshot into a private db, then publishes it in the user's cache folder as that
    user (atomic rename). Returns (private_db_path, tmpdir): the caller loads it and removes tmpdir."""
    final = _cache_file(cdir, snap, _opts_key(opts))
    tmpdir = tempfile.mkdtemp(prefix="sta_scan_")  # only root can enter it
    private = os.path.join(tmpdir, "scan.db")
    c = _open_db(private)
    c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    t0 = time.time()
    with snap.opener() as root:
        errors = scan_snapshot(
            c,
            snap.date,
            root,
            opts,
            progress=lambda n: (
                log(f"    {n:,} entries, {time.time() - t0:,.0f}s"),
                emit({"event": "scan_progress", "date": snap.date, "entries": n,
                      "seconds": round(time.time() - t0, 1)}),
            ),  # fmt: skip
        )
    c.execute("INSERT INTO meta VALUES ('errors', ?)", (json.dumps(errors),))
    c.execute("INSERT INTO meta VALUES ('seconds', ?)", (str(time.time() - t0),))
    c.commit()
    c.close()
    _publish_cache(private, final)
    return private, tmpdir


def _publish_cache(private, final):
    """Copy into the user's cache folder with the user's own rights; a failure only costs the cache."""
    tmp = final + ".tmp"
    try:
        with as_invoking_user():
            os.makedirs(os.path.dirname(final), exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            shutil.copyfile(private, tmp)
            os.rename(tmp, final)
    except OSError:
        pass


def scan(source, dates, opts, db_path, log=print, use_cache=True, cdir=None, emit=lambda ev: None):
    """Loads/creates the per-snapshot scans and merges them into one working db."""
    conn = _open_db(db_path)
    wanted = set(dates)
    snaps = [s for s in source.snapshots() if s.date in wanted]
    cdir = cdir or cache_dir()
    if use_cache:
        try:
            with as_invoking_user():
                os.makedirs(cdir, exist_ok=True)
        except OSError:
            use_cache = False
    scan_errors, spent, scanned = {}, 0.0, 0
    for i, snap in enumerate(snaps, 1):
        hit = _find_cache(cdir, snap, opts) if use_cache else None
        if hit:
            scan_errors[snap.date] = _load_cached(conn, hit[0], opts, hit[1])
            log(f"[{i}/{len(snaps)}] {snap.date}: from cache")
            emit(
                {
                    "event": "scan_snapshot",
                    "i": i,
                    "n": len(snaps),
                    "date": snap.date,
                    "cached": True,
                }
            )
            continue
        log(f"[{i}/{len(snaps)}] {snap.date}: scanning ...")
        emit(
            {"event": "scan_snapshot", "i": i, "n": len(snaps), "date": snap.date, "cached": False}
        )
        t0 = time.time()
        if use_cache:
            private, tmpdir = _scan_to_cache(snap, opts, cdir, log, emit)
            try:
                scan_errors[snap.date] = _attach_and_load(conn, private, opts, False)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            with snap.opener() as root:
                scan_errors[snap.date] = scan_snapshot(conn, snap.date, root, opts)
        spent += time.time() - t0
        scanned += 1
        left = sum(1 for s in snaps[i:] if not (use_cache and _find_cache(cdir, s, opts)))
        if left:
            log(f"    took {time.time() - t0:,.0f}s; ~{spent / scanned * left / 60:,.1f} min left")
            emit({"event": "scan_eta", "minutes_left": round(spent / scanned * left / 60, 1)})
    return conn, scan_errors


# ---------------------------------------------------------------- planning


def _existing_ancestor(path):
    path = os.path.abspath(path)
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return path


def dest_traits(dest):
    """(hardlinks_supported, case_insensitive, free_bytes) of the destination."""
    base = _existing_ancestor(dest)
    st = os.statvfs(base)
    free = st.f_bavail * st.f_frsize
    probe = tempfile.mkdtemp(prefix=".sta_probe_", dir=base)
    try:
        a = os.path.join(probe, "Ab")
        with open(a, "w") as f:
            f.write("x")
        try:
            os.link(a, os.path.join(probe, "link"))
            links = True
        except OSError:
            links = False
        return links, os.path.exists(os.path.join(probe, "aB")), free
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def make_plan(conn, dest, via_image=False):
    q = conn.execute
    files, full = q("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE kind='f'").fetchone()
    unique_files, unique = q(
        "SELECT COUNT(*), COALESCE(SUM(s),0) FROM "
        "(SELECT size AS s FROM files WHERE kind='f' GROUP BY ino,size,mtime_ns)"
    ).fetchone()
    links, case_insens, free = dest_traits(dest)
    host_links = links
    if via_image:  # we write inside a case-sensitive APFS image: links work, no case clashes
        links, case_insens = True, False
    needed = unique if links else full
    needed_margin = int(needed * (1 + SPACE_MARGIN)) + SPACE_MARGIN_BYTES
    capacity = image_capacity(free) if via_image else free
    return {
        "snapshots": q("SELECT COUNT(DISTINCT snap) FROM files").fetchone()[0],
        "files": files,
        "unique_files": unique_files,
        "bytes_with_links": unique,
        "bytes_without_links": full,
        "dest_hardlinks": links,
        "host_hardlinks": host_links,
        "via_image": via_image,
        "dest_case_insensitive": case_insens,
        "dest_free": free,
        "image_capacity": capacity if via_image else None,
        "bytes_needed": needed,
        "bytes_needed_with_margin": needed_margin,
        "fits": capacity >= needed_margin,
    }


# ---------------------------------------------------------------- copying


def _copyfile(src, dst, flags):
    rc = _libc.copyfile(os.fsencode(src), os.fsencode(dst), None, flags)
    if rc != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), src)


def _copy_with_retry(src, dst, tries=3):
    for attempt in range(tries):
        try:
            _copyfile(src, dst, _COPY_ALL)
            return
        except OSError as e:
            if os.path.lexists(dst):
                os.unlink(dst)  # never leave a half-written file behind
            if e.errno != errno.EIO or attempt == tries - 1:
                raise
            time.sleep(0.5)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(rel):
    """True for a plain relative path inside the archive. The scan data lives in a file the user can
    edit while the engine runs as administrator, so a path is never trusted to stay inside."""
    return (
        bool(rel)
        and "\0" not in rel
        and not os.path.isabs(rel)
        and ".." not in rel.split("/")
    )


class DestinationFull(Exception):
    pass


class Extractor:
    def __init__(
        self,
        source,
        dest,
        conn,
        dates,
        log=print,
        cancel=lambda: False,
        case_insensitive=False,
        emit=lambda ev: None,
    ):
        self.source, self.dest, self.conn, self.dates = source, dest, conn, dates
        self.log, self.cancel, self.case_insensitive = log, cancel, case_insensitive
        self.emit = emit
        self.report = {"dates": {}, "status": None, "started": time.strftime("%Y-%m-%d %H:%M:%S")}

    def run(self):
        os.makedirs(os.path.join(self.dest, REPORT_DIR, "manifests"), exist_ok=True)
        for p in (
            self.dest,
            os.path.join(self.dest, REPORT_DIR),
            os.path.join(self.dest, REPORT_DIR, "manifests"),
        ):
            _own(p)
        openers = {sn.date: sn.opener for sn in self.source.snapshots()}
        try:
            for i, date in enumerate(self.dates, 1):
                if self.cancel():
                    break
                self._extract_date(date, openers[date], i)
        except Exception as e:  # unexpected: report, never claim success
            self.report["fatal"] = (
                str(e) if isinstance(e, DestinationFull) else f"{type(e).__name__}: {e}"
            )
            self.report["status"] = FAILED
            self.emit({"event": "error", "message": self.report["fatal"]})
        else:
            if self.cancel():
                self.report["status"] = CANCELLED
            elif any(d["errors"] for d in self.report["dates"].values()):
                self.report["status"] = COMPLETED_WITH_ERRORS
            else:
                self.report["status"] = COMPLETED
        self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_report()
        return self.report["status"]

    def _date_root(self, date, current):
        return os.path.join(self.dest, date + ".partial" if date == current else date)

    def _extract_date(self, date, opener, i=1):
        final = os.path.join(self.dest, date)
        stats = {
            "copied": 0,
            "linked": 0,
            "dirs": 0,
            "symlinks": 0,
            "skipped_special": 0,
            "bytes_copied": 0,
            "errors": [],
            "skipped": False,
        }
        self.report["dates"][date] = stats
        if os.path.isdir(final):
            stats["skipped"] = True
            self.log(f"{date}: already extracted, skipping")
            self.emit({"event": "date_skipped", "date": date, "i": i, "n": len(self.dates)})
            self._load_done(date)
            return
        part = final + ".partial"
        if os.path.isdir(part):
            shutil.rmtree(part)
        os.makedirs(part)
        _own(part)
        self.log(f"{date}: extracting ...")
        rows = self.conn.execute(
            "SELECT rel, kind, ino, size, mtime_ns FROM files WHERE snap=? ORDER BY rel", (date,)
        ).fetchall()
        self.emit({"event": "extract_start", "date": date, "i": i, "n": len(self.dates),
                   "entries": len(rows)})  # fmt: skip
        manifest, dirs, seen = [], [], set()
        with opener() as data_root:
            for n, (rel, kind, ino, size, mtime_ns) in enumerate(rows, 1):
                if self.cancel():
                    self.log(f"{date}: cancelled, leaving {os.path.basename(part)}")
                    return
                if not safe_rel(rel):
                    stats["errors"].append((rel, 0, "unsafe path in the scan data: skipped"))
                    continue
                src, dst = os.path.join(data_root, rel), os.path.join(part, rel)
                if self.case_insensitive:
                    low = rel.lower()
                    if low in seen:
                        stats["errors"].append(
                            (rel, 0, "name collision on case-insensitive destination")
                        )
                        continue
                    seen.add(low)
                try:
                    if kind == "d":
                        os.makedirs(dst, exist_ok=True)
                        dirs.append((src, dst))
                        stats["dirs"] += 1
                    elif kind == "l":
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        _copyfile(src, dst, _COPY_ALL)
                        stats["symlinks"] += 1
                    elif kind == "f":
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        sha = self._file(date, rel, src, dst, ino, size, mtime_ns, stats)
                        manifest.append((sha, rel))
                    else:
                        stats["skipped_special"] += 1
                except OSError as e:
                    if e.errno == errno.ENOSPC:  # every next file would fail too: stop here
                        raise DestinationFull(
                            "The destination is full. The backups already finished are kept."
                        ) from e
                    stats["errors"].append((rel, e.errno or 0, e.strerror or str(e)))
                    self.log(f"  ERROR {rel}: {e.strerror}")
                if n % 2000 == 0:
                    self.emit({"event": "extract_progress", "date": date, "done": n,
                               "total": len(rows), "copied": stats["copied"],
                               "linked": stats["linked"], "bytes_copied": stats["bytes_copied"],
                               "errors": len(stats["errors"])})  # fmt: skip
                if n % 5000 == 0:
                    self.log(f"  {date}: {n}/{len(rows)} entries")
            for src, dst in sorted(dirs, key=lambda t: -t[1].count(os.sep)):
                with contextlib.suppress(OSError):
                    _copyfile(src, dst, _COPY_META)  # dir mtime/xattrs/acl after contents
        mpath = os.path.join(self.dest, REPORT_DIR, "manifests", date + ".sha256")
        with open(mpath, "w", newline="\n", encoding="utf-8", errors="surrogateescape") as f:
            f.writelines(manifest_line(sha, rel) for sha, rel in manifest)
        _own(mpath)
        os.rename(part, final)
        self.conn.commit()
        self.log(
            f"{date}: done ({stats['copied']} copied, {stats['linked']} linked, "
            f"{len(stats['errors'])} errors)"
        )
        self.emit({"event": "date_done", "date": date, "i": i, "n": len(self.dates),
                   "copied": stats["copied"], "linked": stats["linked"],
                   "bytes_copied": stats["bytes_copied"], "errors": len(stats["errors"])})  # fmt: skip

    def _file(self, date, rel, src, dst, ino, size, mtime_ns, stats):
        row = self.conn.execute(
            "SELECT snap, rel, sha FROM done WHERE ino=? AND size=? AND mtime_ns=?",
            (ino, size, mtime_ns),
        ).fetchone()
        if row:
            prev = os.path.join(self._date_root(row[0], date), row[1])
            try:
                os.link(prev, dst)
                stats["linked"] += 1
                return row[2]
            except OSError:
                pass  # fall through: plain copy
        _copy_with_retry(src, dst)
        try:
            sha = _sha256(dst)
        except OSError:
            sha = None
        stats["copied"] += 1
        stats["bytes_copied"] += size
        if not row:
            self.conn.execute(
                "INSERT OR IGNORE INTO done VALUES (?,?,?,?,?,?)",
                (ino, size, mtime_ns, date, rel, sha),
            )
        return sha

    def _load_done(self, date):
        """A date extracted in an earlier run still serves as a hard-link target."""
        shas = {}
        try:
            with open(
                os.path.join(self.dest, REPORT_DIR, "manifests", date + ".sha256"),
                newline="\n",
                encoding="utf-8",
                errors="surrogateescape",
            ) as f:
                for line in f:
                    sha, rel = parse_manifest_line(line)
                    shas[rel] = sha
        except OSError:
            pass
        rows = self.conn.execute(
            "SELECT ino, size, mtime_ns, snap, rel FROM files WHERE snap=? AND kind='f'", (date,)
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO done VALUES (?,?,?,?,?,?)",
            [(i, s, m, sn, r, shas.get(r)) for i, s, m, sn, r in rows.fetchall()],
        )

    def _write_report(self):
        name = "report_" + time.strftime("%Y%m%d-%H%M%S")
        try:
            self._write_report_to(os.path.join(self.dest, REPORT_DIR, name))
        except OSError as e:  # e.g. the destination is full: keep the report somewhere else
            fallback = os.path.join(cache_dir(), name)
            with as_invoking_user():  # the folder is the user's: never write there as root
                os.makedirs(os.path.dirname(fallback), exist_ok=True)
                self._write_report_to(fallback)
            self.log(f"Report not written on the destination ({e.strerror}); kept in {fallback}")

    def _write_report_to(self, base):
        self.report["report_file"] = base + ".txt"
        with open(base + ".json", "w") as f:
            json.dump(self.report, f, indent=2)
        _own(base + ".json")
        with open(base + ".txt", "w") as f:
            r = self.report
            f.write(
                f"SmartTimeArchive report\nStatus: {r['status']}\n"
                f"Started: {r['started']}\nFinished: {r['finished']}\n"
            )
            if r.get("fatal"):
                f.write(f"Fatal: {r['fatal']}\n")
            for d, s in r["dates"].items():
                f.write(
                    f"\n{d}: copied={s['copied']} linked={s['linked']} dirs={s['dirs']} "
                    f"symlinks={s['symlinks']} bytes_copied={s['bytes_copied']} "
                    f"errors={len(s['errors'])}{' (skipped: already extracted)' if s['skipped'] else ''}\n"
                )
                for rel, err, msg in s["errors"]:
                    f.write(f"  UNREADABLE/FAILED [{err}] {rel}: {msg}\n")
        _own(base + ".txt")
