# nova_embedding_viz.py
# Data/logic module backing the Embedding-Space Visualization page (86bawjg14).
# Projects the entire Chroma embedding space to 2D (t-SNE), so Marvin can
# visually audit character cluster overlap -- a spatial way to catch the
# "character blending" retrieval problem this project has fought before.
# Distinct from nova_chunk_viz.py, which debugs a single query's retrieval
# results rather than the whole corpus at once.

import json
import os

import numpy as np
from sklearn.manifold import TSNE

from nova_query import collection
from nova_chunk_viz import DEFAULT_N_RESULTS, FILENAME_TO_CHARACTER, resolve_chunks

# ── Config ─────────────────────────────────────────────────────
OTHER_LABEL = "Other"
TEXT_PREVIEW_CHARS = 160
TRAINING_FLAGS_PATH = "C:/Nova/logs/training_flags.jsonl"

# Fixed so the projection is stable across requests -- without a fixed seed
# the whole map reshuffles every call, and Marvin loses a stable mental map
# of the clusters across a debugging session.
TSNE_RANDOM_STATE = 42
TSNE_PERPLEXITY = 30
TSNE_INIT = "pca"  # deterministic, paired with the fixed seed above

# Module-level cache for the projected points -- t-SNE on this collection's
# real size (~479 chunks) takes a couple of seconds, fast enough to compute
# on first request and reuse for the process's lifetime. No file-based
# cache or cron job; revisit only if the collection grows by orders of
# magnitude, which it isn't close to today.
_cached_points: list[dict] | None = None


# ── Helpers ────────────────────────────────────────────────────
def _character_for_filename(filename: str) -> str:
    """
    Reverse-lookup a chunk's filename against FILENAME_TO_CHARACTER.
    Returns OTHER_LABEL for non-character (non-fiction) sources.

    Note: CHARACTER_FILES has two keys ("symphony" and "sys_symphony") that
    both point at SYS_Symphony.EXE.md. FILENAME_TO_CHARACTER's dict
    comprehension resolves that collision to "sys_symphony" (defined
    second, so it wins) -- this is expected, not a bug to "fix".
    """
    return FILENAME_TO_CHARACTER.get(filename, OTHER_LABEL)


def _preview_text(text: str, max_chars: int = TEXT_PREVIEW_CHARS) -> str:
    """Flatten newlines and truncate for a compact tooltip preview."""
    flat = text.replace("\n", " ")
    if len(flat) > max_chars:
        return flat[:max_chars] + "..."
    return flat


def _fetch_all_chunks() -> dict:
    """Pull every chunk's embedding + metadata + document text from Chroma
    in one call -- the collection is small enough (~479 chunks) that no
    pagination is needed."""
    return collection.get(include=["embeddings", "metadatas", "documents"])


def _project_to_2d(embeddings: list[list[float]]) -> np.ndarray:
    """Run t-SNE to project the full-dimensional chunk embeddings down to
    2D for plotting. Fixed seed + PCA init keep the layout stable across
    requests instead of reshuffling every call."""
    projector = TSNE(
        n_components=2,
        random_state=TSNE_RANDOM_STATE,
        perplexity=TSNE_PERPLEXITY,
        init=TSNE_INIT,
    )
    return projector.fit_transform(np.array(embeddings))


def _retrieval_hit_keys(query: str | None) -> set[tuple[str, int]]:
    """
    Return the (filename, chunk_index) pairs that a real retrieval call for
    this query would return, using the exact same branching nova_query.ask()
    uses in production (via nova_chunk_viz.resolve_chunks) -- so the overlay
    reflects real retrieval behavior, not a simplified approximation.
    Returns an empty set if no query was given.
    """
    if not query:
        return set()
    chunks, _mode = resolve_chunks(query, DEFAULT_N_RESULTS, character=None, use_graph=True)
    return {
        (chunk["metadata"].get("filename", ""), chunk["metadata"].get("chunk_index", -1))
        for chunk in chunks
    }


def _dpo_corrected_filenames() -> set[str]:
    """
    Read training_flags.jsonl directly (not via nova_corrector.py, which
    would pull in the anthropic client + dotenv-loading for what should be
    a lightweight read-only lookup) and return the set of filenames
    involved in any blend event that has actually been corrected.

    Precision limit, documented rather than worked around: chunk_excerpts
    carries a filename + text snippet, not a chunk_index, so this overlay
    is filename-granularity -- every chunk from a DPO-corrected file's
    blend event is flagged, not only the exact excerpted chunk. Exact-chunk
    matching would need fuzzy text matching against corpus chunks, which
    isn't reliable enough to be worth building for a v1 audit tool.
    """
    if not os.path.exists(TRAINING_FLAGS_PATH):
        return set()

    corrected_filenames: set[str] = set()
    with open(TRAINING_FLAGS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if not entry.get("correction"):
                continue
            for excerpt in entry.get("chunk_excerpts", []):
                filename = excerpt.get("filename")
                if filename:
                    corrected_filenames.add(filename)
    return corrected_filenames


def _build_base_points() -> list[dict]:
    """
    Pull every chunk from Chroma, project to 2D, and attach static
    per-chunk fields (everything that doesn't depend on a query or
    training-flags state, which is what "base" means here). Overlay fields
    (is_retrieval_hit, is_dpo_corrected) are filled in separately per
    request by build_embedding_viz_data(), since those can change without
    needing to re-run the expensive projection.
    """
    raw = _fetch_all_chunks()
    coordinates = _project_to_2d(raw["embeddings"])

    points = []
    for i in range(len(raw["ids"])):
        metadata = raw["metadatas"][i]
        filename = metadata.get("filename", "unknown")
        points.append({
            "x": float(coordinates[i][0]),
            "y": float(coordinates[i][1]),
            "filename": filename,
            "chunk_index": metadata.get("chunk_index"),
            "character": _character_for_filename(filename),
            "text_preview": _preview_text(raw["documents"][i]),
        })
    return points


# ── Core ───────────────────────────────────────────────────────
def build_embedding_viz_data(query: str | None = None, refresh: bool = False) -> dict:
    """
    Build the full response for the Embedding-Space Visualization page: one
    point per chunk, with its 2D projection, character tag, and two
    overlays -- is_retrieval_hit (which chunks a real retrieval call for
    `query` would return) and is_dpo_corrected (which chunks belong to a
    file involved in an already-corrected training-flag blend event).

    The expensive part (pulling every embedding + running t-SNE) is cached
    at module level and only re-run when the cache is empty or `refresh`
    is True -- overlays are cheap and always recomputed fresh.
    """
    global _cached_points

    if _cached_points is None or refresh:
        _cached_points = _build_base_points()

    hit_keys = _retrieval_hit_keys(query)
    dpo_filenames = _dpo_corrected_filenames()

    points = []
    for point in _cached_points:
        key = (point["filename"], point["chunk_index"])
        points.append({
            **point,
            "is_retrieval_hit": key in hit_keys,
            "is_dpo_corrected": point["filename"] in dpo_filenames,
        })

    return {
        "points": points,
        "query": query,
        "point_count": len(points),
    }
