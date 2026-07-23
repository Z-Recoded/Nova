# state_writer.py
# Writes every Browser Hands run into a new browser_tasks table in
# nova_state.db, so a scrape is a queryable event rather than a loose file.
# See the Build Spec, Section 2.5, for the exact column schema.
#
# This is a separate table from nova_state.py's domain_state table, not an
# entity within it — domain_state holds current-state snapshots per
# domain/entity, while browser_tasks is an append-only event log (one row
# per run), a genuinely different shape. Shares nova_state.py's DB_PATH
# constant (same nova_state.db file), nothing else.

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from nova_state import DB_PATH


@dataclass
class RunResult:
    """
    One adapter run's outcome — matches the Build Spec's Section 2.2 adapter
    contract exactly. `errors` is a list of short error strings, not full
    tracebacks (nova_state.db stores summaries, not full payloads).
    """

    adapter: str
    target: str
    status: str  # "success" | "partial" | "error"
    items_processed: int
    items_saved: int
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def _get_connection() -> sqlite3.Connection:
    """Open a connection to nova_state.db, creating the browser_tasks table if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adapter TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            items_processed INTEGER NOT NULL,
            items_saved INTEGER NOT NULL,
            result_summary TEXT,
            source_url TEXT,
            captured_at TEXT NOT NULL,
            error_detail TEXT
        )
        """
    )
    conn.commit()
    return conn


def record_run(result: RunResult, source_url: str | None = None) -> None:
    """
    Write one RunResult as a new browser_tasks row. `captured_at` is
    generated here at write time (not taken from RunResult) — it's
    specifically about when this state was recorded, so anything reading it
    later can tell a fresh scrape from a stale one, per the Build Spec's own
    warning against treating an old scrape as current truth.
    """
    result_summary = (
        "; ".join(result.errors) if result.errors else f"{result.items_saved}/{result.items_processed} saved"
    )  # noqa: E501
    error_detail = "; ".join(result.errors) if result.errors else None

    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO browser_tasks "
            "(adapter, target, status, items_processed, items_saved, result_summary, source_url, captured_at, error_detail) "  # noqa: E501
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.adapter,
                result.target,
                result.status,
                result.items_processed,
                result.items_saved,
                result_summary,
                source_url,
                datetime.now().isoformat(timespec="seconds"),
                error_detail,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_runs(adapter: str | None = None, limit: int = 20) -> list[dict]:
    """
    Read back the most recent browser_tasks rows, newest first. If `adapter`
    is given, only rows for that adapter are returned.
    """
    conn = _get_connection()
    try:
        if adapter is not None:
            rows = conn.execute(
                "SELECT * FROM browser_tasks WHERE adapter = ? ORDER BY id DESC LIMIT ?",
                (adapter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM browser_tasks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        columns = [description[0] for description in conn.execute("SELECT * FROM browser_tasks LIMIT 0").description]
    finally:
        conn.close()

    return [dict(zip(columns, row, strict=True)) for row in rows]
