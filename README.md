# SmartTimeArchive

Extract the **history of a Time Machine (APFS) backup** into plain, dated folders you can browse
and search with Finder — on another disk, or inside a disk image on a network share — without
multiplying the space the history takes.

Apple does not let you copy an APFS Time Machine backup to another disk: each backup is a hidden
snapshot and only Time Machine knows how to move them. If your backup disk is old or full, the usual
advice is "keep it as an archive and start over". SmartTimeArchive turns that history into ordinary
folders instead:

```
<destination>/
├── 2026-08-23-203105/Users/<you>/Documents/...
├── 2026-09-18-194541/Users/<you>/Documents/...
└── _sta/                        manifests (sha256 per date) + extraction reports
```

Files that did not change between two backups are **hard links** to the same data, exactly like
Time Machine itself does, so 12 snapshots that would take 671 GB as separate copies took 184 GB.
It is an archive, not a Time Machine replacement: you open a folder and find what you deleted
years ago.

> **Status:** the v2 engine (command line) is working and tested against a real 12-snapshot
> backup. A terminal UI is next (see [Roadmap](#roadmap)).

## Requirements

- macOS with the APFS Time Machine backup disk (a local USB disk) mounted.
- Python 3.9+ — standard library only.
- `sudo`: mounting snapshots needs root. Everything the tool writes is handed back to your user.

## Usage

```bash
# 1. which snapshots does the backup contain?
sudo python3 -m sta list "/Volumes/My Backup"

# 2. how much space is needed? (read-only, nothing is copied)
sudo python3 -m sta plan "/Volumes/My Backup" /Volumes/Archive

# 3. extract
sudo python3 -m sta extract "/Volumes/My Backup" /Volumes/Archive

# 4. later: re-check the archive against its sha256 manifests (read-only; sudo also covers
#    the permission-protected files). Works on a folder or on a SmartTimeArchive image.
sudo python3 -m sta verify /Volumes/Archive
```

| Option | Meaning |
|---|---|
| `--dates 2026-09-15,2026-09-18` | only snapshots whose name starts with any of these prefixes |
| `--last N` | only the N most recent snapshots |
| `--one-per-day` | keep only the last snapshot of each day |
| `--users bob` / `--folders Documents,Desktop` | limit to users / top-level home folders (hidden files are included by default) |
| `--exclude GLOB` | skip any path component matching the glob (repeatable, exact component, not substring) |
| `--dest-image` | write into a case-sensitive APFS disk image (`SmartTimeArchive.sparsebundle`) created inside the destination: keeps hard links on exFAT, NTFS or network shares |
| `--yes` | accept the "destination has no hard links" warning and store everything in full |
| `--no-cache` | ignore the scan cache |
| `--json` | machine-readable output, one JSON object per line (plan, progress, result); used by the terminal UI |

`plan` and `extract` first read the **metadata of every file** in the selected snapshots. On an old
disk this can take several minutes; the result is cached in `~/Library/Caches/sta/` (snapshots never
change), so the extraction after a `plan` does not read it again. Copying is then limited by the disk:
many small files are much slower than a few big ones (a real 184 GB / 2.4 M-entry extraction from a
5400 rpm USB disk took about an hour).

Exit codes (`extract` and `verify`): `0` ok, `3` ok with warnings (unreadable files), `130` cancelled, `1` failed.
Ctrl+C cancels cleanly and never reports success.

## Safety rules

- The backup is mounted **read-only** and is never modified. Nothing is ever deleted from it.
- Each date is written to `<date>.partial` and renamed only when finished, so an interrupted run is
  visible as such. Re-running skips finished dates and still links against them.
- Every source file is read **once**; read errors (`EIO`) are retried a couple of times, logged and the
  run continues. The final report lists exactly which files could not be read.
- A sha256 is stored per file in `_sta/manifests/` (computed on the copy, so the fragile source disk
  is not read twice). Names containing `\`, newline or carriage return are escaped like GNU `sha256sum`.
- Disk space is checked before copying. A destination without hard links (exFAT, NTFS, SMB share) is
  refused unless you pass `--dest-image` (recommended) or `--yes`, and the plan shows the size with and without links.
- Ownership, permissions, extended attributes, ACLs and timestamps are preserved (via `copyfile(3)`),
  so some files stay unreadable for a normal user, as in the original.

## Good to know

- **Copying the archive later with Finder breaks the hard links** and multiplies its size. Move it with
  `sudo ditto SRC DST` (checked: it keeps hard links, owners, modes, xattrs and symlinks; the only thing
  it lost in a real test was the own timestamp of one symlink that carries a "deny writeattr" ACL) or
  `rsync -aH`. Never copy it to an exFAT/NTFS/SMB destination directly: use `--dest-image`.
- The disk image is created with `diskutil image` (macOS versions without it fall back to the deprecated
  `hdiutil`). Its volume is **case-sensitive APFS**, like the Time Machine backup it comes from.
- On a case-insensitive destination (the default for Mac system disks) two names that differ only in
  case collide; the tool detects it and lists them in the report instead of overwriting.
- Only the `Data/Users` part of each backup is extracted (your files and settings, not the sealed
  system volume, not other volumes).

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests build fake "snapshots" with the same hard-link layout Time Machine leaves and check content
per date, links, hidden files, symlinks, xattrs, timestamps, unreadable files, cancellation, resume,
filters and the scan cache.

## Scope

**Source:** a Time Machine backup on a local USB disk (APFS). That is the case Apple gives no way out of.
(A Time Machine backup on a network share is a `.sparsebundle` file: to just move it, copy that file.
The engine can read one that is already attached, but it is not a supported source.)

**Destination:** another USB disk, or a network unit. Disks that cannot hold hard links (exFAT, NTFS,
SMB shares) use a case-sensitive APFS disk image created inside them with `--dest-image`.

## Roadmap

1. **Engine (CLI)** — done, including `--dest-image`.
2. Terminal UI wizard: pick source → contents → destination → confirm → run → report.
3. Optional `.tar.gz` output that keeps the hard links.

`main.py`, `engine.py` and `ui/` are the **v1 desktop app** (PySide6). They are kept until the terminal UI
replaces them and should not be used on data you care about: v1 can delete the source after a migration
that reported errors, and reports success after a cancel.

## Support

If this tool saved your history, consider [buying me a coffee](https://ko-fi.com/hbarchini).
