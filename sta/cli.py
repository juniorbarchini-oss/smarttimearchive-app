"""Command line front-end:  sudo python3 -m sta <list|plan|extract> ..."""

import argparse
import os
import shutil
import signal
import sys
import tempfile

from . import core, image
from .apfs import ApfsError, cleanup_stale_mounts


def _gb(n):
    return f"{n / 1e9:,.2f} GB"


def _parser():
    p = argparse.ArgumentParser(
        prog="sta", description="Extract Time Machine (APFS) history to dated folders."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, dest):
        sp.add_argument(
            "source", help="mounted Time Machine backup volume, e.g. '/Volumes/DJI Fly OJO'"
        )
        if dest:
            sp.add_argument("dest", help="destination folder (APFS disk or mounted image)")
        sp.add_argument(
            "--source-type",
            choices=["apfs", "dir"],
            default="apfs",
            help="'dir' = plain folders <date>/Users/... (testing)",
        )
        sp.add_argument(
            "--dates", help="comma-separated date prefixes, e.g. 2026-09-15,2026-09-18-19"
        )
        sp.add_argument("--last", type=int, help="only the N most recent snapshots")
        sp.add_argument(
            "--one-per-day", action="store_true", help="keep only the last snapshot of each day"
        )
        sp.add_argument("--users", help="comma-separated user names (default: all)")
        sp.add_argument(
            "--folders", help="comma-separated top-level home folders (default: everything)"
        )
        sp.add_argument(
            "--exclude",
            action="append",
            default=[],
            help="glob matched against each path component (repeatable)",
        )
        sp.add_argument(
            "--no-cache", action="store_true", help="ignore and do not write the scan cache"
        )
        if dest:
            sp.add_argument(
                "--dest-image",
                action="store_true",
                help="write into a case-sensitive APFS disk image (SmartTimeArchive.sparsebundle) "
                "created inside DEST; for destinations without hard links (exFAT, NTFS, SMB)",
            )

    common(sub.add_parser("list", help="list snapshots"), dest=False)
    common(sub.add_parser("plan", help="scan and show sizes / space check"), dest=True)
    ex = sub.add_parser("extract", help="extract snapshots to dated folders")
    common(ex, dest=True)
    ex.add_argument(
        "--yes", action="store_true", help="accept warnings (e.g. no hard-link support)"
    )
    return p


def _csv(v):
    return [x for x in (v or "").split(",") if x]


def _source(args):
    return (
        core.DirSource(args.source) if args.source_type == "dir" else core.ApfsSource(args.source)
    )


def _plan_text(plan):
    lines = [
        f"Snapshots selected:      {plan['snapshots']}",
        f"Files (all snapshots):   {plan['files']:,}  ({plan['unique_files']:,} unique)",
        f"Size WITH hard links:    {_gb(plan['bytes_with_links'])}",
        f"Size WITHOUT hard links: {_gb(plan['bytes_without_links'])}",
        f"Destination hard links:  {'yes' if plan['dest_hardlinks'] else 'NO'}"
        + (
            f"  (via disk image; the disk itself: {'yes' if plan['host_hardlinks'] else 'NO'})"
            if plan["via_image"]
            else ""
        ),
        f"Destination case-insens: {'yes' if plan['dest_case_insensitive'] else 'no'}",
        f"Destination free:        {_gb(plan['dest_free'])}",
        f"Space needed (+margin):  {_gb(plan['bytes_needed_with_margin'])}   -> "
        + ("fits" if plan["fits"] else "DOES NOT FIT"),
    ]
    return "\n".join(lines)


def main(argv=None):
    args = _parser().parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress must show up through pipes/tee
    if args.source_type == "apfs" and os.geteuid() != 0:
        sys.exit("Mounting snapshots needs root: run with sudo.")
    if args.source_type == "apfs":
        cleanup_stale_mounts()
    try:
        source = _source(args)
        all_dates = [sn.date for sn in source.snapshots()]
    except ApfsError as e:
        sys.exit(f"error: {e}")
    if args.cmd == "list":
        print("\n".join(all_dates) or "no Time Machine snapshots found")
        return 0

    dates = core.select_dates(all_dates, _csv(args.dates), args.last, args.one_per_day)
    if not dates:
        sys.exit("no snapshots match the selection")
    opts = core.Options(_csv(args.users), _csv(args.folders), args.exclude)
    work = tempfile.mkdtemp(prefix="sta_work_")
    try:
        return _run(args, source, dates, opts, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _extract(source, dest, conn, dates, plan, cancelled):
    ext = core.Extractor(
        source,
        dest,
        conn,
        dates,
        cancel=lambda: bool(cancelled),
        case_insensitive=plan["dest_case_insensitive"],
    )
    return ext.run(), ext.report


def _run(args, source, dates, opts, work):
    use_cache = args.source_type == "apfs" and not args.no_cache
    pending = core.uncached_dates(source, dates, opts) if use_cache else dates
    if pending:
        print(
            f"NOTE: {len(pending)} of {len(dates)} snapshots must be scanned. This reads the metadata "
            "of every file and can take\nseveral minutes per snapshot on an old or slow disk. "
            "It happens once: results are cached."
        )
    conn, scan_errors = core.scan(
        source, dates, opts, os.path.join(work, "scan.db"), use_cache=use_cache
    )
    plan = core.make_plan(conn, args.dest, via_image=args.dest_image)
    print(_plan_text(plan))
    n_err = sum(len(v) for v in scan_errors.values())
    if n_err:
        print(
            f"WARNING: {n_err} entries could not be read during the scan (see report after extract)"
        )
    if args.cmd == "plan":
        return 0 if plan["fits"] else 2

    if not plan["fits"]:
        sys.exit("Not enough free space on the destination. Nothing was copied.")
    if not plan["dest_hardlinks"] and not args.yes:
        sys.exit(
            "Destination does not support hard links: every snapshot would be stored in full "
            f"({_gb(plan['bytes_without_links'])} instead of {_gb(plan['bytes_with_links'])}).\n"
            "Re-run with --dest-image to write into an APFS disk image on it (recommended), "
            "or with --yes to store everything in full."
        )

    cancelled = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: cancelled.append(1))
    if args.dest_image:
        img = os.path.join(args.dest, image.IMAGE_NAME)
        size = image.image_size_for(plan["bytes_needed_with_margin"])
        print(f"Using disk image {img} (max {_gb(size)}, grows as needed)")
        with image.ImageMount(img, size) as im:
            status, report = _extract(source, im.mountpoint, conn, dates, plan, cancelled)
        if report.get("report_file"):  # the mount point is gone after detaching
            report["report_file"] = report["report_file"].replace(im.mountpoint, img + " (inside)")
        if im.detached is False:
            print(f'WARNING: could not detach the image; run: hdiutil detach "{im.mountpoint}"')
        print(f"Archive is inside {img} (double-click it to open)")
    else:
        status, report = _extract(source, args.dest, conn, dates, plan, cancelled)
    print(f"\nStatus: {status}\nReport: {report.get('report_file')}")
    return {core.COMPLETED: 0, core.COMPLETED_WITH_ERRORS: 3, core.CANCELLED: 130}.get(status, 1)


if __name__ == "__main__":
    sys.exit(main())
