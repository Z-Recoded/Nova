# nova_completion_gate.py
# Ground-truth completion gate (86bb71x39) -- a harness-level, model-
# independent check that runs on a coding task's final diff before it's
# trusted as "completed", instead of relying only on the model's own
# self-report or a downstream LLM review (nova_orchestrator._review_coding_
# diff()). Built in direct response to a real false-completion incident
# (2026-08-01 held-out eval, Task 3: the model stopped after reading 2 files
# with zero tool calls and a plain-text summary, and the turn loop's own
# "no more tool calls means done" logic reported "completed" against a
# genuinely empty diff) and a real research finding cited on the sibling
# self-verification task (86bb71x2a): LLM judges reading a trajectory/diff
# are unreliable at catching false completion (AUROC <= 0.65 across 5
# judges/5 prompting strategies). This gate deliberately never judges the
# diff's correctness -- only mechanical, structural facts about it.
#
# extract_task_requirements() is the one piece that DOES call Claude -- but
# only to parse the task's own spec text into structure, before any work has
# started. It never sees the diff or the agent's trajectory, so it is not
# the "LLM judge" pattern the research above warns about -- it's the same
# shape as nova_task_queue.propose_tier()'s existing non-agentic triage
# call, not a correctness judgment.

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Loaded here (not just relied on from nova_orchestrator.py's own
# load_dotenv() call) so this module works correctly when imported or run
# standalone, same discipline as nova_orchestrator.py/nova_remote_inference.py.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────

# Matches nova_orchestrator.NOVA_AGENT_MODEL -- duplicated here rather than
# imported, since nova_orchestrator.py is the one that imports this module
# (importing it back would be circular).
EXTRACTION_MODEL = "claude-sonnet-5"

# Generous relative to the extraction task's small expected output (a JSON
# object with a few short string lists) -- but extended thinking can eat an
# entire small token budget before any real text is emitted, regardless of
# how simple the final output is. Exact lesson nova_orchestrator.
# _review_coding_diff() learned the hard way at max_tokens=600 on a real
# diff-review call (5/6 reviews came back with no usable text block).
# Sized well above that risk, not just the expected output length.
EXTRACTION_MAX_TOKENS = 1024

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured requirements from a software task description. "
    "You are not being asked whether any work is correct or complete -- only "
    "to identify what the task text itself explicitly names, before any work "
    "has started. Respond with ONLY a JSON object, no other text, in exactly "
    "this shape:\n\n"
    '{"required_files": ["<path or filename explicitly named as something to '
    'create or modify>", ...], '
    '"forbidden_files": ["<path or filename the task explicitly says NOT to '
    'touch or change, or says to preserve/leave unchanged>", ...], '
    '"narrow_scope_files": ["<path or filename the task explicitly says to '
    "change only a small, targeted amount -- phrasing like 'only add X', "
    "'just change Y', 'preserve all existing behavior otherwise' -- as "
    "opposed to a rewrite, refactor, or broad restructuring. Do not include a "
    "file here unless the task text draws a real contrast between a small "
    'change and a bigger one it does NOT want>", ...], '
    '"deliverables": ["<specific named function, route, class, or constant '
    'the task explicitly asks to exist when finished>", ...]}\n\n'
    "Only include items the task text explicitly names. Do not guess or "
    "infer files/functions that seem related but aren't actually named. "
    "Empty lists are correct and expected when the task doesn't name "
    "anything in a category."
)


def extract_task_requirements(task_description: str) -> dict:
    """
    One-time, non-agentic Claude call that parses a task's own spec text
    into structured requirements, before any work starts -- see this
    module's header comment for why this is not the "LLM judge" pattern
    the false-completion research warns against (it never sees a diff or
    trajectory, only the original task text).

    Mirrors nova_task_queue.propose_tier()'s established pattern: a plain
    client.messages.create() call, no tool use, no second turn. Same
    ThinkingBlock gotcha already found live in propose_tier()/
    request_correction()/_review_coding_diff() (86bb53hmk) -- find the
    first block with type == "text" explicitly, never assume content[0]
    is the text block.

    Returns {"required_files": [...], "forbidden_files": [...],
    "narrow_scope_files": [...], "deliverables": [...]}. Fails toward an
    all-empty result (every downstream check becomes a no-op, not a false
    failure) on a missing API key or any parse/API failure -- this is an
    enhancement over a bare nonzero-diff check, not the sole source of
    truth, so failing open is the right default here. Unlike propose_tier()'s
    fail-toward-restrictive (an under-confident tier is the safe direction
    there), an under-populated requirement list is the safe direction here
    -- it silently skips a check rather than raising a false alarm off a
    malformed extraction.
    """
    empty_result = {
        "required_files": [],
        "forbidden_files": [],
        "narrow_scope_files": [],
        "deliverables": [],
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return empty_result

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=EXTRACTION_MAX_TOKENS,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": task_description}],
        )
        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            return empty_result
        raw = text_blocks[0].strip()
        # Same markdown-fence gotcha nova_task_queue.propose_tier() found
        # live 2026-07-19 -- Claude sometimes wraps the JSON in ```json
        # despite being told not to.
        unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
        parsed = json.loads(unfenced)
        return {
            "required_files": list(parsed.get("required_files", [])),
            "forbidden_files": list(parsed.get("forbidden_files", [])),
            "narrow_scope_files": list(parsed.get("narrow_scope_files", [])),
            "deliverables": list(parsed.get("deliverables", [])),
        }
    except (anthropic.APIError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return empty_result


# ── Diff parsing helpers ──────────────────────────────────────

_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _touched_files(diff: str) -> list[str]:
    """
    Real file paths (post-edit side) touched by a unified git diff, parsed
    from each `diff --git a/X b/Y` header line. Uses the b/ (destination)
    path -- correct for additions, modifications, and renames alike; a
    straight deletion's b/ path won't exist on disk anymore, which the
    syntax check below already handles by skipping unreadable paths.
    """
    return [match.group(2) for match in _DIFF_FILE_HEADER_RE.finditer(diff)]


def _added_lines_text(diff: str) -> str:
    """
    Concatenated text of every added line (`+...`, excluding the `+++`
    file-header line) across the whole diff -- the substring space the
    deliverable-presence check searches against.
    """
    lines = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


# ── Checks ─────────────────────────────────────────────────────


def _check_nonzero_diff(diff: str) -> str | None:
    """Hard-fail reason if the diff is empty/whitespace-only, else None."""
    if not diff.strip():
        return "The diff is empty -- no changes were made, but the task did not report an incomplete/halted status."
    return None


def _check_required_files_touched(diff: str, required_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec explicitly named as
    something to create or modify (extract_task_requirements()'s
    required_files) that the diff never touched at all. Matches by
    filename, not exact repo-relative path -- required_files entries are
    free-text extractions that may not carry the exact path the diff
    header uses.
    """
    if not required_files:
        return []
    touched_names = {os.path.basename(path) for path in _touched_files(diff)}
    reasons = []
    for required in required_files:
        required_name = os.path.basename(required.strip())
        if required_name and required_name not in touched_names:
            reasons.append(f"'{required}' was named as a file the task requires touching, but it was never touched.")
    return reasons


def _check_syntax_valid(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per touched .py file that doesn't parse as valid
    Python in its current (post-edit) worktree state. Skips files the diff
    touched but that no longer exist on disk (a straight deletion) and
    non-.py files entirely -- this check is Python-source-only, same scope
    as nova_orchestrator_runpod._find_duplicate_functions().
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        try:
            source = open(full_path, encoding="utf-8").read()
        except OSError:
            continue
        try:
            ast.parse(source)
        except SyntaxError as e:
            reasons.append(f"'{path}' does not parse as valid Python: {e}")
    return reasons


def _check_forbidden_paths_untouched(diff: str, forbidden_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec explicitly named as
    off-limits (extract_task_requirements()'s forbidden_files) that the
    diff touched anyway. Matches by filename, same rationale as
    _check_required_files_touched().

    Real gap found by testing this function against the actual 2026-07-29
    scope-violation incident's real task text: that task's forbidden files
    (nova_api.py/nova_tools.py/nova_orchestrator.py) were never touched --
    the model's real violation was drastically over-rewriting an *allowed*
    file (nova_query.py) far beyond the "just add an early-return branch"
    scope, deleting the real RAG pipeline in the process. This check
    catches "touched a fully off-limits file." The sibling shape --
    "touched an allowed file far more than the task intended" -- is what
    _check_narrow_scope_not_exceeded() below targets instead.
    """
    if not forbidden_files:
        return []
    touched = _touched_files(diff)
    reasons = []
    for forbidden in forbidden_files:
        forbidden_name = os.path.basename(forbidden.strip())
        if not forbidden_name:
            continue
        for touched_path in touched:
            if os.path.basename(touched_path) == forbidden_name:
                reasons.append(
                    f"'{touched_path}' was touched, but the task explicitly said not to change '{forbidden}'."
                )
    return reasons


# A file changed more than this fraction of its original line count is
# treated as "far more than a small/targeted edit" -- picked to be well
# above normal editing noise (a real single-purpose edit rarely rewrites
# more than half a file) while still catching a wholesale rewrite like the
# 2026-07-29 incident (the RAG pipeline was not trimmed, it was replaced).
# Deliberately approximate, not a precise line -- see this check's own
# docstring on tuning.
NARROW_SCOPE_CHANGE_RATIO_THRESHOLD = 0.5

# Below this many original lines, "50% changed" isn't a meaningful signal
# (a 10-line file losing 6 lines to a genuinely small edit is normal) --
# skip the ratio check entirely for files this small.
NARROW_SCOPE_MIN_ORIGINAL_LINES = 20


def _diff_numstat(root: str, base_ref: str) -> dict[str, tuple[int, int]]:
    """
    {path: (added, removed)} for every file changed between base_ref and
    the worktree's current state, via `git diff --numstat` -- more
    reliable than hand-parsing the diff text's hunk headers (which only
    carry as much context as git's default -U3, not necessarily enough to
    infer a file's real total line count).
    """
    result = subprocess.run(
        ["git", "diff", "--numstat", base_ref],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stats: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if added == "-" or removed == "-":
            continue  # binary file -- numstat reports "-" instead of a count
        stats[path] = (int(added), int(removed))
    return stats


def _original_line_count(root: str, base_ref: str, path: str) -> int | None:
    """Total line count of `path` at base_ref, or None if it didn't exist there (a new file)."""
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return len(result.stdout.splitlines())


def _check_narrow_scope_not_exceeded(root: str, base_ref: str, narrow_scope_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec said should get
    only a small, targeted edit (extract_task_requirements()'s
    narrow_scope_files) whose real diff removed more than
    NARROW_SCOPE_CHANGE_RATIO_THRESHOLD of its original line count.

    Built in direct response to the real gap _check_forbidden_paths_
    untouched() documents in its own docstring: the 2026-07-29 scope-
    violation incident's real violation was over-rewriting an ALLOWED file
    (nova_query.py) far beyond the "just add an early-return branch" scope,
    not touching a forbidden one. This check targets exactly that shape --
    matched by filename, same rationale as the other checks in this module.

    Line-count ratio is a real approximation, not a precise correctness
    signal -- a legitimate large refactor explicitly asked for elsewhere in
    the same task would not be flagged (only files actually named as
    narrow-scope are checked), but a genuinely small file with unusual
    formatting could still produce a false positive. Treated as a hard fail
    despite that, because this check exists specifically for the most
    severe entry in the failure registry (a full pipeline deletion) --
    surfacing an occasional false positive for a human to dismiss is a far
    better trade than missing a repeat of that incident.
    """
    if not narrow_scope_files:
        return []
    stats = _diff_numstat(root, base_ref)
    reasons = []
    for narrow_file in narrow_scope_files:
        name = os.path.basename(narrow_file.strip())
        if not name:
            continue
        matching_path = next((path for path in stats if os.path.basename(path) == name), None)
        if matching_path is None:
            continue  # not touched at all -- not this check's concern
        _added, removed = stats[matching_path]
        original_lines = _original_line_count(root, base_ref, matching_path)
        if original_lines is None or original_lines < NARROW_SCOPE_MIN_ORIGINAL_LINES:
            continue
        change_ratio = removed / original_lines
        if change_ratio > NARROW_SCOPE_CHANGE_RATIO_THRESHOLD:
            reasons.append(
                f"'{matching_path}' was marked for a small/targeted edit only, but {removed} of its "
                f"original {original_lines} lines were removed ({change_ratio:.0%}) -- looks like a "
                f"much larger rewrite than the task asked for."
            )
    return reasons


def _check_deliverables_present(diff: str, deliverables: list[str]) -> list[str]:
    """
    Soft-flag warnings, one per named deliverable (extract_task_requirements()'s
    deliverables) that never appears in any added line of the diff. Not a
    hard fail -- free-text extraction has real false-positive risk (a name
    mentioned only for context, not as something to create), so this is
    surfaced for the human/review pass rather than blocking completion.
    """
    if not deliverables:
        return []
    added_text = _added_lines_text(diff)
    warnings = []
    for deliverable in deliverables:
        name = deliverable.strip()
        if name and name not in added_text:
            warnings.append(f"'{name}' was named as a deliverable but never appears in the diff's added lines.")
    return warnings


# ── Entry point ────────────────────────────────────────────────


def check_ground_truth_completion(
    diff: str, task_description: str, root: str, base_ref: str = "master", requirements: dict | None = None
) -> dict:
    """
    Runs every ground-truth check and returns {"passed": bool, "hard_fails":
    [...], "warnings": [...]}. "passed" is False if any hard-fail check
    found something -- callers should surface that loudly (see
    nova_orchestrator.run_coding_task()'s commit_note handling) rather than
    let a false "completed" status go unnoticed, per 86bb71x39's whole
    point. Never blocks the commit itself -- Marvin reviews every diff by
    hand regardless, same non-blocking precedent as _review_coding_diff().

    base_ref defaults to "master" -- correct for run_coding_task()'s real
    call site, which always diffs against master via
    _git_diff_against_master(). Only needs overriding by a caller diffing
    against a different base (e.g. a held-out eval harness using a
    historical task's real pre-merge commit).

    requirements: pass a dict already produced by extract_task_requirements()
    to skip re-extracting it here -- the RunPod backend's task-scoped file
    allowlist guard (86bb72wd5) needs this exact extraction before the task
    even starts, and re-running the same Claude call a second time at the
    end of the same task would be a real, avoidable duplicate cost. None
    (the default) preserves the original behavior: extract fresh here.

    An empty diff short-circuits immediately: there is no point extracting
    requirements or checking syntax/deliverables against a diff with
    nothing in it, and skipping the API call here means a genuinely
    incomplete task never spends a Claude call it doesn't need.
    """
    empty_diff_reason = _check_nonzero_diff(diff)
    if empty_diff_reason:
        return {"passed": False, "hard_fails": [empty_diff_reason], "warnings": []}

    if requirements is None:
        requirements = extract_task_requirements(task_description)

    hard_fails = []
    hard_fails.extend(_check_syntax_valid(diff, root))
    hard_fails.extend(_check_required_files_touched(diff, requirements["required_files"]))
    hard_fails.extend(_check_forbidden_paths_untouched(diff, requirements["forbidden_files"]))
    hard_fails.extend(_check_narrow_scope_not_exceeded(root, base_ref, requirements["narrow_scope_files"]))
    warnings = _check_deliverables_present(diff, requirements["deliverables"])

    return {"passed": not hard_fails, "hard_fails": hard_fails, "warnings": warnings}
