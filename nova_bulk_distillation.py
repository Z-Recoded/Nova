# nova_bulk_distillation.py
# Nova Training Pipeline Phase 1 (86bbcfpap): the "ZERO" pattern -- bulk,
# execution-free distillation from Claude. Generates a large volume of
# UNVERIFIED coding trajectories, cheap by design, to build baseline
# semantic/task intuition before Phase 2's expensive, execution-grounded
# refinement (real test-execution pass/fail).
#
# Input: Phase 3's synthetic task pool
# (data/coding_training/synthetic/synthetic_task_pairs.jsonl, produced by
# nova_synthetic_task_gen.py). Each row has a real historical (task, diff)
# pair from commit back-translation. Phase 1 NEVER sends that real diff to
# Claude -- it's kept only as provenance. Instead, for each task, this
# script pulls the actual pre-diff file content from right before the real
# historical commit (`git show <sha>^:path`) and asks Claude to solve the
# task from scratch using only that real context, in one API call. This is
# deliberately NOT a full agentic tool-use loop through nova_orchestrator.py
# -- that loop runs up to 25 turns with gate/review/verification overhead
# built for real production coding tasks, directly at odds with "cheap by
# design" (confirmed live: no lightweight entry point exists to strip that
# overhead out). One Claude call per task instead, same order of magnitude
# as nova_synthetic_task_gen.py's own back-translation cost.
#
# Output schema deliberately does NOT reuse approved=True the way Phase 3's
# rows honestly do (those ARE the real, shipped, confirmed-good diff).
# Phase 1's diff is Claude's own unverified attempt -- writing approved=True
# here would misrepresent a guess as ground truth and silently corrupt
# anything (both the SFT and DPO loaders) that trusts that field. Written to
# its own file, never logs/coding_review_log.jsonl and never Phase 3's own
# file, so nothing downstream can load an unverified row through a path
# that assumes ground truth.
#
# Usage:
#   python nova_bulk_distillation.py --dry-run --limit 5   # zero-cost preview
#   python nova_bulk_distillation.py --curate --limit 20   # real paid pilot run
#   python nova_bulk_distillation.py --report
#   python nova_bulk_distillation.py --all

import argparse
import json
import os
import re
import subprocess
import sys

import anthropic
from dotenv import load_dotenv

from nova_orchestrator import NOVA_AGENT_MODEL

# Same cp1252-crash precedent as the other Claude-API scripts in this repo --
# a real generated diff can contain characters Windows' default console
# codepage can't encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Resolved relative to this file's own location, same as every other
# Claude-API script here -- a hardcoded "C:/Nova/.env" silently fails to
# load real secrets on the Omen.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))


# ── Config ─────────────────────────────────────────────────────
def _resolve_main_checkout_root() -> str:
    """
    Find the MAIN checkout's root, not wherever this script happens to be
    running from -- same reasoning and mechanism as
    nova_synthetic_task_gen.py's identically-named helper.

    Every `subprocess.run(..., text=True)` call in this file also passes
    `encoding="utf-8"` explicitly -- Windows' default text-mode encoding is
    cp1252, and real file content in this repo routinely contains
    characters that crash a cp1252 decode (confirmed live in
    nova_synthetic_task_gen.py before this file existed).
    """
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=_SCRIPT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return os.path.dirname(result.stdout.strip())


REPO_ROOT = _resolve_main_checkout_root()
SYNTHETIC_INPUT_PATH = os.path.join(REPO_ROOT, "data", "coding_training", "synthetic", "synthetic_task_pairs.jsonl")
BULK_OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "coding_training", "bulk_distillation")
BULK_OUTPUT_PATH = os.path.join(BULK_OUTPUT_DIR, "phase1_trajectories.jsonl")

# Safety guard against a Phase 3 row whose real pre-diff file context is
# unusually large (e.g. the repo's initial-commit task touches 18 files) --
# skip rather than silently spend a large amount on one oversized call.
# Chosen loosely, not tuned against real data yet, same discipline
# nova_synthetic_task_gen.py's own MIN_DIFF_LINES_CHANGED documents.
MAX_CONTEXT_TOKENS = 60_000

# Sonnet 5's real confirmed list rates -- see nova_synthetic_task_gen.py for
# the same constants and the same "visibility only, never enforced" caveat.
SONNET_5_INPUT_COST_PER_MTOK = 2.00
SONNET_5_OUTPUT_COST_PER_MTOK = 10.00


# ── Real pre-diff file context ─────────────────────────────────
def _has_parent_commit(commit_sha: str) -> bool:
    """False only for the repo's real root commit (confirmed live: Phase 3's
    own first row IS that root commit, zero parents -- must not crash)."""
    result = subprocess.run(
        ["git", "rev-parse", f"{commit_sha}^"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0


def _file_content_at_parent(commit_sha: str, path: str, has_parent: bool) -> str | None:
    """
    Real content of `path` right before `commit_sha` was applied, or None if
    the file didn't exist yet at that point (a normal, expected outcome for
    a newly-created file -- not an error).
    """
    if not has_parent:
        return None
    result = subprocess.run(
        ["git", "show", f"{commit_sha}^:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _build_file_context_block(commit_sha: str, files_touched: list[str]) -> str:
    """One text block per touched file: its real pre-diff content, or an
    explicit not-yet-created note -- never the real diff itself."""
    has_parent = _has_parent_commit(commit_sha)
    blocks = []
    for path in files_touched:
        content = _file_content_at_parent(commit_sha, path, has_parent)
        if content is None:
            blocks.append(f"### File: {path}\n(This file does not exist yet -- you will need to create it.)")
        else:
            blocks.append(f"### File: {path}\n```\n{content}\n```")
    return "\n\n".join(blocks)


# ── Bulk generation via Claude ─────────────────────────────────
def request_bulk_solution(client: anthropic.Anthropic, task: str, file_context: str):
    """
    Ask Claude to solve a task from scratch, grounded ONLY in the real
    pre-diff file content given -- never the real historical diff. Mirrors
    nova_coding_corrector.py's request_correction() shape (client call,
    ThinkingBlock-safe parsing, fence-stripping) but for "write a solution"
    instead of "fix a flawed diff."
    """
    system = (
        "You are solving a real coding task in an existing repository. You are given the task "
        "and the real current content of every file relevant to it. Using ONLY the file content "
        "shown -- do not assume any other file exists or invent APIs/functions not shown -- write "
        "a unified diff that solves the task. Use standard unified-diff format (file paths, "
        "hunk headers). If a file is noted as not existing yet, create it from scratch. Output "
        "ONLY the diff, no other text."
    )
    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        # Same budget nova_coding_corrector.py needed for full-diff output --
        # a real multi-file diff plus an invisible thinking block can exceed
        # a review-verdict-sized budget.
        max_tokens=16000,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\nRelevant files:\n\n{file_context}\n\nWrite the diff that solves this task:"
                ),
            }
        ],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        # Real, billed API call already happened (thinking can consume the
        # entire max_tokens budget before any visible text, e.g. on a large
        # from-scratch task) -- return the usage so the caller's cost tally
        # stays honest even though there's no diff to use.
        return None, message.usage
    raw = text_blocks[0].strip()
    diff = re.sub(r"^```(?:diff)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    return diff, message.usage


# ── JSONL read / write ─────────────────────────────────────────
def _load_input_rows() -> list[dict]:
    """Every row from Phase 3's synthetic task pool."""
    if not os.path.exists(SYNTHETIC_INPUT_PATH):
        return []
    rows = []
    with open(SYNTHETIC_INPUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_processed_shas() -> set[str]:
    """Every commit_sha already present in this script's own output file."""
    if not os.path.exists(BULK_OUTPUT_PATH):
        return set()
    processed = set()
    with open(BULK_OUTPUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                processed.add(json.loads(line)["commit_sha"])
    return processed


def _append_row(row: dict) -> None:
    """Append one row and flush immediately -- crash-safe, no full-file rewrite."""
    os.makedirs(BULK_OUTPUT_DIR, exist_ok=True)
    with open(BULK_OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


# ── Main ───────────────────────────────────────────────────────
def curate(limit: int | None, dry_run: bool) -> None:
    input_rows = _load_input_rows()
    if not input_rows:
        print(f"No input rows found at {SYNTHETIC_INPUT_PATH} — run nova_synthetic_task_gen.py first.")
        return

    already_processed = _load_processed_shas()
    remaining = [row for row in input_rows if row["commit_sha"] not in already_processed]

    print(f"{len(input_rows)} row(s) in Phase 3's task pool, {len(already_processed)} already processed.")
    print(f"{len(remaining)} remaining unprocessed row(s).")

    if limit is not None:
        remaining = remaining[:limit]
        print(f"Limited to {len(remaining)} row(s) this run.")

    if not remaining:
        print("Nothing to do.")
        return

    # Built unconditionally, even for --dry-run: count_tokens() (used below
    # to size the real context-guard check) still requires a valid API key,
    # even though it's free of charge and never generates a token.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set. Export it before running this script.")
    client = anthropic.Anthropic(api_key=api_key)

    total_input_tokens = 0
    total_output_tokens = 0
    written = 0
    skipped_oversized = 0

    for row in remaining:
        sha = row["commit_sha"]
        task = row["task"]
        files = row["files_touched"]
        print(f"\n{sha[:12]}  ({len(files)} file(s))")

        file_context = _build_file_context_block(sha, files)

        # count_tokens() is free of charge -- a real safety check before
        # spending on an oversized call, not a guess.
        count = client.messages.count_tokens(
            model=NOVA_AGENT_MODEL,
            messages=[{"role": "user", "content": [{"type": "text", "text": task + file_context}]}],
        )
        print(f"Real context size: {count.input_tokens} token(s)")
        if count.input_tokens > MAX_CONTEXT_TOKENS:
            print(f"Skipping — exceeds MAX_CONTEXT_TOKENS ({MAX_CONTEXT_TOKENS}).")
            skipped_oversized += 1
            continue

        if dry_run:
            print("(dry run — skipping API call)")
            continue

        try:
            diff, usage = request_bulk_solution(client, task, file_context)
        except Exception as e:
            print(f"Bulk solution generation failed for this row, will retry on a future run: {e}")
            continue

        # Real, billed usage regardless of whether a usable diff came back --
        # a call that hit max_tokens with no visible text still spent real
        # tokens (see request_bulk_solution's own comment).
        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens

        if diff is None:
            print("No usable diff (max_tokens likely exhausted by thinking) -- will retry on a future run.")
            continue

        output_row = {
            "task": task,
            "diff": diff,
            "verification_status": "unverified",
            "commit_sha": sha,
            "files_touched": files,
            "source": "phase1_bulk_distillation",
            "license": "proprietary",
        }
        _append_row(output_row)
        written += 1

        print(f"Diff preview: {diff[:120]}{'...' if len(diff) > 120 else ''}")

    if dry_run:
        print(
            f"\nDry run complete — {len(remaining)} row(s) previewed "
            f"({skipped_oversized} would be skipped as oversized), no API calls made, nothing written."
        )
        return

    cost = (
        total_input_tokens / 1_000_000 * SONNET_5_INPUT_COST_PER_MTOK
        + total_output_tokens / 1_000_000 * SONNET_5_OUTPUT_COST_PER_MTOK
    )
    print(f"\n{written} row(s) written to {BULK_OUTPUT_PATH} ({skipped_oversized} skipped as oversized)")
    print(
        f"Real usage this run: {total_input_tokens} input token(s), {total_output_tokens} output token(s), "
        f"~${cost:.4f} at Sonnet 5's introductory list rate (visibility only, not budget-enforced)."
    )


def report() -> None:
    """Print final counts and a couple of sample rows."""
    if not os.path.exists(BULK_OUTPUT_PATH):
        print(f"No curated file found at {BULK_OUTPUT_PATH} — run --curate first.")
        return

    with open(BULK_OUTPUT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    total_input_rows = len(_load_input_rows())
    print(f"Total curated rows: {len(rows)} of {total_input_rows} row(s) in Phase 3's task pool.\n")

    if rows:
        print("Sample row(s) (commit_sha, files_touched count, task preview):")
        for row in rows[:3]:
            task_preview = row["task"][:100] + ("..." if len(row["task"]) > 100 else "")
            print(f"  {row['commit_sha'][:12]}  |  {len(row['files_touched'])} file(s)  |  {task_preview}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nova Training Pipeline Phase 1 — bulk execution-free distillation from Claude (86bbcfpap)"
    )
    parser.add_argument("--curate", action="store_true", help="Generate unverified solutions, write curated rows")
    parser.add_argument("--report", action="store_true", help="Print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    parser.add_argument("--limit", type=int, default=None, help="Cap how many new rows to process this run")
    parser.add_argument("--dry-run", action="store_true", help="Preview context sizes without calling the API")
    args = parser.parse_args()

    if args.all or args.curate:
        curate(limit=args.limit, dry_run=args.dry_run)
    if args.all or args.report:
        report()
