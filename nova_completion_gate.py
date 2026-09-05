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
import builtins
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
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

# On-disk cache for extract_task_requirements(), keyed by a hash of the
# exact task text -- avoids re-paying for the same extraction every time
# the same task description is seen twice. Real, confirmed duplication:
# nova_coding_eval.py's three backend runners (run_runpod_backend/
# run_devstral_backend/run_qwen3_backend) each call this fresh for the
# same fixed dev-set tasks, repeated across every eval session, and this
# function's own docstring already documents (2026-08-08) that its output
# is stable across repeated calls with the same input -- required_files/
# forbidden_files/narrow_scope_files (the fields hard-fail checks depend
# on) came back byte-identical across 3 repeated real calls; only
# deliverables (warning-level only) showed minor wording variance.
# logs/ is gitignored -- this cache is regenerable, never meant to be
# committed, same as every other file already written there.
TASK_REQUIREMENTS_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "task_requirements_cache.json"
)

# Host-side PowerShell syntax check (see _check_powershell_syntax_valid()
# below). This gate runs unsandboxed in the orchestrator's own trusted
# process, unlike a coding task's own run_command calls -- confirmed live
# (2026-08-08) that run_command has no path to a real PowerShell interpreter
# at all (its restricted PATH excludes system directories, and the
# 2026-07-24 outside-root-argument guard blocks an absolute-path fallback
# too), so a model task can never self-verify .ps1 output today. This check
# closes that gap from here instead.
POWERSHELL_EXE = "powershell.exe"
POWERSHELL_SYNTAX_CHECK_TIMEOUT_SECONDS = 15

# Ruff-based lint check (see _check_lint_clean() below). Invoked as
# `sys.executable -m ruff` rather than a hardcoded `ruff`/`ruff.exe` path --
# ruff is a pinned pip dependency (requirements.txt), not a system tool like
# powershell.exe above, so resolving it through the same interpreter this
# module is already running under is portable across Aero/Omen without
# assuming a binary is on PATH.
RUFF_CHECK_TIMEOUT_SECONDS = 20

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured requirements from a software task description. "
    "You are not being asked whether any work is correct or complete -- only "
    "to identify what the task text itself explicitly names, before any work "
    "has started.\n\n"
    "Read the ENTIRE task text before answering, not just the numbered/lettered "
    "implementation steps. Real task descriptions very often state their most "
    "important constraints -- especially forbidden_files and narrow_scope_files "
    "-- as a single sentence appended near the END of a longer paragraph, after "
    "all the technical implementation detail, not as their own numbered step. A "
    "real, confirmed miss (2026-08-08): a task whose final paragraph read "
    '"...be careful to preserve all existing behavior for every other category '
    "exactly as-is; only add the new early-return branch. Do not touch "
    'nova_api.py, nova_tools.py, or nova_orchestrator.py itself." was extracted '
    "with completely empty forbidden_files AND empty narrow_scope_files, three "
    "times in a row, deterministically -- even though that sentence explicitly "
    "names three forbidden files and draws an explicit narrow-scope contrast for "
    "a fourth. The correct extraction from that exact sentence would have been "
    'forbidden_files: ["nova_api.py", "nova_tools.py", "nova_orchestrator.py"] '
    'and narrow_scope_files: ["nova_query.py"] (from "only add the new '
    '"early-return branch" / "preserve all existing behavior otherwise"). Do '
    "not let a long or technically dense task body cause you to under-weight "
    "its concluding sentences -- scan the last paragraph specifically for "
    "constraint language before finalizing your answer.\n\n"
    "Respond with ONLY a JSON object, no other text, in exactly this shape:\n\n"
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


def _task_requirements_cache_key(task_description: str) -> str:
    """SHA-256 of the exact task text -- same hashing discipline as nova_task_queue._description_hash()."""
    return hashlib.sha256(task_description.encode("utf-8")).hexdigest()


def _load_task_requirements_cache() -> dict:
    """{cache_key: requirements_dict}. Empty dict if the cache file doesn't exist yet or is corrupt."""
    if not os.path.exists(TASK_REQUIREMENTS_CACHE_PATH):
        return {}
    try:
        with open(TASK_REQUIREMENTS_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_task_requirements_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(TASK_REQUIREMENTS_CACHE_PATH), exist_ok=True)
    with open(TASK_REQUIREMENTS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


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

    NO temperature/top_p passed -- real, much bigger bug found and fixed
    2026-08-08, superseding the temperature=0 fix this docstring used to
    describe: `temperature` (and `top_p`) are now deprecated for
    EXTRACTION_MODEL and the API call was failing outright on EVERY call
    with a 400 BadRequestError, silently caught by the except clause below
    and returned as empty_result -- indistinguishable from "the task text
    just doesn't name anything." This wasn't a subtle prompt-tuning problem;
    the extraction had been completely non-functional, always, for as long
    as this model was configured here. Confirmed live: the exact same task
    text that always came back all-empty returned the fully correct
    forbidden_files/narrow_scope_files the moment temperature was dropped.
    Real-world determinism re-checked afterward (3 repeated calls, same
    task text, no sampling params): required_files/forbidden_files/
    narrow_scope_files -- the three categories that feed hard-fail checks
    below -- were identical every time; only deliverables (warning-level
    only, never a hard fail) showed minor wording variance run to run. Good
    enough without a temperature knob to hold onto.

    Cached on disk (TASK_REQUIREMENTS_CACHE_PATH), keyed by an exact hash
    of task_description -- the determinism confirmed above is exactly what
    makes this safe. Only a genuinely successful, parsed extraction is
    cached; empty_result (API-key-missing or parse/API failure) is never
    written, so a transient failure can't permanently poison the cache for
    a task text that would otherwise extract cleanly on a later real call.
    """
    empty_result = {
        "required_files": [],
        "forbidden_files": [],
        "narrow_scope_files": [],
        "deliverables": [],
    }

    cache_key = _task_requirements_cache_key(task_description)
    cache = _load_task_requirements_cache()
    if cache_key in cache:
        return cache[cache_key]

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
        result = {
            "required_files": list(parsed.get("required_files", [])),
            "forbidden_files": list(parsed.get("forbidden_files", [])),
            "narrow_scope_files": list(parsed.get("narrow_scope_files", [])),
            "deliverables": list(parsed.get("deliverables", [])),
        }
        cache[cache_key] = result
        _save_task_requirements_cache(cache)
        return result
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


def _find_untracked_file_by_basename(root: str, basename: str) -> bool:
    """
    True if a file named `basename` exists anywhere under the task's own
    worktree (`root`), regardless of whether git tracks it.

    Exists specifically for a required file living under a gitignored path
    (e.g. a `logs/*.jsonl` file) -- _touched_files() parses `git diff`
    output, which structurally can never show a gitignored file no matter
    what the model actually did. Real, verified gap: `logs/` is gitignored
    repo-wide, and `logs/benchmark_log.jsonl` has never once been tracked in
    this repo's git history (confirmed via `git check-ignore` and `git log
    --all`) -- so a task requiring it would hard-fail this check for every
    candidate, including a hypothetically perfect one, without this
    fallback.

    Safe to treat plain existence as real evidence here (no mtime check
    needed): `git worktree add` only ever populates a fresh worktree from
    tracked content, confirmed live by inspecting a real fresh worktree --
    it carries no `logs/` directory at all. So a gitignored file found
    inside one MUST have been created by the model's own run_command
    execution during this exact task, not inherited from the main repo.

    Matches anywhere in the tree, by basename only -- same "match by
    filename, not exact path" discipline the git-diff-based check above
    already uses, since required_files is a free-text extraction that may
    carry an absolute path straight from the task's own description (e.g.
    "C:/Nova/logs/benchmark_log.jsonl") rather than a worktree-relative one.
    """
    for _dirpath, _dirnames, filenames in os.walk(root):
        if basename in filenames:
            return True
    return False


def _check_required_files_touched(diff: str, required_files: list[str], root: str) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec explicitly named as
    something to create or modify (extract_task_requirements()'s
    required_files) that the diff never touched at all. Matches by
    filename, not exact repo-relative path -- required_files entries are
    free-text extractions that may not carry the exact path the diff
    header uses.

    A required file not found in the diff gets one more chance via
    _find_untracked_file_by_basename() before being flagged -- see that
    function's own docstring for the real gitignored-file gap this closes.
    """
    if not required_files:
        return []
    touched_names = {os.path.basename(path) for path in _touched_files(diff)}
    reasons = []
    for required in required_files:
        required_name = os.path.basename(required.strip())
        if not required_name:
            continue
        if required_name in touched_names:
            continue
        if _find_untracked_file_by_basename(root, required_name):
            continue
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


def _powershell_literal_string(value: str) -> str:
    """
    Escape `value` as a PowerShell single-quoted string literal (double any
    embedded single quotes). Single-quoted literals do no interpolation at
    all in PowerShell, so this is safe against injection from a diff's own
    file paths -- unlike a double-quoted string, which would need $/`
    escaping too.
    """
    return "'" + value.replace("'", "''") + "'"


def _powershell_parse_error(full_path: str) -> str | None:
    """
    Runs PSParser.Tokenize() (parse-only -- never executes the script)
    against `full_path` via a real, unsandboxed powershell.exe subprocess.
    Returns the joined parse-error text if the file doesn't parse, None if
    it parses clean. Also returns None -- fails open, doesn't block the gate
    -- if powershell.exe isn't available on this host or the call times out,
    same accepted-gap philosophy every other check in this file already uses
    when it can't get a confident answer.
    """
    ps_path = _powershell_literal_string(full_path)
    script = (
        "$parseErrors = $null; "
        f"[void][System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -LiteralPath {ps_path}), "
        "[ref]$parseErrors); "
        "$parseErrors | ForEach-Object { Write-Output $_.Message }"
    )
    try:
        result = subprocess.run(
            [POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=POWERSHELL_SYNTAX_CHECK_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    output = result.stdout.strip()
    return output or None


def _check_powershell_syntax_valid(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per touched .ps1 file that doesn't parse as valid
    PowerShell in its current (post-edit) worktree state -- the PowerShell
    analog of _check_syntax_valid() above. Skips files the diff touched but
    that no longer exist on disk (a straight deletion) and non-.ps1 files
    entirely. Only catches real syntax/corruption defects (e.g. Devstral's
    "corrupted PowerShell" finding, 2026-08-03) -- a logic bug in
    syntactically valid PowerShell, like an inverted if/else, is out of
    scope for this check by design, same as _check_syntax_valid() not
    catching a Python logic bug either.
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".ps1"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        error = _powershell_parse_error(full_path)
        if error:
            reasons.append(f"'{path}' does not parse as valid PowerShell: {error}")
    return reasons


def _ruff_check_raw(args: list[str], input_bytes: bytes | None = None) -> list[dict] | None:
    """
    Shared `ruff check --output-format=json` runner -- returns the raw
    parsed violation dicts, or None if ruff couldn't be run at all (not
    installed, timeout, any subprocess error) -- fails open, same
    accepted-gap philosophy as _powershell_parse_error() above. An empty
    list (as opposed to None) means ruff ran successfully and found
    nothing. Takes raw `args` (a real file path, or `--stdin-filename=...`
    plus `-` to lint content read from `input_bytes`) so both
    _ruff_violations() (on-disk file) and _base_commit_ruff_violations()
    (a git-history blob, no worktree file to point at) share one
    subprocess/JSON-parsing path.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format=json", *args],
            input=input_bytes,
            capture_output=True,
            timeout=RUFF_CHECK_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    try:
        return json.loads(result.stdout or b"[]")
    except json.JSONDecodeError:
        return None


def _format_ruff_violations(violations: list[dict]) -> list[str]:
    """One formatted string per violation: 'CODE: message (line N)'."""
    return [f"{v.get('code')}: {v.get('message')} (line {v.get('location', {}).get('row')})" for v in violations]


def _ruff_violations(full_path: str) -> list[str] | None:
    """
    Runs `ruff check` against `full_path` (the current, post-edit worktree
    file) and returns one formatted string per violation, or None if ruff
    couldn't be run at all.
    """
    violations = _ruff_check_raw([full_path])
    if violations is None:
        return None
    return _format_ruff_violations(violations)


def _ruff_violation_signature_counts(violations: list[dict]) -> Counter:
    """
    Multiset of (code, message) pairs, deliberately dropping line number --
    a diff shifts every line below an edit, so two violations at different
    line numbers but the same code+message are the same real issue, not two.
    Used only to tell "already present at base_ref" apart from "new in this
    diff"; _format_ruff_violations() (which does keep line numbers) is what
    actually gets shown to a human.
    """
    return Counter((v.get("code"), v.get("message")) for v in violations)


def _base_commit_ruff_violations(root: str, base_ref: str, rel_path: str) -> list[dict] | None:
    """
    Ruff violations for rel_path as it existed at base_ref -- lints the
    git-history blob via stdin (`--stdin-filename` gives ruff the real
    filename for its per-path config resolution, e.g. per-file-ignores in
    pyproject.toml) rather than needing a second real checkout. Returns None
    if the file didn't exist at base_ref (a brand-new file this diff added --
    nothing to subtract, every violation in it is this diff's) or if git/ruff
    couldn't be run. Deliberately captures git's output as raw bytes, no
    `text=True` decode -- avoids the exact cp1252-on-Windows corruption
    nova_synthetic_task_gen.py hit on a real em-dash diff (2026-08-12
    changelog); ruff reads bytes from stdin just as happily as text.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{base_ref}:{rel_path}"],
            cwd=root,
            capture_output=True,
            timeout=RUFF_CHECK_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return _ruff_check_raw([f"--stdin-filename={rel_path}", "-"], input_bytes=result.stdout)


def _new_ruff_violations(current: list[dict], base: list[dict] | None) -> list[dict]:
    """
    current violations minus whatever (code, message) signatures were
    already present at base_ref, as a multiset difference -- 3 duplicate
    pre-existing E501s in current only cancel out 3 matching E501s in base,
    not all of them. base=None (couldn't determine the base-ref state) means
    every current violation counts as new, preserving the gate's original
    fail-open-toward-flagging behavior for that case.
    """
    if base is None:
        return current
    remaining = _ruff_violation_signature_counts(current) - _ruff_violation_signature_counts(base)
    new_violations = []
    for v in current:
        sig = (v.get("code"), v.get("message"))
        if remaining.get(sig, 0) > 0:
            new_violations.append(v)
            remaining[sig] -= 1
    return new_violations


def _check_lint_clean(diff: str, root: str, base_ref: str = "master") -> list[str]:
    """
    Hard-fail reasons, one per touched .py file that ruff flags real lint
    issues on in its current (post-edit) worktree state, AFTER subtracting
    whatever ruff already flagged in that same file at base_ref -- the
    "test/lint pass, where a suite exists for the repo" check from
    86bb71x39's original list. This repo has no pytest suite, but it does
    have a real, already-wired lint tool (ruff, pyproject.toml's
    [tool.ruff], enforced by the pre-commit hook in .pre-commit-config.yaml)
    -- this check runs that same tool earlier, at gate time.

    Base-commit diffing added 2026-09-04 (Eval Harness Initiative 2's
    lint_clean follow-up, docs/aci-completion-gate-audit-scope.md): the
    original version assumed the whole repo is always ruff-clean, so any
    violation on a touched file must be diff-caused. That assumption held
    up on 3 real held-out diffs (2026-09-02), but was never structurally
    guaranteed -- a worktree branched from a `master` with transient lint
    drift (nova_api.py, the most-churned file, accounted for 15/26 of the
    dev-set's fires) could misattribute pre-existing debt to the task.
    Diffing against base_ref's own ruff output for the same file closes
    that gap outright rather than just accumulating more anecdotal
    held-out evidence that it hasn't happened yet.

    Deliberately still checks the whole current file, not just diff-added
    lines (same approach as _check_syntax_valid()/
    _check_powershell_syntax_valid() above, no new diff-to-line-number
    mapping machinery) -- the base-ref subtraction is what makes "whole
    file" safe now, instead of relying on repo-wide cleanliness discipline.

    Also closes a real ordering gap: check_ground_truth_completion() runs
    *before* nova_orchestrator._commit_worktree_changes()'s `git commit`,
    which does go through pre-commit's ruff hook (never bypassed with
    --no-verify) -- so today the gate could say "passed" on code pre-commit
    would actually reject or reformat. This gives the gate the same signal,
    earlier, instead of a task looking "complete" right up until the commit
    step surprises it.
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        current_violations = _ruff_check_raw([full_path])
        if not current_violations:
            continue
        base_violations = _base_commit_ruff_violations(root, base_ref, path)
        new_violations = _new_ruff_violations(current_violations, base_violations)
        if not new_violations:
            continue
        formatted = _format_ruff_violations(new_violations)
        summary = "; ".join(formatted[:5])
        if len(formatted) > 5:
            summary += f"; and {len(formatted) - 5} more"
        reasons.append(f"'{path}' has unresolved ruff lint issues: {summary}")
    return reasons


def _assignment_target_names(target: ast.expr) -> set[str]:
    """Real names one assignment/for/with target binds -- attribute/subscript targets don't bind a new name."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names |= _assignment_target_names(elt)
        return names
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    return set()


def _names_bound_by_statement(stmt: ast.stmt) -> set[str]:
    """
    Names a single top-level statement adds to the module namespace once it
    finishes executing. Deliberately conservative: statement kinds not
    explicitly handled here (if/try/while/for-else, etc.) contribute no
    names -- same accepted-gap philosophy as every other best-effort check
    in this file (e.g. nova_tools._cd_targets_outside_root's own doc on
    what it doesn't try to model). A name legitimately bound only inside a
    top-level `if` block would be treated as unbound afterward by this
    checker, a known, accepted false-positive risk -- this codebase doesn't
    lean on that pattern today (verified against every real top-level .py
    file in the repo before this check was wired in).
    """
    if isinstance(stmt, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in stmt.names}
    if isinstance(stmt, ast.ImportFrom):
        return {alias.asname or alias.name for alias in stmt.names if alias.name != "*"}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        names: set[str] = set()
        for target in stmt.targets:
            names |= _assignment_target_names(target)
        return names
    if isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
        return _assignment_target_names(stmt.target)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _assignment_target_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        names = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                names |= _assignment_target_names(item.optional_vars)
        return names
    return set()


class _TopLevelLoadNameCollector(ast.NodeVisitor):
    """
    Collects every ast.Name(ctx=Load) referenced within one top-level
    statement's OWN immediate execution -- explicitly not descending into
    nested function/lambda bodies, since those run later, at call time, by
    which point the whole module has finished loading and a forward
    reference to a name defined further down the file is completely
    legitimate (the normal, common case, not a bug). A class body, unlike a
    function body, DOES execute immediately when the ClassDef statement
    runs, so it's walked normally; methods defined inside that class body
    are themselves function bodies and are skipped the same way as any
    other nested function.

    Known, accepted scope limit: a function's own decorator expressions and
    default-argument values technically evaluate at def-time too, but
    aren't walked here -- narrowing this to the two real, observed bug
    shapes (a module-level dict/expression statement referencing a name not
    yet bound) rather than a fully exhaustive checker, matching this file's
    established best-effort philosophy elsewhere.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # deferred execution -- don't descend

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def _visit_comprehension(self, node) -> None:
        """
        List/set/dict comprehensions and generator expressions create their
        own scope in Python 3 -- the loop variable(s) they bind (e.g. `src`
        in `[src["path"] for src in SOURCES]`) are valid only within the
        comprehension itself and never need to already exist outside it.
        Real false positives found and fixed before this check was wired
        in: 6 of this repo's own real files hit exactly this shape, most
        commonly `[x[...] for x in SOME_LIST]` at module level.

        The first generator's iterable is the one exception -- it evaluates
        in the ENCLOSING scope (there'd be nothing to iterate otherwise),
        so it's visited normally against the outer collector's own bound
        names. Everything else (the element expression, any `if` filters,
        and any later generators in a multi-`for` comprehension) is
        collected separately and only flagged if it references something
        neither comprehension-local nor already bound outside.
        """
        comp_bound: set[str] = set()
        for index, generator in enumerate(node.generators):
            if index == 0:
                self.visit(generator.iter)
            else:
                nested = _TopLevelLoadNameCollector()
                nested.visit(generator.iter)
                self.names |= nested.names - comp_bound
            comp_bound |= _assignment_target_names(generator.target)
            for if_clause in generator.ifs:
                nested = _TopLevelLoadNameCollector()
                nested.visit(if_clause)
                self.names |= nested.names - comp_bound

        elt_nodes = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        for elt in elt_nodes:
            nested = _TopLevelLoadNameCollector()
            nested.visit(elt)
            self.names |= nested.names - comp_bound

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension


# Known process-exit calls -- used only by _handler_terminates() below to
# recognize the one real, common try/except shape found in this repo:
# `except ...: print(...); sys.exit(1)`. A bare name (exit/quit, the
# REPL/interactive builtins) or a `module.func` attribute call matching one
# of these pairs is treated as "this path never falls through."
_EXIT_CALL_NAMES = {"exit", "quit"}
_EXIT_CALL_ATTRS = {("sys", "exit"), ("os", "_exit"), ("os", "abort")}


def _statement_terminates_control_flow(stmt: ast.stmt) -> bool:
    """
    True if `stmt` unconditionally ends control flow (raises, returns,
    breaks/continues, or calls a known process-exit function) rather than
    falling through to whatever comes after it. Deliberately narrow -- not
    a general reachability analyzer, just enough to recognize the one real
    pattern this check needs (see _handler_terminates()'s own docstring).
    """
    if isinstance(stmt, (ast.Raise, ast.Return, ast.Break, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Name) and func.id in _EXIT_CALL_NAMES:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in _EXIT_CALL_ATTRS:
                return True
    return False


def _handler_terminates(handler: ast.ExceptHandler) -> bool:
    """
    True if an except handler's last statement always ends control flow
    instead of falling through. Real, common pattern found and fixed
    before this check was wired in: `try: capacity_report = f() except
    (...): print(...); sys.exit(1)` -- the ONLY way code after the
    try/except runs is via the try body succeeding, since the handler
    always exits the process, so `capacity_report` is safe to treat as
    bound afterward even though it was only assigned inside the try body.
    """
    if not handler.body:
        return False
    return _statement_terminates_control_flow(handler.body[-1])


# Names always available in a module's namespace without an explicit
# import/assignment -- Python builtins plus the standard module dunders
# present in every module by default.
_ALWAYS_BOUND_NAMES = frozenset(dir(builtins)) | {
    "__name__",
    "__file__",
    "__doc__",
    "__builtins__",
    "__package__",
    "__spec__",
    "__loader__",
}


def _check_statement_sequence(statements: list[ast.stmt], bound: set[str], path: str) -> list[str]:
    """
    Checks one ordered sequence of statements (a module body, or the nested
    body/orelse/handler/finalbody of a compound statement) against `bound`,
    mutating it in place as each statement's own bindings land -- so a
    later statement in the SAME sequence correctly sees names bound by an
    earlier one.

    Recurses into if/while/for/with/try so a very common real pattern --
    `if __name__ == "__main__": parser = argparse.ArgumentParser(); args =
    parser.parse_args()` -- is tracked correctly in order (an earlier line
    inside the block legitimately binds a name a later line inside the SAME
    block then uses). Real bug found and fixed before this check was ever
    wired into the gate: treating a compound statement's whole body as one
    opaque, unordered blob (rather than recursing into it as its own
    sequence) produced 43 false positives across this repo's own real
    files, every single one this exact if-__main__-block shape -- would
    have made the gate cry wolf constantly, worse than not having the
    check at all.

    Nested bindings are checked against a COPY of `bound`, never merged
    back into the caller's set afterward -- a conditional block might not
    run at all, so anything it binds should not be assumed available to
    code after the block ends. Conservative, matches Python's real "maybe
    bound" semantics; this codebase doesn't lean on top-level conditional
    imports today (verified in the same false-positive sweep above).
    """
    reasons: list[str] = []
    for stmt in statements:
        own_names = _TopLevelLoadNameCollector()
        if isinstance(stmt, ast.If):
            own_names.visit(stmt.test)
        elif isinstance(stmt, ast.While):
            own_names.visit(stmt.test)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            own_names.visit(stmt.iter)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                own_names.visit(item.context_expr)
        elif isinstance(stmt, ast.Try):
            pass  # nothing of its own to check before descending into body/handlers
        elif isinstance(stmt, ast.ClassDef):
            for base in stmt.bases:
                own_names.visit(base)
            for keyword in stmt.keywords:
                own_names.visit(keyword.value)
        else:
            own_names.visit(stmt)

        unbound = own_names.names - bound - _ALWAYS_BOUND_NAMES
        for name in sorted(unbound):
            reasons.append(
                f"'{path}' line {stmt.lineno}: '{name}' is referenced before anything binds it in this "
                f"file (or it's never bound at all) -- this would raise NameError the instant the module "
                f"is imported, not just a style nit."
            )

        if isinstance(stmt, ast.If):
            # Unlike every other compound statement here, an if/else where
            # BOTH branches bind the same name is unconditionally safe to
            # propagate outward -- every real execution path binds it. Real,
            # common pattern found and fixed before this check was wired
            # in: `if x: report = a() else: report = b()` then `print(
            # report)` -- flagged as a false positive until this
            # intersection logic was added. A bare `if` with no `else`
            # stays fully conservative (nothing propagates), same as
            # every other compound statement below -- the "didn't enter
            # the block" path really might leave a name unbound.
            body_bound = set(bound)
            orelse_bound = set(bound)
            reasons.extend(_check_statement_sequence(stmt.body, body_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, orelse_bound, path))
            if stmt.orelse:
                guaranteed = (body_bound - bound) & (orelse_bound - bound)
                bound |= guaranteed
        elif isinstance(stmt, ast.While):
            reasons.extend(_check_statement_sequence(stmt.body, set(bound), path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            nested_bound = set(bound) | _assignment_target_names(stmt.target)
            reasons.extend(_check_statement_sequence(stmt.body, nested_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            nested_bound = set(bound)
            for item in stmt.items:
                if item.optional_vars is not None:
                    nested_bound |= _assignment_target_names(item.optional_vars)
            reasons.extend(_check_statement_sequence(stmt.body, nested_bound, path))
        elif isinstance(stmt, ast.Try):
            try_bound = set(bound)
            reasons.extend(_check_statement_sequence(stmt.body, try_bound, path))
            all_handlers_terminate = bool(stmt.handlers) and all(_handler_terminates(h) for h in stmt.handlers)
            for handler in stmt.handlers:
                handler_bound = set(bound)
                if handler.name:
                    handler_bound.add(handler.name)
                reasons.extend(_check_statement_sequence(handler.body, handler_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
            reasons.extend(_check_statement_sequence(stmt.finalbody, set(bound), path))
            if all_handlers_terminate:
                bound |= try_bound - bound
        elif isinstance(stmt, ast.ClassDef):
            # A class body executes immediately, top to bottom, just like a
            # module -- a method def'd earlier in the same class body is
            # legitimately bound for a later statement in that same body
            # (e.g. `visit_ListComp = _visit_comprehension` right after
            # `def _visit_comprehension(...):`). Real false positive found
            # in this very file before this branch was added. Only the
            # class NAME itself (handled by _names_bound_by_statement)
            # propagates outward -- attributes/methods stay class-local.
            reasons.extend(_check_statement_sequence(stmt.body, set(bound), path))

        bound |= _names_bound_by_statement(stmt)
    return reasons


def _check_module_level_name_order(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per real NameError-class bug: a name referenced
    before anything has bound it yet, purely from source order. Python
    executes a module's top-level statements top-to-bottom -- referencing a
    name before its binding statement has run is a real, 100%-reproducible
    NameError the instant the module is imported, even though
    ast.parse()/_check_syntax_valid() sees it as perfectly valid syntax
    (neither of those execute or resolve names, only parse grammar).

    Built after this exact failure shape was independently reproduced
    TWICE in one real held-out eval run (2026-08-01): a dict value calling
    a function imported on a later line, and a real `import time` line
    mangled into a bare `time` expression statement referencing a name
    never bound anywhere in the file at all. Neither is a syntax error, so
    neither was ever caught before this check existed.

    Deliberately static, never a real `import <module>` subprocess call:
    several of this repo's own modules (nova_query.py, ingest.py,
    graph_builder.py) construct a live Chroma HttpClient at module scope --
    actually importing an arbitrary touched file could make a real,
    network-dependent call as a side effect of a supposedly cheap,
    deterministic completion check. This only ever inspects the module's
    own AST, in source order, against names bound by everything earlier --
    it never executes anything.

    Known, accepted limitation: walrus-operator bindings (`if (n :=
    f()) > 0:`) aren't modeled as bindings at all, so a name bound only via
    `:=` and used afterward will be (incorrectly) flagged. Left unfixed
    deliberately -- verified zero real files in this repo use that pattern
    today (the same real-file sweep that shook out and fixed every other
    false positive here found none), so chasing full correctness for a
    pattern with no real evidence of use would be scope creep beyond what
    this check actually needs to earn its keep.
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
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue  # unreadable or a real syntax error -- _check_syntax_valid already reports that case

        reasons.extend(_check_statement_sequence(tree.body, set(), path))
    return reasons


class _EagerImportCollector(ast.NodeVisitor):
    """
    Collects the module names of every Import/ImportFrom statement that
    executes at true import time -- module top level, class bodies, and the
    body of if/while/try/for/with (all reached via the default
    generic_visit, since none of those are overridden below) -- but never
    descends into a FunctionDef/AsyncFunctionDef body, since a lazy import
    inside a function only ever runs at call time, well after the module has
    finished loading, and can never itself contribute to a real import-time
    cycle. This is exactly the pattern this repo's own code already uses in
    a couple of spots to break a potential cycle on purpose (e.g. nova_log.
    _read_benchmark_entries()'s local import) -- deliberately not flagged.
    """

    def __init__(self) -> None:
        self.modules: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            self.modules.add(node.module)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # lazy import inside a function body can't form a real import-time cycle

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass  # a lambda body can't contain an import statement anyway, but stay consistent


def _eager_imports(source: str) -> set[str]:
    """
    Real module names a source file imports at true import time only -- see
    _EagerImportCollector's own docstring for exactly what "eager" means
    here. Returns bare module names (e.g. "nova_benchmark", "os"), not
    individual imported symbols -- only which OTHER MODULES this one
    depends on at load time matters for cycle detection, not what it
    imports from them. Relative imports (`from . import x`) are skipped --
    this repo is a flat module layout with no packages to resolve them
    against.
    """
    tree = ast.parse(source)
    collector = _EagerImportCollector()
    collector.visit(tree)
    return collector.modules


def _find_import_cycle(current: str, target: str, root: str, visited: set[str]) -> list[str] | None:
    """
    DFS following only eager (import-time) imports outward from `current`,
    looking for a path that leads back to `target`. Returns the real chain
    the moment one is found (e.g. ["nova_benchmark", "nova_query",
    "nova_log"] when called with current="nova_benchmark",
    target="nova_log"), or None if no such path exists.

    `visited` accumulates every module name already explored in this one
    DFS (mutated in place across recursive calls) so a module reachable via
    more than one branch is only ever read and expanded once -- it is not a
    record of the current path, which is instead reconstructed on the way
    back up the recursion via each call's own return value.

    Reads each candidate module's source directly from `root` and stops
    following a name the moment there's no corresponding <name>.py on disk
    -- stdlib/third-party imports can't recurse back to a local file, so
    there's nothing further to explore down that branch. Deliberately
    static, same reasoning as _check_module_level_name_order's own
    docstring: never a real `import <module>` call, since several of this
    repo's own modules construct a live Chroma HttpClient at module scope.
    """
    if current in visited:
        return None
    visited.add(current)

    module_path = os.path.join(root, f"{current}.py")
    if not os.path.isfile(module_path):
        return None  # stdlib/third-party -- can't recurse back to a local file

    try:
        source = open(module_path, encoding="utf-8").read()
        eager = _eager_imports(source)
    except (OSError, SyntaxError):
        return None

    for imported in sorted(eager):
        if imported == target:
            return [current, target]
        chain = _find_import_cycle(imported, target, root, visited)
        if chain is not None:
            return [current] + chain
    return None


def _check_module_level_circular_imports(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per touched .py file whose current (post-edit)
    top-level imports form a genuine import-time cycle back to itself --
    e.g. nova_log importing nova_benchmark, which imports nova_query, which
    imports back from nova_log. Every module in the chain reports valid
    Python (ast.parse() succeeds) and every individual name is bound in
    correct source order (_check_module_level_name_order sees nothing
    wrong) -- this is a distinct defect class neither existing check can
    see, since it only becomes visible once you follow imports ACROSS file
    boundaries rather than within one file.

    Built after this exact failure shape was found in a real held-out eval
    (2026-08-02, task 6, Nova Log Benchmark view): the model added a
    top-level `from nova_benchmark import BENCHMARK_LOG_PATH` to
    nova_log.py, forming exactly this cycle -- and the gate false-passed it,
    since neither check above has any cross-module concept. Claude's
    original solution for the same task used a local (inside-function)
    import specifically to avoid this cycle.

    Only flags a cycle where every edge is eager (see _eager_imports /
    _find_import_cycle) -- a cycle broken by even one local import anywhere
    in the chain is a real, standard, safe pattern already used elsewhere in
    this repo, not a bug. Reports at most one chain per touched file, to
    avoid pile-on noise if multiple of its imports all happen to cycle back.
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        module_name = os.path.splitext(os.path.basename(path))[0]
        try:
            source = open(full_path, encoding="utf-8").read()
            eager = _eager_imports(source)
        except (OSError, SyntaxError):
            continue  # unreadable or a real syntax error -- _check_syntax_valid already reports that case

        for imported in sorted(eager):
            if imported == module_name:
                continue  # a direct self-import is a different (also broken) pattern -- not this check's concern
            chain = _find_import_cycle(imported, module_name, root, {module_name})
            if chain is not None:
                reasons.append(
                    f"'{path}' has a real module-level (eager) import cycle: "
                    f"{' -> '.join([module_name] + chain)} -- this would very likely crash with "
                    f'"ImportError: cannot import name ... from partially initialized module" '
                    f"the instant '{path}' is imported. Consider a local (inside-function) import "
                    f"to break the cycle, same pattern already used elsewhere in this repo (e.g. "
                    f"nova_log._read_benchmark_entries())."
                )
                break
    return reasons


def _eager_bound_names(statements: list[ast.stmt]) -> set[str]:
    """
    Every name bound at module level by this statement sequence, or by any
    nested if/while/for/with/try body inside it (all execute immediately,
    same "eager" scope concept as _EagerImportCollector) -- but never
    descends into a function/class body, since a name a class only binds on
    itself (an attribute or method) isn't a bare module-level name a `from
    module import X` statement could ever resolve to; only the class's own
    name (already returned by _names_bound_by_statement for the ClassDef
    statement itself) matters for that.

    Deliberately lenient, unlike _check_module_level_name_order's ordering
    logic: a name bound only inside one branch of a conditional still
    counts as "exists somewhere in this module." This check's job is
    catching a definitely-missing export (something no code path ever
    binds), not verifying a guaranteed one.
    """
    names: set[str] = set()
    for stmt in statements:
        names |= _names_bound_by_statement(stmt)
        if isinstance(stmt, (ast.If, ast.While)):
            names |= _eager_bound_names(stmt.body)
            names |= _eager_bound_names(stmt.orelse)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            names |= _eager_bound_names(stmt.body)
            names |= _eager_bound_names(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            names |= _eager_bound_names(stmt.body)
        elif isinstance(stmt, ast.Try):
            names |= _eager_bound_names(stmt.body)
            for handler in stmt.handlers:
                names |= _eager_bound_names(handler.body)
            names |= _eager_bound_names(stmt.orelse)
            names |= _eager_bound_names(stmt.finalbody)
    return names


def _local_module_file(root: str, dotted_module: str) -> str | None:
    """
    Real file path for a local dotted module name (e.g.
    "browser_hands.harness.cdp_connect" -> ".../browser_hands/harness/
    cdp_connect.py"), or None if no such file exists locally. Checks a
    flat <module>.py first (the common case in this repo), falling back to
    a package's <module>/__init__.py -- this repo has exactly one real
    nested package (browser_hands/, per CLAUDE.md), so both shapes need
    handling, not just the flat one the other cross-module checks assume.
    """
    parts = dotted_module.split(".")
    flat_path = os.path.join(root, *parts) + ".py"
    if os.path.isfile(flat_path):
        return flat_path
    init_path = os.path.join(root, *parts, "__init__.py")
    if os.path.isfile(init_path):
        return init_path
    return None


def _check_cross_module_missing_exports(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per touched .py file with a `from <local_module>
    import <name>` statement where <name> isn't actually bound anywhere in
    <local_module>'s CURRENT (post-edit) source -- a distinct failure class
    from both existing cross-module/single-file checks:
    _check_module_level_circular_imports catches an eager IMPORT CYCLE;
    _check_module_level_name_order only looks at ordering WITHIN one file.
    Neither can see "the thing being imported simply isn't there anymore."

    Built after a real held-out eval incident (2026-08-02, task 6, Nova Log
    Benchmark view): a rewritten nova_log.py stopped defining
    DEFAULT_RECENT_QUERIES_LIMIT at all, but nova_api.py's diff still
    imported it -- a real ImportError the instant nova_api.py loads,
    invisible to every other check since none of them verify an imported
    name actually exists in the module it claims to come from.

    Reads the target module's CURRENT worktree state, so a diff that
    touches both the importer and the exporter together is handled
    correctly (if the exporter's new version does define the name, nothing
    is flagged). Only resolves imports pointing at a real local module
    (_local_module_file returns a path) -- a third-party/stdlib import
    missing a name would be that package's own bug, not this repo's, and
    this check has no way to inspect an installed dependency's source
    anyway. Star imports and relative imports are skipped -- a star import
    can't be verified without executing the module, and this repo's flat
    layout doesn't use relative imports.
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
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue  # unreadable or a real syntax error -- _check_syntax_valid already reports that case

        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.ImportFrom):
                continue
            if not stmt.module or stmt.level != 0:
                continue  # relative import -- nothing to resolve against a flat layout
            target_path = _local_module_file(root, stmt.module)
            if target_path is None:
                continue  # not a local module -- stdlib/third-party, not this check's concern

            try:
                target_source = open(target_path, encoding="utf-8").read()
                target_tree = ast.parse(target_source)
            except (OSError, SyntaxError):
                continue  # target itself unreadable/broken -- _check_syntax_valid already reports that

            exported_names = _eager_bound_names(target_tree.body)
            for alias in stmt.names:
                if alias.name == "*":
                    continue  # star import -- can't verify without executing the module
                if alias.name not in exported_names:
                    reasons.append(
                        f"'{path}' imports '{alias.name}' from '{stmt.module}', but '{stmt.module}.py' "
                        f"doesn't define '{alias.name}' anywhere -- this would raise "
                        f"\"ImportError: cannot import name '{alias.name}' from '{stmt.module}'\" "
                        f"the instant '{path}' is imported."
                    )
    return reasons


def _name_referenced_elsewhere(tree: ast.AST, import_stmt: ast.stmt, name: str) -> bool:
    """
    True if `name` is Name-referenced (Load context) anywhere in `tree`
    outside `import_stmt` itself. Deliberately a whole-file, best-effort
    scan rather than anything scope-aware -- covers both a direct call
    (`run_coding_task(...)`) and attribute access off an `import os`-style
    binding (`os.path`, whose AST shape is an Attribute wrapping a
    Name(id="os")) with the same simple check.
    """
    for node in ast.walk(tree):
        if node is import_stmt:
            continue
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            return True
    return False


def _check_unused_new_imports(diff: str, root: str) -> list[str]:
    """
    Soft-flag warnings, one per newly ADDED import whose bound name is never
    referenced anywhere else in its file's current (post-edit) source.

    Built after a real false-pass (2026-08-02 held-out eval, task 3): the
    model's entire nova_query.py diff was a single new import line (`from
    nova_orchestrator import run_coding_task`) -- the actual integration the
    task's spec majority asked for was never written, and every hard-fail
    check in this module structurally couldn't see it, since the diff was
    non-empty, syntactically valid, correctly ordered, and the required file
    WAS touched.

    Deliberately a WARNING, not a hard fail -- this is a narrow, mechanical
    proxy for "the model may have started but not finished a piece of
    work," not a reliable signal on its own. It only catches the specific
    tell this incident left behind (a dead import), not the general case of
    a model writing a plausible-looking call that does nothing -- closing
    that fully would mean judging whether the diff's content fulfills the
    task's intent, exactly the LLM-judge-reading-a-trajectory pattern this
    whole module exists to avoid trusting (see this module's own header).

    Only checks imports that are themselves part of an ADDED line in the
    diff -- a pre-existing unused import elsewhere in the file is a
    style/legacy-code question, not evidence about this task.
    """
    added_lines = {
        line[1:].strip() for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    }
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        try:
            source = open(full_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue  # unreadable or a real syntax error -- _check_syntax_valid already reports that case

        for stmt in ast.walk(tree):
            if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            segment = ast.get_source_segment(source, stmt)
            if segment is None or segment.strip() not in added_lines:
                continue  # not a newly added import -- pre-existing, not this task's concern

            for bound_name in sorted(_names_bound_by_statement(stmt)):
                if bound_name == "*" or _name_referenced_elsewhere(tree, stmt, bound_name):
                    continue
                reasons.append(
                    f"'{path}' newly imports '{bound_name}' (line {stmt.lineno}) but never references it "
                    f"anywhere else in the file -- possible sign of unfinished work, not just dead code."
                )
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


# Soft-signal threshold for _check_unexpected_deletions() below -- unlike
# NARROW_SCOPE_CHANGE_RATIO_THRESHOLD above (a fraction of the file's own
# size, only applied to files the task explicitly named as narrow-scope),
# this applies to files the task's spec never mentioned at all, so there is
# no "original size" baseline to take a ratio against. An absolute line
# count is a rougher signal, which is acceptable here since this check is a
# warning for human/Claude review, not a hard fail -- occasional over- or
# under-flagging is a fine trade for a soft signal.
UNEXPECTED_DELETION_LINE_THRESHOLD = 20


def _check_unexpected_deletions(root: str, base_ref: str, requirements: dict) -> list[str]:
    """
    Soft-flag warnings for files that lost a large number of lines -- or
    were deleted outright -- without being named anywhere in the task's own
    spec (extract_task_requirements()'s required_files/narrow_scope_files).
    This is the "unexpected-deletion flag" from 86bb71x39's original check
    list, deliberately built as a warning rather than a hard fail (the
    ticket's own wording: "soft signal for human/Claude review, not
    auto-fail") -- a legitimate broad refactor can look mechanically
    identical to an accidental deletion, so this surfaces the fact rather
    than blocking on it.

    Distinct from _check_narrow_scope_not_exceeded() above: that check only
    fires for files the task explicitly marked for a small edit. A big
    deletion in a file the task never named at all -- required, narrow-
    scope, or otherwise -- is a different, more surprising shape that check
    doesn't cover.

    An outright deletion (the file existed at base_ref, is gone from the
    worktree now) is always flagged regardless of whether it was named --
    even an in-scope file being deleted entirely is worth a human glance.
    """
    stats = _diff_numstat(root, base_ref)
    named_files = {
        os.path.basename(f.strip())
        for f in requirements.get("required_files", []) + requirements.get("narrow_scope_files", [])
        if f.strip()
    }
    warnings = []
    for path, (_added, removed) in stats.items():
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            warnings.append(f"'{path}' was deleted outright ({removed} line(s) removed).")
            continue
        if os.path.basename(path) in named_files:
            continue  # task's own spec said this file could change -- not "unexpected"
        if removed >= UNEXPECTED_DELETION_LINE_THRESHOLD:
            warnings.append(
                f"'{path}' had {removed} line(s) removed but was never named in the task spec -- "
                f"verify this deletion was intended."
            )
    return warnings


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
        if not name:
            continue
        # A deliverable named like "send_notification()" is delivered as long
        # as the identifier itself shows up -- match the bare name so a
        # reflowed multi-line signature ("def send_notification(\n    title,
        # ...") still counts. Held-out eval 2026-09-02 caught this exact
        # false positive (86bbcfv9d).
        search_term = name[:-2] if name.endswith("()") else name
        if search_term not in added_text:
            warnings.append(f"'{name}' was named as a deliverable but never appears in the diff's added lines.")
    return warnings


# ── Entry point ────────────────────────────────────────────────


def _tag(check_name: str, messages: list[str]) -> list[str]:
    """
    Prefixes each message with which check produced it, e.g.
    "[syntax_valid] ...". Guard-firing attribution (Marvin's ask, 2026-08-02):
    without this, hard_fails/warnings is one flat, untagged list and nobody
    can tell which of the 6 checks below is actually catching real problems
    across re-runs without re-reading transcripts by hand. Deliberately a
    plain string prefix, not a schema change to hard_fails/warnings itself --
    every existing consumer (nova_coding_eval._format_gate_result(),
    nova_orchestrator._log_ground_truth_gate()) keeps working unchanged, and
    the tag is immediately readable in the generated eval report too, not
    just machine-parseable by nova_guard_stats.py.
    """
    return [f"[{check_name}] {m}" for m in messages]


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
        return {"passed": False, "hard_fails": _tag("nonzero_diff", [empty_diff_reason]), "warnings": []}

    if requirements is None:
        requirements = extract_task_requirements(task_description)

    hard_fails = []
    hard_fails.extend(_tag("syntax_valid", _check_syntax_valid(diff, root)))
    hard_fails.extend(_tag("powershell_syntax_valid", _check_powershell_syntax_valid(diff, root)))
    hard_fails.extend(_tag("lint_clean", _check_lint_clean(diff, root, base_ref)))
    hard_fails.extend(_tag("module_level_name_order", _check_module_level_name_order(diff, root)))
    hard_fails.extend(_tag("cross_module_circular_import", _check_module_level_circular_imports(diff, root)))
    hard_fails.extend(_tag("cross_module_missing_export", _check_cross_module_missing_exports(diff, root)))
    hard_fails.extend(
        _tag("required_files_touched", _check_required_files_touched(diff, requirements["required_files"], root))
    )
    hard_fails.extend(
        _tag("forbidden_paths_untouched", _check_forbidden_paths_untouched(diff, requirements["forbidden_files"]))
    )
    hard_fails.extend(
        _tag(
            "narrow_scope_not_exceeded",
            _check_narrow_scope_not_exceeded(root, base_ref, requirements["narrow_scope_files"]),
        )
    )
    warnings = _tag("deliverables_present", _check_deliverables_present(diff, requirements["deliverables"]))
    warnings.extend(_tag("unused_new_import", _check_unused_new_imports(diff, root)))
    warnings.extend(_tag("unexpected_deletion", _check_unexpected_deletions(root, base_ref, requirements)))

    return {"passed": not hard_fails, "hard_fails": hard_fails, "warnings": warnings}
