# nova_domain_backfill.py
# Nova Tutor Phase 1 (86bawnkbv) — one-off backfill of the new required
# `domain` metadata field onto every chunk ingested before this field
# existed. Run by hand once after ingest.py/nova_sources.py's domain changes
# land; not wired into run_ingestion() or any cron.
#
# Uses collection.update() -- a metadata-only patch that does NOT re-embed
# the document -- rather than a full re-ingest, which would needlessly
# re-embed the entire corpus just to add one field.

import sys

from ingest import collection

BATCH_SIZE = 100
BACKFILL_DOMAIN = "lore"  # every chunk ingested before Phase 1 came from the lore-only SOURCES config


def backfill_lore_domain(batch_size: int = BATCH_SIZE) -> int:
    """
    Sets domain: "lore" on every existing chunk that doesn't already have a
    domain field. Idempotent -- chunks that already have domain set (from a
    prior run, or from the updated ingest_file() going forward) are skipped,
    so this is safe to re-run.
    """
    existing = collection.get(include=["metadatas"])
    ids = existing["ids"]
    metadatas = existing["metadatas"]

    to_update_ids = []
    to_update_metadatas = []
    for chunk_id, metadata in zip(ids, metadatas, strict=True):
        if metadata.get("domain"):
            continue
        metadata["domain"] = BACKFILL_DOMAIN
        to_update_ids.append(chunk_id)
        to_update_metadatas.append(metadata)

    total = len(to_update_ids)
    print(f"{total} chunk(s) missing domain -- backfilling to '{BACKFILL_DOMAIN}' ({len(ids)} total chunks checked)")

    for start in range(0, total, batch_size):
        batch_ids = to_update_ids[start : start + batch_size]
        batch_metadatas = to_update_metadatas[start : start + batch_size]
        collection.update(ids=batch_ids, metadatas=batch_metadatas)
        print(f"  ✓ backfilled {min(start + batch_size, total)}/{total}")

    return total


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    count = backfill_lore_domain()
    print(f"\nDone. {count} chunk(s) backfilled.")
