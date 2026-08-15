# nova_corrector.py
# Uses Claude API to auto-correct blended responses in training_flags.jsonl.
# For each flagged entry where correction == "", it:
#   1. Loads the character's source file from the Second Brain for grounding
#   2. Asks Claude to write the accurate response
#   3. Writes the correction back into the JSONL entry
#
# Usage:
#   python nova_corrector.py           # process all uncorrected entries
#   python nova_corrector.py --dry-run # preview without writing anything

import json
import os
import sys

import anthropic
import numpy as np
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

# Windows' default console codepage (cp1252) can't encode characters Claude's
# real corrections sometimes contain (em-dashes, emoji) -- the same recurring
# bug class already fixed in nova_benchmark.py/ingest.py/nova_board.py/
# browser_hands. Confirmed live 2026-07-22: a real correction run crashed
# mid-way on '❓' (an emoji in a generated correction), losing every
# already-paid-for API call made before the crash since save_entries() only
# ran at the very end (see run()'s own incremental-save fix below).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Resolved relative to this file's own location -- same bug class already
# fixed in nova_orchestrator.py's dotenv path. A hardcoded "C:/Nova/.env"
# silently returns False (not a raised error) on the Omen (Linux) instead of
# loading real secrets, and "C:/Nova/logs/training_flags.jsonl" isn't a real
# path there either -- this script has never actually run correctly off the
# Aero.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))

JSONL_PATH = os.path.join(_SCRIPT_DIR, "logs", "training_flags.jsonl")
SECOND_BRAIN = r"C:\Users\marvi\OneDrive\Documents\Second Brain"
CLAUDE_MODEL = "claude-sonnet-4-6"

# nova_benchmark.py's GOLDEN_QUERIES are synthetic queries run repeatedly for
# the base-model swap-trigger benchmark, not real questions Marvin asked --
# spending a real Claude API call correcting a duplicate of the same
# synthetic query is pure waste (11 identical "tell me a story" duplicates
# were corrected before this check existed, found live 2026-08-11).
# Duplicated here rather than imported from nova_benchmark.py to avoid
# pulling in its nova_query import, which connects to Chroma at import time
# -- this script has no other Chroma dependency and shouldn't start needing
# the Omen to be reachable just to run.
GOLDEN_QUERY_STRINGS = {
    "who am i",
    "tell me about null",
    "who is fatale",
    "tell me a story",
    "tell me about the mood garden project",
    "what's my trading strategy",
    "how does nova_query.py work",
    "what's a good way to stay productive",
}


def _is_golden_duplicate(query: str) -> bool:
    """True if query exactly matches (case/whitespace-insensitive) one of nova_benchmark.py's GOLDEN_QUERIES."""
    return query.strip().lower() in GOLDEN_QUERY_STRINGS


# ── Correction reuse cache ─────────────────────────────────────
# Avoids spending a fresh Claude call on a flagged entry that's really a
# repeat of one already corrected. Real duplication confirmed directly
# against this file's own data (2026-08-14 audit): "Who is Null?" recurs 6x
# verbatim, and the Null/Nullius source pair recurs 9x with closely related
# rewordings ("Good, who is Null?"). Two tiers, cheapest and safest first:
#   1. Exact match on normalized query text -- zero risk, the same question
#      asked twice has the same correct answer.
#   2. Embedding similarity, scoped to entries sharing the exact same
#      sources_mixed set -- for near-duplicate rewordings that don't match
#      exactly.
#
# embedding_functions.DefaultEmbeddingFunction() runs a local ONNX model --
# no network call, no dependency on the Omen/Chroma server being reachable.
# This preserves request_correction()'s existing no-Chroma-connectivity
# guarantee (see this file's own dotenv-path comment above) even though it
# does add a chromadb import; both chromadb and numpy are already pinned
# project dependencies (requirements.txt), so this introduces no new one.
SIMILARITY_REUSE_THRESHOLD = 0.90

# Real validated margin behind the 0.90 cutoff (2026-08-14, tested against
# this file's own recorded near-duplicates): a genuine reworded repeat
# ("Good, who is Null?" vs "Who is Null?") scored 0.935 cosine similarity,
# while a genuinely DIFFERENT question sharing the same character ("How old
# is Null?" vs "Who is Null?") scored only 0.792 -- a real ~14-point gap.
# A naive character-level similarity (difflib.SequenceMatcher) was tried
# first and rejected: it scored those same two pairs at 0.800 vs 0.786, a
# gap too thin to safely tell "same question, reworded" apart from
# "different question, same character" -- reusing a correction for the
# wrong question would silently ship a wrong answer as if it were verified.


def _normalize_query(query: str) -> str:
    """Same normalization _is_golden_duplicate() uses -- strip + lowercase, no other transformation."""
    return query.strip().lower()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Standard cosine similarity -- 1.0 is identical direction, 0.0 is orthogonal."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _build_reuse_cache(entries: list[dict], embedding_fn) -> list[dict]:
    """
    One record per already-corrected entry: normalized query text, its
    sources_mixed set (as a sorted tuple, for scoping the similarity search
    below), the correction text itself, and its embedding -- computed once
    here in a single batched call, reused for every comparison against it.
    """
    corrected = [e for e in entries if e.get("correction")]
    if not corrected:
        return []
    queries = [e["messages"][0]["content"] for e in corrected]
    embeddings = embedding_fn(queries)
    return [
        {
            "query_norm": _normalize_query(entry["messages"][0]["content"]),
            "sources": tuple(sorted(entry.get("sources_mixed", []))),
            "correction": entry["correction"],
            "embedding": np.array(embedding),
        }
        for entry, embedding in zip(corrected, embeddings, strict=True)
    ]


def _find_reusable_correction(
    query: str, sources: list[str], query_embedding: np.ndarray, cache: list[dict]
) -> tuple[str, str, float] | None:
    """
    Look for a prior correction this entry can reuse instead of a fresh API
    call. Returns (correction, match_kind, similarity) where match_kind is
    "exact" or "similarity", or None if nothing reusable was found.

    Tier 1 -- exact match on normalized query text, against any source set.
    Tier 2 -- embedding similarity, but ONLY against cache entries sharing
    this entry's exact sources_mixed set (the same character-file pair) --
    a cheap pre-filter that also doubles as a safety net, since a
    same-character near-duplicate is a fundamentally safer reuse than a
    coincidentally-similar-sounding question about different characters.
    """
    query_norm = _normalize_query(query)
    for record in cache:
        if record["query_norm"] == query_norm:
            return record["correction"], "exact", 1.0

    sources_key = tuple(sorted(sources))
    best_record, best_similarity = None, 0.0
    for record in cache:
        if record["sources"] != sources_key:
            continue
        similarity = _cosine_similarity(query_embedding, record["embedding"])
        if similarity > best_similarity:
            best_record, best_similarity = record, similarity

    if best_record and best_similarity >= SIMILARITY_REUSE_THRESHOLD:
        return best_record["correction"], "similarity", best_similarity
    return None


# ── File lookup ────────────────────────────────────────────────
def find_character_file(filename: str) -> str | None:
    """Search the Second Brain for a file matching filename."""
    for root, _, files in os.walk(SECOND_BRAIN):
        if filename in files:
            return os.path.join(root, filename)
    return None


def load_lore(filenames: list[str]) -> str:
    """Load and concatenate lore content for each source file."""
    blocks = []
    for fname in filenames:
        path = find_character_file(fname)
        if not path:
            blocks.append(f"[{fname}]\n(file not found)")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                blocks.append(f"[{fname}]\n{f.read()}")
        except Exception as e:
            blocks.append(f"[{fname}]\n(error reading file: {e})")
    return "\n\n---\n\n".join(blocks)


# ── Correction via Claude ──────────────────────────────────────
def request_correction(client: anthropic.Anthropic, query: str, lore: str) -> str:
    """Ask Claude to write an accurate response grounded only in the lore provided."""
    system = (
        "You are correcting responses from a local AI assistant that blended "
        "details across multiple fictional characters. "
        "You will be given the source lore files and the original query. "
        "Write a concise, accurate response that draws ONLY from the lore provided. "
        "Do not invent details. Do not mix attributes across characters. "
        "Match the tone of a knowledgeable assistant answering a direct question."
    )
    # cache_control: lore is loaded verbatim from the same Second Brain
    # source files across many flagged entries in one run (e.g. several
    # entries about the same character) — splitting it into its own cached
    # block lets repeat calls skip re-billing that text. system is cached
    # too for correctness, though at its current size it's under
    # Anthropic's minimum cacheable length so it won't yet produce a hit.
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=[
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Lore source files:\n\n{lore}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": f"---\n\nQuery: {query}\n\nWrite the correct response:",
                    },
                ],
            }
        ],
    )
    # Same ThinkingBlock gotcha nova_orchestrator._review_coding_diff() and
    # nova_task_queue.propose_tier() already found live (86bb53hmk):
    # message.content[0] is not reliably the text block -- this account/
    # model can return a leading ThinkingBlock (no .text attribute) before
    # the real TextBlock. Find the first block with type "text" explicitly
    # rather than assuming index 0.
    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("No text block in Claude's response.")
    return text_blocks[0].strip()


# ── JSONL read / write ─────────────────────────────────────────
def load_entries() -> list[dict]:
    if not os.path.exists(JSONL_PATH):
        return []
    entries = []
    with open(JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def save_entries(entries: list[dict]) -> None:
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Main ───────────────────────────────────────────────────────
def run(dry_run: bool = False) -> None:
    entries = load_entries()
    uncorrected = [e for e in entries if not e.get("correction")]
    duplicates = [e for e in uncorrected if _is_golden_duplicate(e["messages"][0]["content"])]
    pending = [e for e in uncorrected if e not in duplicates]

    if duplicates:
        entry_word = "entry" if len(duplicates) == 1 else "entries"
        print(f"Skipping {len(duplicates)} golden-benchmark duplicate {entry_word} (not real questions).")

    if not pending:
        print("No uncorrected entries found.")
        return

    print(f"Found {len(pending)} uncorrected entr{'y' if len(pending) == 1 else 'ies'}.")
    if dry_run:
        print("Dry run — no changes will be written.\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set. Export it before running this script.")
    client = anthropic.Anthropic(api_key=api_key)
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    reuse_cache = _build_reuse_cache(entries, embedding_fn)

    generated = 0
    reused = 0
    for entry in entries:
        if entry.get("correction"):
            continue
        if _is_golden_duplicate(entry["messages"][0]["content"]):
            continue

        query = entry["messages"][0]["content"]
        sources = entry.get("sources_mixed", [])

        print(f"\nQuery : {query[:80]}{'...' if len(query) > 80 else ''}")
        print(f"Sources: {', '.join(sources)}")

        query_embedding = np.array(embedding_fn([query])[0])
        reuse = _find_reusable_correction(query, sources, query_embedding, reuse_cache)

        if reuse:
            correction, match_kind, similarity = reuse
            if dry_run:
                if match_kind == "exact":
                    print("(dry run — would reuse an exact prior match, no API call)")
                else:
                    print(f"(dry run — would reuse a similar prior match, similarity={similarity:.3f}, no API call)")
                # Real correction text -- safe to add to the cache so a later
                # entry in this same dry-run preview can chain off it too.
                reuse_cache.append(
                    {
                        "query_norm": _normalize_query(query),
                        "sources": tuple(sorted(sources)),
                        "correction": correction,
                        "embedding": query_embedding,
                    }
                )
                continue

            entry["correction"] = correction
            entry["correction_source"] = "reused_exact" if match_kind == "exact" else "reused_similarity"
            reused += 1
            reuse_cache.append(
                {
                    "query_norm": _normalize_query(query),
                    "sources": tuple(sorted(sources)),
                    "correction": correction,
                    "embedding": query_embedding,
                }
            )
            save_entries(entries)

            if match_kind == "exact":
                print("Reused an exact prior correction — no API call.")
            else:
                print(f"Reused a similar prior correction (similarity={similarity:.3f}) — no API call.")
            continue

        if dry_run:
            print("(dry run — would call the API to generate a fresh correction)")
            continue

        lore = load_lore(sources)
        correction = request_correction(client, query, lore)
        entry["correction"] = correction
        entry["correction_source"] = "generated"
        generated += 1
        reuse_cache.append(
            {
                "query_norm": _normalize_query(query),
                "sources": tuple(sorted(sources)),
                "correction": correction,
                "embedding": query_embedding,
            }
        )

        # Saved after every single correction, not once at the end -- a real
        # run crashed mid-way on an encoding bug (fixed above) and threw away
        # every already-paid-for API call made before the crash, since the
        # old code only wrote once after the whole loop finished. Rewriting
        # the full ~50-entry file per correction is trivially cheap I/O; a
        # lost real Claude API call is not.
        save_entries(entries)

        print(f"Correction: {correction[:120]}{'...' if len(correction) > 120 else ''}")

    if not dry_run and (generated or reused):
        print(
            f"\n{generated} correction(s) generated via API, {reused} reused from a prior match "
            f"— written to {JSONL_PATH}"
        )
    elif not dry_run:
        print("Nothing to write.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
