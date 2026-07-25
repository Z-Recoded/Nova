# nova_log_rotation.py
# Nova Log rotation (ClickUp 86barby7t) -- keeps the append-only Nova Log
# telemetry files from growing unbounded. Two rules, applied together and
# non-destructively:
#   1. Archive every entry older than MAX_AGE_DAYS (90 days).
#   2. Keep at most MAX_ACTIVE_ENTRIES (1000) entries in each active file --
#      anything past the most-recent 1000 is archived too.
# Archived entries are never deleted: they are appended to month-stamped
# archive files under logs/archive/ (e.g. query_log_archive_2026-07.jsonl),
# and only then is the active file rewritten with the entries that survive.
#
# Scope, deliberately narrow (see 86barby7t summary):
#   ROTATABLE_LOGS lists only the two real "Nova Log" telemetry files --
#   query_log.jsonl and benchmark_log.jsonl (the ones nova_log.py and the
#   /nova-log dashboard read). Their only consumers show "recent" data, so
#   rotating old entries out is exactly what's wanted and breaks nothing.
#   The codebase's other append-only JSONL logs (training_flags.jsonl,
#   tool_call_log.jsonl, agent_log.jsonl, scheduled_dispatch_log.jsonl, ...)
#   are intentionally NOT rotated here: each feeds a consumer that reads the
#   FULL history (the DPO training corpus, the /label-queue judge-pass, the
#   unreviewed-dispatch diff), so rotating them needs a per-log decision, not
#   a blanket sweep. Adding one later is a one-line append to ROTATABLE_LOGS
#   plus a check that its consumers tolerate a truncated active file.
#
# Scheduling: this is a standalone CLI meant to be run weekly by the real
# scheduler (a user crontab entry on the Omen, matching nova_scheduled_
# dispatch.py's pattern) -- NOT wired into nova_watcher.py, which is deferred
# and not running. Suggested weekly cron line on the Omen:
#   0 4 * * 0 cd ~/nova && nova-env/bin/python nova_log_rotation.py
# (Sunday 04:00). Deliberately not auto-installed -- same manual-trigger
# discipline as nova_omen_sync.py.

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────
# Resolved relative to this file's own location, never a hardcoded Windows
# path -- same bug class already fixed across nova_log.py / nova_logger.py /
# nova_corrector.py (86bb1pkpb). logs/ (and thus logs/archive/) is gitignored,
# so archived telemetry stays machine-local and is never committed.
LOGS_DIR = Path(__file__).resolve().parent / "logs"
ARCHIVE_DIR = LOGS_DIR / "archive"

# The two real Nova Log telemetry files. See the module header for why the
# other append-only JSONL logs are deliberately excluded from the default set.
ROTATABLE_LOGS = [
    "query_log.jsonl",
    "benchmark_log.jsonl",
]

MAX_AGE_DAYS = 90  # entries older than this are archived out of the active file
MAX_ACTIVE_ENTRIES = 1000  # most-recent entries kept in the active file
TIMESTAMP_FIELD = "timestamp"  # ISO-8601 field every Nova Log entry carries


# ── Helpers ────────────────────────────────────────────────────
def _read_lines(path: Path) -> list[tuple[str, dict | None]]:
    """
    Read a JSONL log into (raw_line, parsed_dict) pairs, preserving file order.

    The raw line text is kept verbatim so archived entries are written back
    byte-for-byte, with no reserialization drift. A line that won't parse as
    JSON gets parsed_dict=None -- it is never dropped; downstream logic keeps
    such lines rather than risk losing data. Blank lines are skipped.
    """
    if not path.exists():
        return []

    entries: list[tuple[str, dict | None]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            entries.append((stripped, parsed))
    return entries


def _parse_timestamp(parsed: dict | None) -> datetime | None:
    """
    Pull the ISO-8601 timestamp out of a parsed entry, or None if it's missing,
    the wrong type, or unparseable. None means "age unknown" -- callers treat
    such an entry as too-recent-to-archive so its age can never cause data loss.
    """
    if parsed is None:
        return None
    raw = parsed.get(TIMESTAMP_FIELD)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _split_entries(
    entries: list[tuple[str, dict | None]],
    now: datetime,
    max_age_days: int,
    max_active_entries: int,
) -> tuple[list[tuple[str, dict | None]], list[tuple[str, dict | None]]]:
    """
    Split entries into (kept, archived), preserving original file order in both.

    An entry is archived when either rotation rule says so:
      - it is older than max_age_days, OR
      - it falls outside the most-recent max_active_entries by position.
    An entry survives in the active file only if it satisfies BOTH rules
    (recent enough AND within the last max_active_entries). Entries with an
    unknown age (malformed line or missing timestamp) are never archived by
    age -- they can only leave the active file via the position rule, so a
    bad timestamp can never silently expire a record.
    """
    cutoff = now - timedelta(days=max_age_days)
    total = len(entries)
    count_window_start = max(0, total - max_active_entries)

    kept: list[tuple[str, dict | None]] = []
    archived: list[tuple[str, dict | None]] = []

    for index, (raw, parsed) in enumerate(entries):
        within_count_window = index >= count_window_start
        timestamp = _parse_timestamp(parsed)
        too_old = timestamp is not None and timestamp < cutoff

        if within_count_window and not too_old:
            kept.append((raw, parsed))
        else:
            archived.append((raw, parsed))

    return kept, archived


def _archive_month_key(parsed: dict | None, now: datetime) -> str:
    """
    Month bucket (YYYY-MM) an archived entry belongs to, taken from its own
    timestamp so archives partition naturally by when the entry happened.
    Entries with an unknown age fall back to the current run's month.
    """
    timestamp = _parse_timestamp(parsed)
    when = timestamp if timestamp is not None else now
    return when.strftime("%Y-%m")


def _archive_path(log_filename: str, month_key: str) -> Path:
    """
    Build the month-stamped archive path for a log, e.g.
    query_log.jsonl + "2026-07" -> logs/archive/query_log_archive_2026-07.jsonl.
    """
    stem = log_filename[: -len(".jsonl")] if log_filename.endswith(".jsonl") else log_filename
    return ARCHIVE_DIR / f"{stem}_archive_{month_key}.jsonl"


def _write_archives(log_filename: str, archived: list[tuple[str, dict | None]], now: datetime) -> dict[str, int]:
    """
    Append archived entries to their month-stamped archive files (mode "a", so
    a weekly run adds to the same month's file rather than overwriting it).
    Returns {archive_filename: entries_written}. Non-destructive: this only
    ever appends -- it is called before the active file is rewritten.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    by_month: dict[str, list[str]] = {}
    for raw, parsed in archived:
        by_month.setdefault(_archive_month_key(parsed, now), []).append(raw)

    written: dict[str, int] = {}
    for month_key, raw_lines in by_month.items():
        path = _archive_path(log_filename, month_key)
        with open(path, "a", encoding="utf-8") as f:
            for raw in raw_lines:
                f.write(raw + "\n")
        written[path.name] = len(raw_lines)
    return written


def _rewrite_active(path: Path, kept: list[tuple[str, dict | None]]) -> None:
    """
    Atomically rewrite the active log with only the kept entries: write to a
    sibling temp file, then os.replace() it over the original. A crash mid-write
    leaves the original untouched rather than a half-written log.
    """
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        for raw, _parsed in kept:
            f.write(raw + "\n")
    os.replace(temp_path, path)


# ── Core ───────────────────────────────────────────────────────
def rotate_log_file(
    log_filename: str,
    now: datetime | None = None,
    max_age_days: int = MAX_AGE_DAYS,
    max_active_entries: int = MAX_ACTIVE_ENTRIES,
    dry_run: bool = False,
) -> dict:
    """
    Rotate one log file in LOGS_DIR by both rules (age + count), non-destructively.

    Reads the file, splits it into kept/archived, appends the archived entries
    to month-stamped archive files, then rewrites the active file with the kept
    entries. With dry_run=True it computes and reports the same split but writes
    nothing. A missing file is a no-op ("status": "missing"). Returns a stats
    dict describing what happened (or would happen).
    """
    if now is None:
        now = datetime.now()

    path = LOGS_DIR / log_filename
    if not path.exists():
        return {
            "file": log_filename,
            "status": "missing",
            "total": 0,
            "kept": 0,
            "archived": 0,
            "malformed": 0,
            "archives": {},
        }

    entries = _read_lines(path)
    kept, archived = _split_entries(entries, now, max_age_days, max_active_entries)
    malformed = sum(1 for _raw, parsed in entries if parsed is None)

    if not archived:
        # Nothing to rotate -- leave the active file completely untouched.
        return {
            "file": log_filename,
            "status": "unchanged",
            "total": len(entries),
            "kept": len(kept),
            "archived": 0,
            "malformed": malformed,
            "archives": {},
        }

    if dry_run:
        # Report the split without writing archives or rewriting the active file.
        preview: dict[str, int] = {}
        for _raw, parsed in archived:
            archive_name = _archive_path(log_filename, _archive_month_key(parsed, now)).name
            preview[archive_name] = preview.get(archive_name, 0) + 1
        return {
            "file": log_filename,
            "status": "dry_run",
            "total": len(entries),
            "kept": len(kept),
            "archived": len(archived),
            "malformed": malformed,
            "archives": preview,
        }

    archives_written = _write_archives(log_filename, archived, now)
    _rewrite_active(path, kept)

    return {
        "file": log_filename,
        "status": "rotated",
        "total": len(entries),
        "kept": len(kept),
        "archived": len(archived),
        "malformed": malformed,
        "archives": archives_written,
    }


def rotate_all(
    now: datetime | None = None,
    max_age_days: int = MAX_AGE_DAYS,
    max_active_entries: int = MAX_ACTIVE_ENTRIES,
    dry_run: bool = False,
) -> dict:
    """
    Rotate every file in ROTATABLE_LOGS and return a combined report. This is
    the weekly-cron entry point. now is injectable so callers (and tests) can
    pin the reference time; it defaults to datetime.now().
    """
    if now is None:
        now = datetime.now()

    results = [
        rotate_log_file(
            log_filename,
            now=now,
            max_age_days=max_age_days,
            max_active_entries=max_active_entries,
            dry_run=dry_run,
        )
        for log_filename in ROTATABLE_LOGS
    ]

    return {
        "ran_at": now.isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "max_age_days": max_age_days,
        "max_active_entries": max_active_entries,
        "total_archived": sum(r["archived"] for r in results),
        "files": results,
    }


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rotate the Nova Log telemetry files -- archive entries older than "
        f"{MAX_AGE_DAYS} days and keep the last {MAX_ACTIVE_ENTRIES} active. Non-destructive."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be archived without writing anything.",
    )
    parser.add_argument(
        "--file",
        metavar="NAME",
        help=f"Rotate only this one log file (must be one of: {', '.join(ROTATABLE_LOGS)}).",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=MAX_AGE_DAYS,
        help=f"Archive entries older than this many days (default {MAX_AGE_DAYS}).",
    )
    parser.add_argument(
        "--max-active",
        type=int,
        default=MAX_ACTIVE_ENTRIES,
        help=f"Keep at most this many entries in each active file (default {MAX_ACTIVE_ENTRIES}).",
    )
    args = parser.parse_args()

    if args.file:
        if args.file not in ROTATABLE_LOGS:
            parser.error(f"--file must be one of: {', '.join(ROTATABLE_LOGS)}")
        report = rotate_log_file(
            args.file,
            max_age_days=args.max_age_days,
            max_active_entries=args.max_active,
            dry_run=args.dry_run,
        )
    else:
        report = rotate_all(
            max_age_days=args.max_age_days,
            max_active_entries=args.max_active,
            dry_run=args.dry_run,
        )

    print(json.dumps(report, indent=2))
