# nova_coding_aci.py
# Constrained Action-Space Interface (ACI) for the coding specialist
# (86bbch95y) — a minimal, LM-friendly command set for interacting with a
# repo, modeled on SWE-agent's own Agent-Computer Interface. Deliberately
# NOT raw shell/filesystem access (that's nova_tools.py's job, for the
# Claude-backed lane) — every command here is narrow and structured on
# purpose, so a small model has a much smaller space of ways to go wrong.
#
# Scope decisions already made on 86bbch95y before this file existed:
# Python-only for now; a custom harness calls this interface directly
# (not MCP/Hammer2.1); runs against an isolated container in production
# (see docs/coding-specialist-aci-sandbox-decision.md -- python:3.13-slim +
# ruff==0.15.22, not yet built) but every function here works against any
# real directory, so it's fully testable without that container existing.
#
# Four core commands (86bbch95y's own list): find_file/search_file/
# search_dir (navigation), view (windowed file read), edit (structural-
# gated write), collapse_history (context management).

import ast
import json
import subprocess
import sys
from pathlib import Path

from nova_tools import _resolve_within_root

# SWE-agent's own ablation found 100 lines outperformed other window sizes
# tested against THEIR models -- 86bbch95y explicitly flags this as needing
# its own test against Qwen2.5-Coder-7B, not assumed to transfer. This is
# SWE-agent's number as a starting default, not yet a Nova-verified one --
# the real tuning run is blocked on the model being pulled and 86bbch988's
# corpus sweep informing what "exact gate behavior" should mean here too.
DEFAULT_VIEW_WINDOW_LINES = 100

# Matches nova_completion_gate.py's own RUFF_CHECK_TIMEOUT_SECONDS.
RUFF_CHECK_TIMEOUT_SECONDS = 20

# Real leakage risk found live (2026-08-15) testing find_file/search_dir
# against a real vendored exercise directory (data/coding_specialist_eval/
# exercism_subset/bob/): both walked into .meta/, which holds the exercise's
# real reference solution (example.py) -- exactly the file
# docs/coding-specialist-exercise-corpus-plan.md already named as "never
# shown to the model." A production container mount is expected to exclude
# .meta/ from what actually gets copied into the model's working directory,
# but this filters it here too, defense-in-depth, so a leak isn't purely
# dependent on the mount step getting that right every time.
EXCLUDED_DIR_NAMES = {".meta"}


def _is_excluded(relative_path: str) -> bool:
    """True if any path component of `relative_path` is in EXCLUDED_DIR_NAMES."""
    return any(part in EXCLUDED_DIR_NAMES for part in Path(relative_path).parts)


# ── Navigation ─────────────────────────────────────────────────
def find_file(pattern: str, root: str) -> list[str]:
    """
    Find files whose name contains `pattern` anywhere under `root`. Returns
    paths relative to `root`, sorted for deterministic output -- the
    structured replacement for a raw `ls`/`find` call. Excludes
    EXCLUDED_DIR_NAMES (see its own comment).
    """
    root_path = Path(root).resolve()
    matches = [
        str(p.relative_to(root_path))
        for p in root_path.rglob(f"*{pattern}*")
        if p.is_file() and not _is_excluded(str(p.relative_to(root_path)))
    ]
    return sorted(matches)


def search_file(path: str, pattern: str, root: str) -> list[tuple[int, str]]:
    """
    Search one file for lines containing `pattern` (plain substring, not
    regex -- keeps the interface predictable for a small model). Returns
    (line_number, line_text) pairs, 1-indexed to match view()'s numbering.
    """
    resolved = _resolve_within_root(path, root)
    lines = resolved.read_text(encoding="utf-8").splitlines()
    return [(i + 1, line) for i, line in enumerate(lines) if pattern in line]


def search_dir(pattern: str, root: str) -> list[tuple[str, int, str]]:
    """
    Search every .py file under `root` for lines containing `pattern`
    (Python-only, per 86bbch95y's own scope decision). Returns
    (relative_path, line_number, line_text) triples -- the structured
    replacement for a raw `grep -r`. Excludes EXCLUDED_DIR_NAMES (see its
    own comment) -- otherwise this would search a real exercise's
    .meta/example.py (the reference solution) right alongside real source,
    a real leakage risk found live testing this function.
    """
    root_path = Path(root).resolve()
    results = []
    for file_path in sorted(root_path.rglob("*.py")):
        if not file_path.is_file():
            continue
        relative = str(file_path.relative_to(root_path))
        if _is_excluded(relative):
            continue
        matches = search_file(relative, pattern, root)
        results.extend((relative, line_number, line_text) for line_number, line_text in matches)
    return results


# ── File viewing ───────────────────────────────────────────────
def view(path: str, root: str, start_line: int = 1, window: int = DEFAULT_VIEW_WINDOW_LINES) -> str:
    """
    Windowed view of a file, with explicit line numbers -- the ACI's
    replacement for a raw full-file read. Returns up to `window` lines
    starting at `start_line` (1-indexed, clamped to the file's real
    length), each prefixed with its line number so a later edit() call can
    reference exact lines without re-reading the whole file.
    """
    resolved = _resolve_within_root(path, root)
    lines = resolved.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start_index = max(0, start_line - 1)
    end_index = min(total, start_index + window)
    shown = lines[start_index:end_index]
    numbered = [f"{start_index + i + 1:>5}  {line}" for i, line in enumerate(shown)]
    footer = f"[lines {start_index + 1}-{end_index} of {total}]"
    return "\n".join(numbered) + f"\n{footer}"


# ── Edit + structural gate ─────────────────────────────────────
def _check_syntax(full_path: str) -> str | None:
    """Real ast.parse() syntax check against the file's current on-disk content. None if valid, else the error text."""
    try:
        ast.parse(Path(full_path).read_text(encoding="utf-8"))
    except SyntaxError as e:
        return str(e)
    return None


def _check_lint(full_path: str) -> list[str]:
    """
    Runs `ruff check` against one real file, returns formatted violation
    strings. Deliberately duplicated from nova_completion_gate.py's own
    _ruff_violations() rather than imported -- importing that module would
    pull in its anthropic/dotenv dependencies for a single helper this
    module doesn't otherwise need, same discipline nova_corrector.py already
    established for avoiding an unwanted import chain.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format=json", full_path],
            capture_output=True,
            text=True,
            timeout=RUFF_CHECK_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    try:
        violations = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [f"{v.get('code')}: {v.get('message')} (line {v.get('location', {}).get('row')})" for v in violations]


def edit(
    path: str, start_line: int, end_line: int, new_content: str, root: str, reject_on_lint_issues: bool = False
) -> dict:
    """
    Replace lines [start_line, end_line] (1-indexed, inclusive) in `path`
    with `new_content`, then immediately run the structural gate before
    accepting the edit -- SWE-agent's own ablation found this single change
    worth +3.0 percentage points, the concrete case 86bbch95y cites for
    gating at the interface level instead of trusting model judgment.

    Format-agnostic on purpose: this is the ACI's one real edit primitive,
    independent of which text FORMAT a model uses to communicate an edit
    (whole-file/search-replace/unified-diff -- 86bbch988's own still-open
    question). Whatever harness parses a model's chosen format is
    responsible for turning it into a concrete (start_line, end_line,
    new_content) call here; this function doesn't know or care which format
    produced its arguments.

    `reject_on_lint_issues`: real, still-open tuning knob, not a guess.
    86bbch95y's remaining blocker is "exact gate behavior" needing a real
    task set to tune against, which doesn't exist yet -- defaults to False
    (only a syntax error rolls back the edit; lint issues are surfaced but
    accepted) as the more conservative starting point, since
    nova_completion_gate.py's own equivalent check treats lint as a hard
    fail at the END of a whole task, not necessarily the right severity for
    ONE line-range edit mid-task. Flip to True once real tuning data exists.

    On a syntax-error rejection, the file is restored to its exact pre-edit
    content rather than left half-applied -- matches nova_tools.
    file_replace()'s own "raise/refuse, don't silently apply an ambiguous
    edit" posture, just enforced by the structural gate instead of an
    old_str-uniqueness check.

    Returns {"accepted": bool, "syntax_error": str | None, "lint_issues": list[str]}.
    """
    resolved = _resolve_within_root(path, root)
    original_lines = resolved.read_text(encoding="utf-8").splitlines(keepends=True)

    start_index = start_line - 1
    end_index = end_line
    new_lines = new_content.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n") and end_index < len(original_lines):
        new_lines[-1] += "\n"

    edited_lines = original_lines[:start_index] + new_lines + original_lines[end_index:]
    resolved.write_text("".join(edited_lines), encoding="utf-8")

    syntax_error = _check_syntax(str(resolved))
    lint_issues = [] if syntax_error else _check_lint(str(resolved))

    reject = syntax_error is not None or (reject_on_lint_issues and lint_issues)
    if reject:
        resolved.write_text("".join(original_lines), encoding="utf-8")
        return {"accepted": False, "syntax_error": syntax_error, "lint_issues": lint_issues}

    return {"accepted": True, "syntax_error": None, "lint_issues": lint_issues}


# ── History collapsing ─────────────────────────────────────────
def collapse_history(turns: list[dict], keep_recent: int = 4) -> list[dict]:
    """
    Condenses older turns in a trajectory down to one short placeholder so a
    long multi-step task doesn't blow past a small model's limited context
    window -- 86bbch95y's 4th core command. Keeps the most recent
    `keep_recent` turns verbatim; every older turn is replaced by a single
    condensed entry, not deleted outright, so the model retains a trace that
    earlier work happened instead of the history just silently shrinking.

    Mirrors nova_orchestrator_runpod.py's own _prune_history_if_needed() --
    same "keep recent, condense older" shape -- but a flat placeholder
    summary here instead of that function's token-budget-driven pair-
    dropping, since the ACI's small-model target is expected to need
    collapsing at a much smaller window than the RunPod endpoint's real
    32,768-token ceiling.
    """
    if len(turns) <= keep_recent:
        return turns

    collapsed_count = len(turns) - keep_recent
    summary_turn = {
        "role": "system",
        "content": f"[{collapsed_count} earlier turn(s) collapsed -- see task history for full detail if needed.]",
    }
    return [summary_turn] + turns[-keep_recent:]
