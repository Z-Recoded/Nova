# nova_synthetic_task_gen.py
# Nova Training Pipeline Phase 3 (86bbcfpc9): generates synthetic (task, diff)
# SFT training pairs by back-translating Nova's own real git commit history --
# given a real, already-shipped diff, asks Claude to reverse-engineer the
# natural-language task a person would have given BEFORE that diff was
# written. Same idea as R2E-Gym's SWEGEN (real commits -> executable
# environments, not hand-written issues), applied to C:/Nova's own history
# rather than a broader open-source corpus -- confirmed with Marvin
# 2026-08-12: every synthetic example should stay grounded in code Nova
# genuinely knows and will be evaluated on.
#
# This solves the "scarce hand-curated examples" problem
# nova_coding_dataset_curator.py's real-trajectory lane hits -- only 8 real
# merged branches exist total in this repo's history, already fully spent
# between the dev set and prior spike-testing (86bbcfv8d). Nova's own commit
# history is ~279 non-merge commits, a much larger real-signal pool.
#
# Output schema matches nova_finetune_qwen_coder_sft.py's
# load_nova_review_examples() field names exactly (task/diff/approved) so a
# future Phase 1/2 step -- or that loader directly -- can consume this file
# with no reformat. approved=True unconditionally: every diff here is real,
# already-merged, shipped code, the same "confirmed good" bar
# nova_coding_dataset_curator.py already applies to its own real
# trajectories. Written to data/coding_training/synthetic/ (gitignored, not
# logs/coding_review_log.jsonl) so synthetic back-translated examples never
# silently blend with real live production review data.
#
# Real per-commit design risk, worth stating plainly: a back-translated task
# can leak information from the diff that a real forward-looking task never
# would have had (e.g. referencing exact variable/function names the diff
# itself introduces). Mitigated via an explicit system-prompt instruction to
# write the task as it would have been RECEIVED, not as a diff caption.
#
# Usage:
#   python nova_synthetic_task_gen.py --dry-run --limit 5   # zero-cost preview
#   python nova_synthetic_task_gen.py --curate --limit 20   # real paid pilot run
#   python nova_synthetic_task_gen.py --report
#   python nova_synthetic_task_gen.py --all

import argparse
import json
import os
import subprocess
import sys

import anthropic
from dotenv import load_dotenv

from nova_orchestrator import NOVA_AGENT_MODEL

# Same cp1252-crash precedent as nova_corrector.py/nova_coding_corrector.py --
# a real back-translated task can contain characters Windows' default console
# codepage can't encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Resolved relative to this file's own location, same as both correction
# scripts -- a hardcoded "C:/Nova/.env" silently fails to load real secrets
# on the Omen.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))


# ── Config ─────────────────────────────────────────────────────
def _resolve_main_checkout_root() -> str:
    """
    Find the MAIN checkout's root, not wherever this script happens to be
    running from -- same reasoning and mechanism as
    nova_coding_dataset_curator.py's identically-named helper. `git
    rev-parse --git-common-dir` always resolves to the main checkout's real
    .git directory, even from inside a worktree.

    Every `subprocess.run(..., text=True)` call in this file also passes
    `encoding="utf-8"` explicitly -- Windows' default text-mode encoding is
    cp1252, and real diff content in this repo (this file's own comments
    included) routinely contains characters that crash a cp1252 decode.
    Confirmed live: a real commit diff crashed subprocess's own internal
    reader thread with UnicodeDecodeError before this fix.
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
SYNTHETIC_DIR = os.path.join(REPO_ROOT, "data", "coding_training", "synthetic")
SYNTHETIC_OUTPUT_PATH = os.path.join(SYNTHETIC_DIR, "synthetic_task_pairs.jsonl")

# Commits touching ONLY these paths (or paths under these directories) are
# real housekeeping, not coding-task signal -- e.g. the "Board digest: ..."
# commits (8 of 279 as of 2026-08-12). A commit that touches one of these
# ALONGSIDE real code is still included; only all-non-code commits are
# skipped.
NON_CODE_ONLY_PATHS = {
    "NOVA_STATUS.md",
    ".nova_status_snapshot.json",
    "NOVA_BUILD_LOG.md",
    "CLAUDE.md",
    ".gitignore",
}
# graphify-out/ added 2026-08-12 after a real live finding: a single
# graphify-regeneration commit (128 files, almost all machine-generated AST
# caches under graphify-out/cache/) was 63% of this script's entire curated
# diff content by character count in its first real 60-row batch -- low
# training value (not hand-written code-editing signal) and disproportionate
# API cost for its own back-translation call.
NON_CODE_ONLY_DIR_PREFIXES = ("screenshots/", "graphify-out/")

# Filters out near-empty commits (a one-line typo fix, a whitespace-only
# change) without discarding genuinely small real fixes -- chosen loosely,
# not tuned against real data yet, same discipline
# nova_coding_dataset_curator.py's MIN_TRAJECTORY_TURNS documents for its own
# untuned threshold.
MIN_DIFF_LINES_CHANGED = 3

# Sonnet 5's real confirmed list rates (see CLAUDE.md's model-catalog
# reference) -- introductory pricing runs through 2026-08-31, after which
# the standard rate applies. Used only for the end-of-run cost tally below;
# never enforced, matching the rest of this repo's offline Claude-API
# scripts (nova_coding_corrector.py/nova_corrector.py), which don't hook
# into nova_token_budget.py's governor either.
SONNET_5_INPUT_COST_PER_MTOK = 2.00
SONNET_5_OUTPUT_COST_PER_MTOK = 10.00


# ── Git history walk ───────────────────────────────────────────
def _list_non_merge_commit_shas() -> list[str]:
    """Every non-merge commit sha on master, oldest first."""
    result = subprocess.run(
        ["git", "log", "--no-merges", "--reverse", "--format=%H", "master"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _changed_files(commit_sha: str) -> list[str]:
    """Real file paths touched by one commit."""
    result = subprocess.run(
        ["git", "show", "--name-only", "--format=", commit_sha],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _diff_line_count(commit_sha: str) -> int:
    """Total insertions + deletions for one commit, from --shortstat."""
    result = subprocess.run(
        ["git", "show", "--shortstat", "--format=", commit_sha],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    stat_line = result.stdout.strip()
    total = 0
    for token in stat_line.split(","):
        token = token.strip()
        if "insertion" in token or "deletion" in token:
            total += int(token.split()[0])
    return total


def _get_full_diff(commit_sha: str) -> str:
    """The real unified diff for one commit."""
    result = subprocess.run(
        ["git", "show", "--format=", commit_sha],
        cwd=REPO_ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _is_non_code_only(files: list[str]) -> bool:
    """True only when EVERY file this commit touched is a known non-code path."""
    for path in files:
        if path in NON_CODE_ONLY_PATHS:
            continue
        if any(path.startswith(prefix) for prefix in NON_CODE_ONLY_DIR_PREFIXES):
            continue
        return False
    return True


def _select_candidate_commits() -> list[str]:
    """
    Every non-merge commit sha that survives the filters: not all-non-code,
    and above the minimum real-diff-size threshold.
    """
    candidates = []
    for sha in _list_non_merge_commit_shas():
        files = _changed_files(sha)
        if not files or _is_non_code_only(files):
            continue
        if _diff_line_count(sha) < MIN_DIFF_LINES_CHANGED:
            continue
        candidates.append(sha)
    return candidates


# ── Back-translation via Claude ────────────────────────────────
def request_backtranslated_task(client: anthropic.Anthropic, diff: str, files: list[str]) -> str:
    """
    Ask Claude to reverse-engineer the natural-language task/instruction a
    person would have given BEFORE this real diff was written. Mirrors
    nova_coding_corrector.py's request_correction() -- same one-shot,
    non-agentic pattern -- but the transform here is "diff -> task", not
    "flawed diff -> corrected diff".
    """
    system = (
        "You are given a real diff from a coding agent's own repository history. Write the "
        "natural-language task or instruction a person would have given BEFORE this diff was "
        "written -- as if you are the one requesting the change, not someone summarizing it "
        "afterward. Do not reference the diff itself, describe what changed, or use past tense "
        "('added', 'fixed', 'changed'). Write it as a forward-looking request a person would "
        "plausibly type, in 1-3 sentences. Do not reference specific variable, function, or "
        "file names that only exist because of this diff -- describe the goal, not the "
        "implementation. Output ONLY the task text, no other commentary."
    )
    files_block = "\n".join(f"- {path}" for path in files)
    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        max_tokens=1024,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Files touched:\n{files_block}\n\n"
                    f"Diff:\n{diff}\n\n"
                    "Write the task that would have produced this diff:"
                ),
            }
        ],
    )
    # Same ThinkingBlock gotcha both correction scripts already found live --
    # content[0] is not reliably the text block for this account/model.
    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("No text block in Claude's response.")
    return text_blocks[0].strip(), message.usage


# ── JSONL read / write ─────────────────────────────────────────
def _load_processed_shas() -> set[str]:
    """Every commit_sha already present in the output file, for resume/skip."""
    if not os.path.exists(SYNTHETIC_OUTPUT_PATH):
        return set()
    processed = set()
    with open(SYNTHETIC_OUTPUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                processed.add(json.loads(line)["commit_sha"])
    return processed


def _append_row(row: dict) -> None:
    """Append one curated row and flush immediately -- crash-safe, no full-file rewrite."""
    os.makedirs(SYNTHETIC_DIR, exist_ok=True)
    with open(SYNTHETIC_OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


# ── Main ───────────────────────────────────────────────────────
def curate(limit: int | None, dry_run: bool) -> None:
    candidates = _select_candidate_commits()
    already_processed = _load_processed_shas()
    remaining = [sha for sha in candidates if sha not in already_processed]

    print(f"{len(candidates)} candidate commit(s) after filtering, {len(already_processed)} already processed.")
    print(f"{len(remaining)} remaining unprocessed candidate(s).")

    if limit is not None:
        remaining = remaining[:limit]
        print(f"Limited to {len(remaining)} commit(s) this run.")

    if not remaining:
        print("Nothing to do.")
        return

    # Built unconditionally, even for --dry-run: count_tokens() (used below
    # to size a run before spending on it) still requires a valid API key,
    # even though it's free of charge and never generates a token.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set. Export it before running this script.")
    client = anthropic.Anthropic(api_key=api_key)

    total_input_tokens = 0
    total_output_tokens = 0
    written = 0

    for sha in remaining:
        files = _changed_files(sha)
        diff = _get_full_diff(sha)
        print(f"\n{sha[:12]}  ({len(files)} file(s), {_diff_line_count(sha)} line(s) changed)")

        if dry_run:
            # count_tokens() is free of charge (no generation happens) --
            # this is the real per-commit input-token estimate, not a guess,
            # while still spending zero on actual generation. Confirmed live:
            # unlike messages.create(), count_tokens() 400s on a bare string
            # `content` ("Input should be a valid list") -- it requires an
            # explicit content-block list.
            count = client.messages.count_tokens(
                model=NOVA_AGENT_MODEL,
                messages=[{"role": "user", "content": [{"type": "text", "text": diff}]}],
            )
            print(f"(dry run — real input token estimate: {count.input_tokens})")
            continue

        try:
            task, usage = request_backtranslated_task(client, diff, files)
        except Exception as e:
            print(f"Back-translation failed for this commit, will retry on a future run: {e}")
            continue

        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens

        row = {
            "task": task,
            "diff": diff,
            "approved": True,
            "commit_sha": sha,
            "files_touched": files,
            "source": "commit_backtranslation",
            "license": "proprietary",
        }
        _append_row(row)
        written += 1

        print(f"Task: {task[:120]}{'...' if len(task) > 120 else ''}")

    if dry_run:
        print(f"\nDry run complete — {len(remaining)} commit(s) previewed, no API calls made, nothing written.")
        return

    cost = (
        total_input_tokens / 1_000_000 * SONNET_5_INPUT_COST_PER_MTOK
        + total_output_tokens / 1_000_000 * SONNET_5_OUTPUT_COST_PER_MTOK
    )
    print(f"\n{written} row(s) written to {SYNTHETIC_OUTPUT_PATH}")
    print(
        f"Real usage this run: {total_input_tokens} input token(s), {total_output_tokens} output token(s), "
        f"~${cost:.4f} at Sonnet 5's introductory list rate (visibility only, not budget-enforced)."
    )


def report() -> None:
    """Print final counts and a couple of sample rows."""
    if not os.path.exists(SYNTHETIC_OUTPUT_PATH):
        print(f"No curated file found at {SYNTHETIC_OUTPUT_PATH} — run --curate first.")
        return

    with open(SYNTHETIC_OUTPUT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    total_candidates = len(_select_candidate_commits())
    print(f"Total curated rows: {len(rows)} of {total_candidates} candidate commit(s) in the current backlog.\n")

    if rows:
        print("Sample row(s) (commit_sha, files_touched count, task preview):")
        for row in rows[:3]:
            task_preview = row["task"][:100] + ("..." if len(row["task"]) > 100 else "")
            print(f"  {row['commit_sha'][:12]}  |  {len(row['files_touched'])} file(s)  |  {task_preview}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nova Training Pipeline Phase 3 — synthetic task generation via commit back-translation (86bbcfpc9)"
    )
    parser.add_argument("--curate", action="store_true", help="Walk commit history, call Claude, write curated rows")
    parser.add_argument("--report", action="store_true", help="Print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    parser.add_argument("--limit", type=int, default=None, help="Cap how many new commits to process this run")
    parser.add_argument("--dry-run", action="store_true", help="Preview candidates without calling the API")
    args = parser.parse_args()

    if args.all or args.curate:
        curate(limit=args.limit, dry_run=args.dry_run)
    if args.all or args.report:
        report()
