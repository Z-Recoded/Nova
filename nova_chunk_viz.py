# nova_chunk_viz.py
# RAG retrieval-audit CLI — given a query, shows which chunks Chroma actually
# retrieved (source file, chunk index, distance, character tag), so Marvin can
# visually debug retrieval problems like a query pulling the wrong character's
# file. Mirrors nova_query.ask()'s real retrieval branching rather than a
# simplified stand-in, so results reflect actual production behavior.
#
# Stage 1 of 3 (ClickUp 86bara3tj): CLI -> simple web view -> Open WebUI debug
# panel. Only the CLI is built here; resolve_chunks() is a clean standalone
# function so a future stage can reuse it without rework.

import argparse
import re
import sys

from colorama import Fore, Style, init as colorama_init

from nova_query import CHARACTER_FILES, retrieve, retrieve_with_graph

# ── Config ─────────────────────────────────────────────────────
DEFAULT_N_RESULTS = 5
TEXT_PREVIEW_CHARS = 200
NO_CHARACTER_TAG = "-"

# ingest.py stores every chunk as f"[{filename}]\n{chunk}" (identity baked
# into the vector for the character-blending fix) -- strip that anchor from
# the preview so it doesn't just repeat the source= column.
SOURCE_ANCHOR_RE = re.compile(r"^\[.*?\]\n")

# Reverse of CHARACTER_FILES (name -> filename) for the character-tag column.
# Most chunks won't match -- that's expected for non-fiction sources, not an error.
FILENAME_TO_CHARACTER = {filename: name for name, filename in CHARACTER_FILES.items()}

# ── Setup ──────────────────────────────────────────────────────
# Windows consoles default to cp1252, which has crashed this project's own
# unicode/dash output more than once (nova_benchmark.py, nova_board.py). Guard
# the same way here before printing anything.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

colorama_init(autoreset=True)


# ── Helpers ────────────────────────────────────────────────────
def _strip_source_anchor(text: str) -> str:
    """Remove ingest.py's leading '[filename]\\n' anchor so the preview shows
    real content instead of repeating the source= column."""
    return SOURCE_ANCHOR_RE.sub("", text, count=1)


def _truncate_preview(text: str, max_chars: int = TEXT_PREVIEW_CHARS) -> str:
    """Flatten newlines and cut to max_chars for a compact one-line preview."""
    flat = text.replace("\n", " ")
    if len(flat) > max_chars:
        return flat[:max_chars] + "..."
    return flat


def _detect_character(query: str) -> str | None:
    """Word-boundary match query text against CHARACTER_FILES -- identical
    logic to nova_query.ask()'s fiction-category character filter, so this
    tool auto-detects characters the same way production retrieval does.
    Returns the matched CHARACTER_FILES key, or None if no character is named."""
    q_lower = query.lower()
    for name in CHARACTER_FILES:
        if re.search(rf"\b{re.escape(name)}\b", q_lower):
            return name
    return None


def _character_tag_for_chunk(metadata: dict) -> str:
    """Reverse-lookup a chunk's filename against FILENAME_TO_CHARACTER.
    Returns the raw lowercase CHARACTER_FILES key, or NO_CHARACTER_TAG for
    non-fiction sources -- never an error."""
    filename = metadata.get("filename", "")
    return FILENAME_TO_CHARACTER.get(filename, NO_CHARACTER_TAG)


def _format_chunk_block(rank: int, chunk: dict, expected_character: str | None) -> str:
    """Build the printed block for one retrieved chunk: rank, source filename,
    chunk index, distance, and character tag -- colored green when it matches
    the expected/detected character, red when it doesn't (the actual
    'wrong file got pulled' signal this tool exists to surface)."""
    meta = chunk["metadata"]
    filename = meta.get("filename", "unknown")
    chunk_index = meta.get("chunk_index", "?")
    total_chunks = meta.get("total_chunks", "?")
    distance = chunk["distance"]
    tag = _character_tag_for_chunk(meta)
    tag_display = tag.capitalize() if tag != NO_CHARACTER_TAG else NO_CHARACTER_TAG

    if expected_character and tag == expected_character:
        tag_color = Fore.GREEN
    elif expected_character and tag != NO_CHARACTER_TAG:
        tag_color = Fore.RED
    else:
        tag_color = Fore.RESET

    preview = _truncate_preview(_strip_source_anchor(chunk["text"]))

    header = (
        f"[{rank}] source={filename}  chunk_index={chunk_index}/{total_chunks}  "
        f"distance={distance:.4f}  character={tag_color}{tag_display}{Style.RESET_ALL}"
    )
    return f'{header}\n    "{preview}"'


# ── Retrieval ──────────────────────────────────────────────────
def resolve_chunks(
    query: str, n_results: int, character: str | None, use_graph: bool
) -> tuple[list[dict], str]:
    """
    Run retrieval using the same decision tree nova_query.ask() uses in
    production, so this debug tool reflects real retrieval behavior instead
    of a simplified nearest-neighbor demo:

      - character named (explicit --character or auto-detected in the query):
        retrieve() with a $eq filename filter, falling back to unfiltered
        retrieve() if that returns nothing -- same as ask()'s fiction branch.
      - no character named: retrieve_with_graph() -- same as ask()'s
        non-fiction branch and its fiction-no-character fallback.
      - use_graph=False forces bare retrieve(), bypassing graph-scoping and
        character filtering entirely, for comparison against the scoped path.

    Returns (chunks, mode_label) where mode_label describes which path ran.
    """
    if not use_graph:
        return retrieve(query, n_results=n_results), "bare retrieve() (--no-graph)"

    detected = character or _detect_character(query)
    if detected:
        char_filter = {"filename": {"$eq": CHARACTER_FILES[detected]}}
        chunks = retrieve(query, n_results=n_results, where=char_filter)
        if chunks:
            return chunks, f"character-filtered ({detected} -> {CHARACTER_FILES[detected]})"
        chunks = retrieve(query, n_results=n_results)
        return chunks, f"character filter for '{detected}' returned nothing, fell back to bare retrieve()"

    chunks = retrieve_with_graph(query, n_results=n_results)
    return chunks, "graph-scoped (retrieve_with_graph)"


def run_chunk_visualization(
    query: str, n_results: int, character: str | None, use_graph: bool
) -> int:
    """Resolve chunks, print the header and one block per chunk, and return
    an exit code (1 on zero results, matching nova_chroma_omen_check.py's
    PASS/FAIL convention -- a real failure signal worth scripting against)."""
    chunks, mode = resolve_chunks(query, n_results, character, use_graph)
    expected_character = character or _detect_character(query)

    print(f'--- Query: "{query}" | mode={mode} | top {n_results} ---\n')
    if not chunks:
        print("No chunks retrieved. Check Chroma connectivity or query wording.")
        return 1

    for i, chunk in enumerate(chunks, start=1):
        print(_format_chunk_block(i, chunk, expected_character))
        print()

    return 0


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Show which chunks Chroma actually retrieves for a query -- RAG retrieval audit tool."
    )
    parser.add_argument("query", help="The query to retrieve chunks for (quote multi-word queries)")
    parser.add_argument(
        "-n", "--top-n", type=int, default=DEFAULT_N_RESULTS,
        help=f"Number of chunks to retrieve (default: {DEFAULT_N_RESULTS})",
    )
    parser.add_argument(
        "--character", choices=sorted(CHARACTER_FILES), default=None,
        help="Force character-file filtering instead of auto-detecting a name in the query",
    )
    parser.add_argument(
        "--no-graph", action="store_true",
        help="Bypass graph-scoped retrieval and character filtering; raw nearest-neighbor search only",
    )
    args = parser.parse_args()
    sys.exit(run_chunk_visualization(args.query, args.top_n, args.character, not args.no_graph))
