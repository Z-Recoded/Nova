import sys
from datetime import date, timedelta

sys.path.insert(0, "C:/Nova")

import pydantic

from nova_tutor import (
    StruggleEntry,
    _get_connection,
    _prune_struggle_history,
    append_struggle_entry,
    collection,
    get_tutor_chunk,
    get_tutor_state,
    write_tutor_chunk,
)

TEST_TOPIC = "test_phase1_verification"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== 2. Direct-call test: write_tutor_chunk + get_tutor_chunk ===")
    chunk_id = write_tutor_chunk(
        topic=TEST_TOPIC,
        subtopic="verification run",
        parent_topic="testing",
        content="This is a throwaway test chunk for Phase 1 verification.",
        source="phase1_verification_script",
        score=0.4,
        streak=1,
    )
    print(f"chunk_id: {chunk_id}")
    merged = get_tutor_chunk(chunk_id)
    _check(merged["domain"] == "tutor", "domain mismatch")
    _check(merged["topic"] == TEST_TOPIC, "topic mismatch")
    _check(merged["mastery"]["score"] == 0.4, "mastery.score mismatch")
    _check(merged["struggle_history"] == [], "struggle_history should start empty")
    print("get_tutor_chunk() returned correct merged view -- PASS")

    print("\n=== 3. Real Chroma check: mastery/struggle_history NOT in Chroma metadata ===")
    raw = collection.get(ids=[chunk_id], include=["metadatas"])
    raw_metadata = raw["metadatas"][0]
    print(f"Chroma metadata keys: {sorted(raw_metadata.keys())}")
    _check(raw_metadata["domain"] == "tutor", "domain missing from Chroma metadata")
    _check("synthesis_links" in raw_metadata, "synthesis_links missing from Chroma metadata")
    _check("mastery" not in raw_metadata, "mastery leaked into Chroma metadata")
    _check("struggle_history" not in raw_metadata, "struggle_history leaked into Chroma metadata")
    print("Storage split confirmed -- PASS")

    print("\n=== 4. Real SQLite check: mastery/struggle_history round-trip ===")
    conn = _get_connection()
    row = conn.execute(
        "SELECT mastery, struggle_history FROM tutor_chunk_state WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()
    conn.close()
    _check(row is not None, "no SQLite row written for this chunk")
    print(f"Raw SQLite row present: mastery={row[0][:60]}...")
    state = get_tutor_state(chunk_id)
    _check(state["mastery"]["score"] == 0.4, "SQLite mastery.score round-trip mismatch")
    print("SQLite round-trip via get_tutor_state() -- PASS")

    print("\n=== 5. struggle_history pruning: count + age caps ===")
    today = date.today()
    entries = [
        StruggleEntry(date=today - timedelta(days=d), error_type=f"err{i}", your_answer="x", correction="y")
        for i, d in enumerate([1, 2, 3, 4, 5, 6, 40, 50])
    ]
    pruned = _prune_struggle_history(entries, now=today, max_entries=5, max_age_days=30)
    print(f"input: {len(entries)} entries -> pruned: {len(pruned)} entries")
    _check(len(pruned) == 3, f"expected 3 (last 5 by position, then >30d cutoff removes 2 more), got {len(pruned)}")
    _check(all((today - e.date).days <= 30 for e in pruned), "a pruned entry exceeds the 30-day cutoff")
    print("Dual age/count pruning -- PASS")

    print("\n=== 5b. append_struggle_entry() real write path ===")
    append_struggle_entry(chunk_id, error_type="test_error", your_answer="wrong", correction="right")
    state2 = get_tutor_state(chunk_id)
    _check(len(state2["struggle_history"]) == 1, "struggle_history should have exactly 1 entry")
    _check(state2["struggle_history"][0]["error_type"] == "test_error", "struggle entry content mismatch")
    print("append_struggle_entry() -- PASS")

    print("\n=== 6. Pydantic validation rejects bad input before any write ===")
    try:
        write_tutor_chunk(
            topic=TEST_TOPIC,
            subtopic="bad score",
            parent_topic="testing",
            content="should never be written",
            source="phase1_verification_script",
            score=1.5,  # out of range, ge=0.0 le=1.0
        )
        raise AssertionError("Expected a validation error, none raised")
    except pydantic.ValidationError:
        print("Out-of-range score correctly rejected -- PASS")

    count_before = collection.get(where={"topic": {"$eq": TEST_TOPIC}}, include=[])
    print(f"Chunks with test topic after rejected write: {len(count_before['ids'])} (should still be 1)")
    _check(len(count_before["ids"]) == 1, "rejected write left a partial trace in Chroma")

    print("\n=== Cleanup: removing test chunk from Chroma + SQLite ===")
    collection.delete(ids=[chunk_id])
    conn = _get_connection()
    conn.execute("DELETE FROM tutor_chunk_state WHERE chunk_id = ?", (chunk_id,))
    conn.commit()
    conn.close()
    remaining = collection.get(where={"topic": {"$eq": TEST_TOPIC}}, include=[])
    _check(len(remaining["ids"]) == 0, "cleanup left test data behind")
    print("Cleanup confirmed -- no test data left behind")

    print("\n=== ALL PHASE 1 VERIFICATION CHECKS PASSED ===")


if __name__ == "__main__":
    main()
