# nova_state.py
# nova_state.db — the unified SQLite state layer (Architecture Principles
# v1.1, Principle 6). Distinct from Chroma: Chroma is deep knowledge (lore,
# documents, past reality), nova_state.db is current reality (live
# balances, active projects, system health) — they must never be
# conflated. See CLAUDE.md's "Domain State Layer" section for what's
# actually wired up vs. still a ready-but-empty entity.
#
# One generic table (domain, entity, data JSON, updated_at) holds every
# domain node Principle 6 describes, rather than a fixed per-entity column
# schema — the design doc specifies which domain/entity pairs exist, not
# their exact fields, and several (financial, work, creative, games) have
# no real data source wired up yet. A generic shape means adding real
# fields later is a data change, not a migration.
#
# Adapters (nova_state_<domain>.py) are the only intended writers.
# Everything else reads via get_state()/get_domain().

import json
import sqlite3
from datetime import datetime

DB_PATH = "C:/Nova/nova_state.db"

# Every domain/entity pair Principle 6 defines, even if no adapter writes
# to it yet — lets get_domain() return a real "not yet populated" answer
# instead of confusing an unbuilt adapter with a missing table.
# system/claude_usage_history, system/dispatch_pause,
# system/claude_activity_profile, and system/pending_escalations are all
# deliberate extensions beyond Principle 6's original list (2026-07-14,
# 2026-07-15, 2026-07-18). Usage history is cross-machine Claude Code usage
# data pushed by nova_usage_logger.py; dispatch_pause is the pause-at-will
# switch nova_escalation.py exposes to nova_omen_dispatch.py;
# claude_activity_profile is the cross-machine hour-of-day/day-of-week
# Claude Code activity histogram, also pushed by nova_usage_logger.py —
# groundwork for the autonomous-dispatch dual-fuel design (86bawpvzz) to
# find genuine idle windows instead of guessing a fixed reserve.
# pending_escalations (86bax0wkj) is a dict keyed by escalation_id — same
# merge-by-key shape as claude_usage_history/claude_activity_profile, not
# dispatch_pause's flat shape — holding real-time questions a headless
# dispatch raised mid-task, awaiting Marvin's answer via /escalations-ui.
# None of these fit financial/work/creative/games, but all belong in
# system alongside nova_health.
KNOWN_ENTITIES = {
    "financial": ["debt_sequence", "budget_pace", "upcoming_obligations", "subscription_audit", "atm_fees"],
    "work": ["active_projects", "next_actions"],
    "creative": ["art_output_rhythm", "active_characters"],
    "games": ["active_builds"],
    "system": ["nova_health", "pending_alerts", "claude_usage_history", "dispatch_pause", "claude_activity_profile", "pending_escalations"],
}


def _get_connection() -> sqlite3.Connection:
    """Open a connection to nova_state.db, creating the schema if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_state (
            domain TEXT NOT NULL,
            entity TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (domain, entity)
        )
        """
    )
    conn.commit()
    return conn


def _validate_entity(domain: str, entity: str) -> None:
    """Raise ValueError if domain/entity isn't one of Principle 6's defined pairs."""
    if domain not in KNOWN_ENTITIES or entity not in KNOWN_ENTITIES[domain]:
        raise ValueError(
            f"Unknown domain/entity pair: '{domain}/{entity}' — not in Principle 6's schema."
        )


def write_state(domain: str, entity: str, data: dict) -> None:
    """
    Write one domain/entity's current state, overwriting whatever was
    there before. Only adapters (nova_state_<domain>.py modules) should
    call this — everything else reads via get_state()/get_domain().
    """
    _validate_entity(domain, entity)

    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO domain_state (domain, entity, data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(domain, entity) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (domain, entity, json.dumps(data), datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def get_state(domain: str, entity: str) -> dict | None:
    """Read one domain/entity's current state, or None if never written."""
    _validate_entity(domain, entity)

    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT data, updated_at FROM domain_state WHERE domain = ? AND entity = ?",
            (domain, entity),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    data, updated_at = row
    return {**json.loads(data), "_updated_at": updated_at}


def get_domain(domain: str) -> dict:
    """
    Read every known entity for a domain, e.g. get_domain('system') ->
    {'nova_health': {...} | None, 'pending_alerts': {...} | None}.
    None for any entity that has no adapter writing to it yet.
    """
    if domain not in KNOWN_ENTITIES:
        raise ValueError(f"Unknown domain: '{domain}'")
    return {entity: get_state(domain, entity) for entity in KNOWN_ENTITIES[domain]}


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    for domain in KNOWN_ENTITIES:
        print(f"{domain}: {get_domain(domain)}")
