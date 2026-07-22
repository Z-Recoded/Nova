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
from dotenv import load_dotenv

# Resolved relative to this file's own location -- same bug class already
# fixed in nova_orchestrator.py's dotenv path. A hardcoded "C:/Nova/.env"
# silently returns False (not a raised error) on the Omen (Linux) instead of
# loading real secrets, and "C:/Nova/logs/training_flags.jsonl" isn't a real
# path there either -- this script has never actually run correctly off the
# Aero.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))

JSONL_PATH      = os.path.join(_SCRIPT_DIR, "logs", "training_flags.jsonl")
SECOND_BRAIN    = r"C:\Users\marvi\OneDrive\Documents\Second Brain"
CLAUDE_MODEL    = "claude-sonnet-4-6"

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
            with open(path, "r", encoding="utf-8") as f:
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
    return message.content[0].text.strip()

# ── JSONL read / write ─────────────────────────────────────────
def load_entries() -> list[dict]:
    if not os.path.exists(JSONL_PATH):
        return []
    entries = []
    with open(JSONL_PATH, "r", encoding="utf-8") as f:
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
    pending = [e for e in entries if not e.get("correction")]

    if not pending:
        print("No uncorrected entries found.")
        return

    print(f"Found {len(pending)} uncorrected entr{'y' if len(pending) == 1 else 'ies'}.")
    if dry_run:
        print("Dry run — no changes will be written.\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export it before running this script."
        )
    client = anthropic.Anthropic(api_key=api_key)

    corrected = 0
    for entry in entries:
        if entry.get("correction"):
            continue

        query    = entry["messages"][0]["content"]
        sources  = entry.get("sources_mixed", [])
        lore     = load_lore(sources)

        print(f"\nQuery : {query[:80]}{'...' if len(query) > 80 else ''}")
        print(f"Sources: {', '.join(sources)}")

        if dry_run:
            print("(dry run — skipping API call)")
            continue

        correction = request_correction(client, query, lore)
        entry["correction"] = correction
        corrected += 1

        print(f"Correction: {correction[:120]}{'...' if len(correction) > 120 else ''}")

    if not dry_run and corrected:
        save_entries(entries)
        print(f"\n{corrected} correction(s) written to {JSONL_PATH}")
    elif not dry_run:
        print("Nothing to write.")

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
