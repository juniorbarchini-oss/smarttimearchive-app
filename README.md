# SmartTimeArchive

Archive the **history of a Time Machine (APFS) backup disk** into plain, dated folders on another
USB disk or a network unit, without multiplying the space the history takes. **Your Time Machine
disk is never touched**: this only makes a copy.

Apple gives no way to copy an APFS Time Machine backup from a USB disk to another one: each backup is
a hidden snapshot and only Time Machine knows how to move them. Its advice is "keep the old disk in a
drawer and start over". This tool covers the two situations where that hurts:

1. **The backup disk is filling up.** Time Machine will start overwriting the oldest backups. Archive
   the N oldest ones now; Time Machine carries on exactly as before.
2. **The backup disk is old and you fear it will die.** Archive everything, reuse the old disk for
   something else, and start Time Machine from zero on a new disk. Nothing is lost.

```
<destination>/
├── 2026-08-23-203105/Users/<you>/Documents/...
├── 2026-09-18-194541/Users/<you>/Documents/...
└── _sta/                        manifests (sha256 per date) + extraction reports
```

Files that did not change between two backups are **hard links** to the same data, exactly like
Time Machine itself does, so 12 snapshots that would take 671 GB as separate copies took 184 GB.
It is an archive, not a Time Machine replacement: to restore an old file, Time Machine does that
already; this keeps the history safe when the disk can no longer hold it.

## Install

**From the source** (no security warning from macOS: nothing is downloaded through a browser):

```bash
git clone https://github.com/juniorbarchini-oss/smarttimearchive-app.git
cd smarttimearchive-app && ./install.sh
```

It asks for your administrator password once. It installs:

| what | where |
|---|---|
| the engine and its own Python environment | `/usr/local/lib/smarttimearchive` (owned by root) |
| the `sta` command | `/usr/local/bin/sta` |
| the app (opens Terminal at the right size) | `/Applications/SmartTimeArchive.app` |

The engine runs as administrator, so its code lives where only an administrator can change it.
To remove everything: `/usr/local/lib/smarttimearchive/uninstall.sh` (it asks before deleting the
scan cache and never touches your archives).

## Use

- **App:** double-click *SmartTimeArchive* in Applications or Launchpad.
- **Terminal:** `sta` opens the UI; `sta --tmux` runs it inside `tmux` so a long copy survives a
  closed window (if `tmux` is missing it says so and runs without it).
- **Engine commands:** `sta plan | extract | verify | list ...` (see below).
- **Scan cache:** the scan of each backup is kept in `~/Library/Caches/sta` so the next run is
  fast (it can reach several GB for a large history). Press `d` on the first screen or the final
  one to delete it (it asks first), or use `sta cache` to see its size and `sta cache --clear` to
  delete it. It is only a speed-up: deleting it never affects your archives.

A guided, retro-green wizard: source, backups, destination, administrator password, scan, copy,
report (with an optional verification). It runs as your user and uses `sudo` only for the engine.
You choose everything: nothing is preselected and nothing starts without your yes. A copy can be
cancelled at any moment; each copy into an APFS disk image creates a new image.

Source disks are detected automatically: only local USB disks with Time Machine snapshots are
offered, and the others are listed with the reason they cannot be used.

## Requirements

- **macOS 11 (Big Sur) or newer**, Intel or Apple silicon. Time Machine saves APFS backups, the only
  kind this tool reads, since macOS 11.
- The APFS Time Machine backup disk (a local USB disk) mounted, and an administrator password.
- **Python 3.9 or newer** for the installer. macOS 13+ gets one with `xcode-select --install`; on
  macOS 11-12 use Homebrew (`brew install python`) or python.org.
- The engine itself uses only the standard library; the UI needs [Textual](https://textual.textualize.io)
  (the installer fetches it once).

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

## Credits

Created by **Humberto Barchini**, built together with **Claude** (Anthropic's AI model) using Claude Code:
the design was discussed in chat, and the engine, tests and terminal UI were pair-programmed with it.
The commit history carries the co-author credit.

Not affiliated with or endorsed by Apple or Anthropic. Time Machine is a trademark of Apple Inc.

## Support

If this tool saved your history, consider [buying me a coffee](https://ko-fi.com/hbarchini).
