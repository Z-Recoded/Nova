# nova_tutor.py
# Nova Tutor Phase 1 (86bawnkbv) — tutor chunk schema, writer, and storage.
#
# Schema is split across two backends, deliberately deviating from the design
# doc's literal "everything in one Chroma chunk" shape: domain/topic/subtopic/
# parent_topic/source/chunk_id/content/synthesis_links live in Chroma (low-churn,
# needed for Phase 3's $eq filtering and Phase 5's link-gated retrieval); mastery
# and struggle_history live in a dedicated SQLite table instead, because Chroma
# here is Omen-hosted over HttpClient and a .upsert() re-embeds the whole
# document on every write — real, foreseeable cost once Phase 2's scheduler
# updates mastery on every quiz answer, not something worth discovering later.
#
# mastery/struggle_history deliberately do NOT go through nova_state.py's
# write_state()/domain_state table -- _validate_entity() there hard-whitelists
# domain/entity pairs against KNOWN_ENTITIES, a finite set for Principle 6's
# system/financial/work/etc. domains. Tutor mastery is keyed per-chunk_id, an
# unbounded/dynamic key space that breaks that whitelist's contract. Instead,
# this module owns its own table in the same physical DB file.

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ingest import client, collection, content_token_budget  # noqa: F401 (client re-exported for callers)
from nova_state import DB_PATH

# ── Schema ─────────────────────────────────────────────────────


class MasteryModel(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    last_tested: date
    next_review: date
    streak: int = Field(ge=0)


class StruggleEntry(BaseModel):
    date: date
    error_type: str
    your_answer: str
    correction: str


class SynthesisLink(BaseModel):
    to: str
    relation: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["pending", "approved", "rejected"] = "pending"


class TutorChunk(BaseModel):
    chunk_id: str
    domain: Literal["tutor"] = "tutor"
    topic: str
    subtopic: str
    parent_topic: str
    mastery: MasteryModel
    struggle_history: list[StruggleEntry] = []
    synthesis_links: list[SynthesisLink] = []
    source: str
    content: str


# ── SQLite state (mastery + struggle_history — see module docstring) ───────


def _get_connection() -> sqlite3.Connection:
    """Open a connection to nova_state.db's tutor_chunk_state table, creating it if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tutor_chunk_state (
            chunk_id TEXT PRIMARY KEY,
            mastery TEXT NOT NULL,
            struggle_history TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _write_tutor_state(chunk_id: str, mastery: dict, struggle_history: list[dict]) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO tutor_chunk_state (chunk_id, mastery, struggle_history, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET mastery = excluded.mastery, "
            "struggle_history = excluded.struggle_history, updated_at = excluded.updated_at",
            (
                chunk_id,
                json.dumps(mastery),
                json.dumps(struggle_history),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_tutor_state(chunk_id: str) -> dict | None:
    """Returns {"mastery": {...}, "struggle_history": [...]} for one chunk_id, or None if unwritten."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT mastery, struggle_history FROM tutor_chunk_state WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"mastery": json.loads(row[0]), "struggle_history": json.loads(row[1])}


def get_tutor_chunk(chunk_id: str) -> dict:
    """Merges the Chroma-side fields (domain/topic/content/etc.) with the SQLite-side
    mastery/struggle_history for one chunk_id into a single dict matching the logical §3 schema."""
    result = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not result["ids"]:
        raise ValueError(f"No chunk found for chunk_id '{chunk_id}'")
    metadata = result["metadatas"][0]
    document = result["documents"][0]

    state = get_tutor_state(chunk_id) or {"mastery": {}, "struggle_history": []}
    synthesis_links = json.loads(metadata.get("synthesis_links") or "[]")

    return {
        "chunk_id": chunk_id,
        "domain": metadata["domain"],
        "topic": metadata["topic"],
        "subtopic": metadata["subtopic"],
        "parent_topic": metadata["parent_topic"],
        "source": metadata["source"],
        "content": document,
        "mastery": state["mastery"],
        "struggle_history": state["struggle_history"],
        "synthesis_links": synthesis_links,
    }


# ── struggle_history pruning (write-triggered — see nova_log_rotation.py's ─
# ── _split_entries() for the same dual age/count algorithm on a different  ─
# ── data shape: JSONL log files, not a list nested in one chunk's state)   ─


def _prune_struggle_history(
    entries: list[StruggleEntry],
    now: date,
    max_entries: int = 5,
    max_age_days: int = 30,
) -> list[StruggleEntry]:
    """
    Keep an entry only if it's within the most-recent max_entries by position
    AND not older than max_age_days by its own date -- whichever cap is
    stricter fires first. No sweep job -- called write-triggered on every
    append, matching nova_log_rotation.py's dual-condition retention logic.
    """
    cutoff = now - timedelta(days=max_age_days)
    count_window_start = max(0, len(entries) - max_entries)
    return [e for i, e in enumerate(entries) if i >= count_window_start and e.date >= cutoff]


def append_struggle_entry(chunk_id: str, error_type: str, your_answer: str, correction: str) -> None:
    """
    Records one real quiz miss against a tutor chunk, pruning the history to
    the hybrid retention rule before writing. The judgment call of WHETHER an
    answer counts as a struggle (freeform answer evaluation) is Phase 2's
    concern -- this function only owns the entry point + pruning + write.
    """
    state = get_tutor_state(chunk_id)
    if state is None:
        raise ValueError(f"No tutor state for chunk_id '{chunk_id}' -- write the chunk first")

    existing = [StruggleEntry(**e) for e in state["struggle_history"]]
    updated = existing + [
        StruggleEntry(date=date.today(), error_type=error_type, your_answer=your_answer, correction=correction)
    ]
    pruned = _prune_struggle_history(updated, now=date.today())
    _write_tutor_state(chunk_id, state["mastery"], [e.model_dump(mode="json") for e in pruned])


# ── Tutor chunk writer ─────────────────────────────────────────


def _slugify(topic: str) -> str:
    """lowercase, non-alphanumeric runs collapsed to a single underscore, matching chunk_id's own example format."""
    return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")


def _next_seq(topic: str, today_str: str) -> int:
    """
    Real chunk_id collision-avoidance: count existing chunks for this topic
    whose chunk_id already carries today's date stamp. Single-user, no
    concurrent writers at this phase -- the query-then-write race window is a
    non-issue, not worth locking.
    """
    existing = collection.get(where={"topic": {"$eq": topic}}, include=[])
    same_day_count = sum(1 for cid in existing["ids"] if f"_{today_str}_" in cid)
    return same_day_count + 1


def write_tutor_chunk(
    topic: str,
    subtopic: str,
    parent_topic: str,
    content: str,
    source: str,
    score: float = 0.0,
    streak: int = 0,
) -> str:
    """
    Writes one tutor chunk: validates the full logical schema as a single
    TutorChunk (pydantic) before any I/O, then splits it across Chroma
    (identity fields + embedded content + synthesis_links) and the
    tutor_chunk_state SQLite table (mastery + struggle_history) -- see the
    module docstring for why. Returns the new chunk_id.

    One tutor chunk is always exactly one Chroma document -- no multi-chunk
    splitting the way ingest_file() does for long lore documents. mastery/
    struggle_history/synthesis_links are properties of one indivisible note;
    a chunk_index/total_chunks split has no sensible meaning here. If content
    is too long to embed, this raises rather than silently splitting it.
    """
    today = date.today()
    today_str = today.strftime("%Y%m%d")
    seq = _next_seq(topic, today_str)
    chunk_id = f"tutor_{_slugify(topic)}_{today_str}_{seq:03d}"

    tutor_chunk = TutorChunk(
        chunk_id=chunk_id,
        topic=topic,
        subtopic=subtopic,
        parent_topic=parent_topic,
        mastery=MasteryModel(score=score, last_tested=today, next_review=today, streak=streak),
        source=source,
        content=content,
    )  # raises pydantic.ValidationError before any write if the schema is violated

    # content_token_budget() already returns the content-only ceiling (prefix
    # cost pre-subtracted) -- same contract ingest.py's chunk_text() relies on.
    from ingest import get_tokenizer  # local import: only needed for this length check

    budget = content_token_budget(chunk_id)
    content_tokens = len(get_tokenizer().encode(tutor_chunk.content, add_special_tokens=False))
    if content_tokens > budget:
        raise ValueError(
            f"Tutor chunk content too long for one chunk ({chunk_id}: {content_tokens} > {budget} tokens) -- "
            "tutor chunks are never split, shorten the content."
        )
    anchored_content = f"[{chunk_id}]\n{tutor_chunk.content}"

    collection.upsert(
        documents=[anchored_content],
        ids=[chunk_id],
        metadatas=[
            {
                "domain": tutor_chunk.domain,
                "topic": tutor_chunk.topic,
                "subtopic": tutor_chunk.subtopic,
                "parent_topic": tutor_chunk.parent_topic,
                "source": tutor_chunk.source,
                "synthesis_links": json.dumps([link.model_dump(mode="json") for link in tutor_chunk.synthesis_links]),
            }
        ],
    )
    _write_tutor_state(
        chunk_id,
        tutor_chunk.mastery.model_dump(mode="json"),
        [e.model_dump(mode="json") for e in tutor_chunk.struggle_history],
    )
    return chunk_id
