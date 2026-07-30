# nova_coding_corrector.py
# Uses Claude API to write the CORRECT diff for every review-flagged entry in
# coding_review_log.jsonl. For each entry where approved == False and no
# chosen_diff exists yet, it:
#   1. Reads the task, the flawed diff Qwen2.5-Coder-32B produced, and the
#      reviewer's specific issue list (all already in the entry -- no
#      external grounding source needed, unlike nova_corrector.py's lore
#      lookup)
#   2. Asks Claude to write a corrected diff that fixes every listed issue
#   3. Writes the correction back into the JSONL entry as "chosen_diff"
#
# This is the missing half of the review-split pipeline's DPO data: the
# review pass (_review_coding_diff in nova_orchestrator.py) only ever wrote
# a verdict on the "rejected" diff. Without a "chosen" counterpart, none of
# that data is usable for a fine-tune. Feeds nova_finetune_qwen_coder.py.
#
# Usage:
#   python nova_coding_corrector.py           # process all uncorrected entries
#   python nova_coding_corrector.py --dry-run # preview without writing anything

import json
import os
import re
import sys

import anthropic
from dotenv import load_dotenv

from nova_orchestrator import CODING_REVIEW_LOG_PATH, NOVA_AGENT_MODEL

# Same cp1252-crash precedent as nova_corrector.py -- a real correction can
# contain characters Windows' default console codepage can't encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Resolved relative to this file's own location, same as nova_corrector.py --
# a hardcoded "C:/Nova/.env" silently fails to load real secrets on the Omen.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))

# Entries whose "issues" list shows the review call itself failed to run
# (see _review_coding_diff's own fail-toward-not-approved behavior) have
# nothing real to correct against -- skip them rather than asking Claude to
# "fix" a diff that was never actually reviewed.
REVIEW_FAILURE_MARKERS = [
    "Review itself failed to run:",
    "ANTHROPIC_API_KEY not set",
]


# ── Correction via Claude ──────────────────────────────────────
def request_correction(client: anthropic.Anthropic, task_description: str, flawed_diff: str, issues: list[str]) -> str:
    """
    Ask Claude to write the correct unified diff for a task, given the
    flawed diff a less reliable coding model (Qwen2.5-Coder-32B) produced
    and the reviewer's specific issue list. Mirrors nova_corrector.py's
    request_correction() -- same one-shot, non-agentic pattern -- but the
    flawed diff + issue list stand in for nova_corrector.py's lore lookup as
    the grounding source, since there's no external reference document for
    "what should this code change look like."
    """
    system = (
        "You are writing the CORRECT version of a diff a less reliable coding model "
        "(Qwen2.5-Coder-32B) got wrong. You will be given the task, its flawed diff, and a "
        "reviewer's specific issue list. Write a corrected unified diff that fully "
        "accomplishes the task and fixes every listed issue, in the same unified-diff format "
        "(same file paths, same hunk-header style) as the flawed diff. Do not introduce "
        "unrelated changes. Output ONLY the corrected diff, no other text."
    )
    issues_block = "\n".join(f"- {issue}" for issue in issues) if issues else "(no specific issues listed)"
    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        # Same extended-thinking-eats-the-budget gotcha _review_coding_diff()
        # hit under 600 -- confirmed live 2026-07-29 this call fails the same
        # way under 4096 AND 8192 for a full two-file rewrite correction
        # (needs room for a large invisible thinking block plus a
        # substantial corrected diff, which is bigger output than a review
        # verdict).
        max_tokens=16000,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Task:\n{task_description}\n\n"
                    f"Flawed diff:\n{flawed_diff}\n\n"
                    f"Reviewer's issues:\n{issues_block}\n\n"
                    "Write the corrected diff:"
                ),
            }
        ],
    )
    # Same ThinkingBlock gotcha _review_coding_diff() already found live --
    # content[0] is not reliably the text block for this account/model.
    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("No text block in Claude's response.")
    raw = text_blocks[0].strip()
    # Strip a ```diff / ``` fence if present, same defensive pattern
    # _review_coding_diff() and nova_task_queue.propose_tier() already use
    # for JSON responses -- Claude sometimes wraps output in a fence despite
    # being told not to.
    return re.sub(r"^```(?:diff)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()


# ── JSONL read / write ─────────────────────────────────────────
def load_entries() -> list[dict]:
    """Read every entry from coding_review_log.jsonl. Empty list if it doesn't exist yet."""
    if not os.path.exists(CODING_REVIEW_LOG_PATH):
        return []
    entries = []
    with open(CODING_REVIEW_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def save_entries(entries: list[dict]) -> None:
    """Full-file rewrite -- same read-all/write-all convention as nova_corrector.py."""
    with open(CODING_REVIEW_LOG_PATH, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _needs_correction(entry: dict) -> bool:
    """
    An entry is pending correction if the review flagged it (approved ==
    False), it doesn't already have a chosen_diff, and the review itself
    actually ran (not one of the fail-toward-not-approved placeholder
    entries with nothing real to correct against).
    """
    if entry.get("approved", True):
        return False
    if entry.get("chosen_diff"):
        return False
    issues = entry.get("issues", [])
    if any(marker in issue for issue in issues for marker in REVIEW_FAILURE_MARKERS):
        return False
    return True


# ── Main ───────────────────────────────────────────────────────
def run(dry_run: bool = False) -> None:
    entries = load_entries()
    pending = [e for e in entries if _needs_correction(e)]

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

    corrected = 0
    for entry in entries:
        if not _needs_correction(entry):
            continue

        task = entry["task"]
        print(f"\nTask  : {task[:80]}{'...' if len(task) > 80 else ''}")
        print(f"Issues: {'; '.join(entry.get('issues', [])) or '(none listed)'}")

        if dry_run:
            print("(dry run — skipping API call)")
            continue

        # A single hard entry (e.g. correcting a large multi-file rewrite)
        # can genuinely fail this call -- confirmed live 2026-07-29, the
        # extended-thinking budget this account/model defaults to can
        # consume the entire max_tokens allowance before any real text is
        # emitted, on the harder end of real corrections. Catching per-entry
        # rather than letting one hard case crash the whole run and block
        # every easier entry after it in the file.
        try:
            chosen_diff = request_correction(client, task, entry["diff"], entry.get("issues", []))
        except Exception as e:
            print(f"Correction failed for this entry, will retry on a future run: {e}")
            continue

        entry["chosen_diff"] = chosen_diff
        corrected += 1

        # Saved after every single correction, not once at the end -- same
        # crash-safety rationale as nova_corrector.py's run(): a mid-run
        # crash should not throw away every already-paid-for API call made
        # before it.
        save_entries(entries)

        print(f"Chosen diff: {chosen_diff[:120]}{'...' if len(chosen_diff) > 120 else ''}")

    if not dry_run and corrected:
        print(f"\n{corrected} correction(s) written to {CODING_REVIEW_LOG_PATH}")
    elif not dry_run:
        print("Nothing to write.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
