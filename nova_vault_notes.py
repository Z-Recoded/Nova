# nova_vault_notes.py
# The one sanctioned exception to the Second Brain vault's read-only rule --
# scoped narrowly, on purpose. See CLAUDE.md Section 2 ("Second Brain
# Location") for the policy this file implements and the reasoning behind
# each restriction.
#
# Real constraint being solved: Marvin needs agents to help him keep up with
# fast-moving fields by dropping distilled research into the vault, but the
# vault also doubles as Nova's own RAG corpus (ingest.py) -- writing into it
# carelessly risks Nova retrieving its own unreviewed output and citing it
# back as if it were Marvin's own verified knowledge. The resolution: a
# single, dedicated, provenance-tagged subfolder that stays excluded from
# ingestion until Marvin manually promotes a note out of it (moves/renames
# it into the main vault namespace) -- the same "isolate, never auto-merge,
# human decides" discipline nova_orchestrator.py already uses for code.
#
# Two hard restrictions enforce that discipline at the code level, not just
# in policy prose:
#   1. Every write is scoped to exactly one subfolder (VAULT_RESEARCH_ROOT).
#      No function in this file can touch any other path in the vault --
#      there is no root parameter to override, unlike nova_tools.py's
#      worktree-scoped primitives.
#   2. Create-only, enforced by refusing to overwrite an existing file.
#      No function in this file can edit or delete anything, including its
#      own prior notes. Worst case if something goes wrong is a handful of
#      unwanted new files, never a lost or altered one.

import re
from datetime import UTC, datetime
from pathlib import Path

from nova_tools import SECOND_BRAIN_PATH

# The one vault subfolder agents may ever write into. Never touch anything
# outside this path from this file.
VAULT_RESEARCH_ROOT = SECOND_BRAIN_PATH / "Nova Research"

# Filenames must be plain text with no path separators or traversal --
# blocks writing outside VAULT_RESEARCH_ROOT via a crafted filename, the
# same class of check nova_tools.py's _resolve_within_root does for the
# coding agent's paths.
_SAFE_FILENAME_PATTERN = re.compile(r"^[\w\-. ]+$")


def _validate_filename(filename: str) -> str:
    """
    Reject anything that isn't a plain, single-segment filename ending in
    .md -- no slashes, no "..", no absolute paths. Returns the filename
    unchanged if it passes, so call sites can use the return value directly
    without a second check.
    """
    if not filename.endswith(".md"):
        raise ValueError(f"Research note filenames must end in .md, got '{filename}'.")
    stem = filename[: -len(".md")]
    if not stem or not _SAFE_FILENAME_PATTERN.match(stem):
        raise ValueError(
            f"'{filename}' isn't a safe plain filename (letters/numbers/spaces/hyphens/underscores/periods only, "
            "no path separators)."
        )
    return filename


def create_research_note(filename: str, title: str, content: str, source_task: str | None = None) -> Path:
    """
    Create one new, provenance-tagged research note inside
    Second Brain/Nova Research/. Refuses to overwrite an existing file --
    this is the create-only guarantee, enforced here rather than left to
    caller discipline. Returns the path written, for logging/confirmation.

    `source_task` is an optional ClickUp task ID or similar, so a note's
    origin is traceable later without relying on memory of which session
    wrote it.
    """
    safe_filename = _validate_filename(filename)
    VAULT_RESEARCH_ROOT.mkdir(parents=True, exist_ok=True)
    target_path = VAULT_RESEARCH_ROOT / safe_filename

    if target_path.exists():
        raise FileExistsError(
            f"'{safe_filename}' already exists in Nova Research/ -- this function never overwrites. "
            "Pick a new filename for a follow-up note instead."
        )

    generated_date = datetime.now(UTC).strftime("%Y-%m-%d")
    frontmatter_lines = [
        "---",
        "nova_generated: true",
        f"generated_date: {generated_date}",
        f"source_task: {source_task or 'none'}",
        "reviewed: false",
        "---",
        "",
        f"# {title}",
        "",
    ]
    full_content = "\n".join(frontmatter_lines) + content

    target_path.write_text(full_content, encoding="utf-8")
    return target_path
