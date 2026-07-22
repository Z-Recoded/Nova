# nova_logger.py
# Detects character blending mid-chat and logs flagged responses
# as training material (JSONL) and a human-readable review file.

import json
import os
from datetime import datetime

# Resolved relative to this file's own location, not hardcoded to the Aero's
# Windows path -- same bug class already fixed in nova_api.py's GRAPH_PATH,
# nova_config.py's CONFIG_PATH, etc. A hardcoded "C:/Nova/logs" silently
# resolves to a bogus relative path on the Omen (Linux), so log_blend() has
# never actually written a real training_flags.jsonl there.
LOGS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
JSONL_PATH = f"{LOGS_DIR}/training_flags.jsonl"
MD_PATH    = f"{LOGS_DIR}/training_review.md"

# ── Detection ──────────────────────────────────────────────────
def detect_blending(chunks: list[dict], category: str) -> bool:
    """
    Return True when a fiction query pulled chunks from more than one
    character file — the primary signal of cross-character blending.
    """
    if category != "fiction":
        return False
    filenames = {c["metadata"].get("filename", "unknown") for c in chunks}
    return len(filenames) > 1

# ── Logging ────────────────────────────────────────────────────
def log_blend(query: str, answer: str, chunks: list[dict], category: str) -> None:
    """Write one flagged exchange to both JSONL and markdown."""
    os.makedirs(LOGS_DIR, exist_ok=True)

    filenames = sorted({c["metadata"].get("filename", "unknown") for c in chunks})
    timestamp = datetime.now().isoformat(timespec="seconds")

    # ── JSONL entry (fine-tuning / DPO ready) ─────────────────
    entry = {
        "timestamp": timestamp,
        "flag": "multi_source_blend",
        "category": category,
        "sources_mixed": filenames,
        "messages": [
            {"role": "user",      "content": query},
            {"role": "assistant", "content": answer},
        ],
        # Corrected response left blank — fill in during review
        "correction": "",
        # Chunk excerpts for debugging (first 300 chars each)
        "chunk_excerpts": [
            {
                "filename": c["metadata"].get("filename", "unknown"),
                "text":     c["text"][:300],
            }
            for c in chunks
        ],
    }

    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Markdown entry (human review + manual correction) ──────
    sources_str = ", ".join(filenames)
    with open(MD_PATH, "a", encoding="utf-8") as f:
        f.write(f"## {timestamp} — multi-source blend\n\n")
        f.write(f"**Sources mixed:** `{sources_str}`\n\n")
        f.write(f"**Query:**\n> {query}\n\n")
        f.write(f"**Response:**\n{answer}\n\n")
        f.write("**Correction:** *(fill in correct response here)*\n\n")

        f.write("<details><summary>Chunk excerpts</summary>\n\n")
        for exc in entry["chunk_excerpts"]:
            f.write(f"**{exc['filename']}**\n```\n{exc['text']}\n```\n\n")
        f.write("</details>\n\n---\n\n")
