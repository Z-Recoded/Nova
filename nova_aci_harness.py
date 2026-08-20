# nova_aci_harness.py
# Real, live test harness driving one exercise from the vendored Exercism
# corpus (data/coding_specialist_eval/exercism_subset/) through
# Qwen2.5-Coder-7B via nova_coding_aci.py's constrained action-space
# interface (86bbch95y). Proves the whole pipeline works end-to-end before
# any container/production wiring -- the same "ship the working loop first,
# harden sandboxing later" precedent nova_orchestrator.py's own Claude-
# backed lane already established (CLAUDE.md: "v1 is driven by the Claude
# API... has no Docker/OpenHands sandboxing for the interactive lane --
# that's deferred"). Runs directly against a real temp directory today;
# docker/nova-aci-sandbox/ is the container this same logic would run
# inside once that hardening pass happens.
#
# Real gotcha found live (2026-08-15): Ollama's `tool_calls` response field
# came back None for qwen2.5-coder:7b even when given a real `tools=[...]`
# schema -- the model emitted its tool-call attempt as raw JSON text in
# `content` instead. Same shape nova_orchestrator_runpod.py already found
# for its own RunPod endpoint (no native tool-calling, a prompted
# <tools>...</tools> text format instead) -- this harness uses the
# identical proven pattern: a system prompt describing the call format,
# JSON parsed out of plain text, defensive against a markdown code fence
# wrapping it (the same gotcha nova_task_queue.propose_tier()/
# nova_completion_gate.extract_task_requirements() already found and
# handle).
#
# Usage:
#   python nova_aci_harness.py <exercise-slug>   # e.g. bob

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import anthropic
import ollama
from dotenv import load_dotenv

import nova_coding_aci as aci
from nova_orchestrator import NOVA_AGENT_MODEL
from nova_tools import _resolve_within_root

# Same .env-relative-to-script-location pattern every other Claude-API
# script in this repo uses -- only actually needed when --hybrid-verify is
# on (see Phase 5 section below), but loading is harmless either way.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_HOST = "http://127.0.0.1:11434"

# Real A/B result, 2026-08-19: client.chat() originally passed no options at
# all, so Ollama silently ran this model's real 32768-token context at its
# own default of 4096. Raised to 24576 (empirically confirmed 100% GPU-
# resident, unlike the full 32768 which forces ~12% CPU offload) and ran a
# real 120-run comparable batch against the existing 3-guard baseline --
# pass rate was flat (9/120 -> 10/120, noise), but every efficiency metric
# got measurably WORSE: avg turns/run 7.76->9.51, max_turns_reached
# 25.8%->38.3%, wall time +30%, and MORE guard fires (repeat_failed_call
# 45->71), not fewer -- the opposite of what the truncation hypothesis
# predicted. Reverted to 4096, kept explicit (not just Ollama's implicit
# default) so this real, tested conclusion is visible in code rather than
# silently relying on whatever Ollama's default happens to be. Second real
# data point (after project_coding_agent_context_budget_finding's 32B-backend
# result) that context scaling has hit diminishing/negative returns here --
# the residual gap looks like model capability, not a context-window bug.
OLLAMA_NUM_CTX = 4096

# Generous relative to a 6-difficulty-tier corpus of small exercises -- most
# should complete in well under this; a run that's still going at this point
# is treated as stuck, matching nova_orchestrator.py's own max-turns
# philosophy for the Claude-backed lane.
MAX_TURNS = 15

# Same "keep recent, condense older" call as nova_coding_aci.collapse_history()
# itself -- applied here, not inside the ACI module, since history
# management is the turn loop's concern, not an individual command's.
HISTORY_KEEP_RECENT = 8

# Real gotcha found live (2026-08-15, docs/aci-failure-mechanism-analysis.md): the `bob`
# exercise resent a byte-for-byte identical, already-rejected `edit` call 13 times in a row
# -- the model's logic was correct, but a single stray character made the code invalid
# Python, and it never adjusted after seeing the same syntax error thirteen times. Mirrors
# nova_orchestrator_runpod.py's own GUARD_REPEAT_FAILED_CALL, built for the identical real
# loop shape in that backend.
GUARD_REPEAT_FAILED_CALL = "repeat_failed_call"

# Real gotcha found live the same session: `zebra-puzzle` and `affine-cipher` both called
# `done` without ever successfully editing anything -- one gave up after two exploratory
# calls, the other quit after a single self-inflicted empty search overrode a filename it
# had already been given. Mirrors nova_orchestrator.py's own self_verify_nudge, which closes
# the same "accepted a stop with nothing actually verified" gap in the interactive lane.
GUARD_DONE_WITHOUT_EDIT = "done_without_edit"

# Deliberately bounded, not infinite -- refusing `done` forever just turns a quick give-up
# into a stuck loop, which is the OTHER failure mode this session found. Two real chances to
# reconsider, then let the model actually stop rather than force it to keep burning turns.
MAX_DONE_WITHOUT_EDIT_NUDGES = 2

# Real gap found live (2026-08-17) re-examining the first guarded corpus run: of 46 runs that
# still hit max_turns_reached, 22 had a GUARD_REPEAT_FAILED_CALL fire along the way -- the
# model never resent the IDENTICAL broken call twice, it just generated a new, differently
# wrong edit against the same spot and kept spinning (watched live in a fresh affine-cipher
# re-run: three different failed edits in a row, all circling the same couple of lines, before
# one finally landed). Exact-repeat detection can't see this. Mirrors
# nova_orchestrator_runpod.py's own file_replace fallback nudge -- that backend already learned
# (86bb728nj) that failures on the same PATH, not just identical calls, are the real recurring
# loop shape and need their own threshold, separate from the exact-repeat guard.
GUARD_SAME_PATH_REPEATED_FAILURE = "same_path_repeated_failure"
SAME_PATH_FAILURE_THRESHOLD = 3

# Nova Training Pipeline coding track, Phase 5 (86bbcfpd1): hybrid inference-time verification --
# real gap found live, 2026-08-20: `done` was accepted purely on has_successful_edit (any edit
# ever succeeded), with zero relationship to whether the solution is actually correct --
# _run_real_tests() only ran once, after the loop already exited, as a final scoring metric, never
# as a gate. Opt-in via --hybrid-verify (off by default, same pattern as --diff-format below) --
# this is the first thing in this file to spend real Anthropic API money, where every prior run
# has been $0 (Ollama only). Combines two verifier types before accepting `done`: execution-based
# (real _run_real_tests(), cheap and already proven -- a real test failure never reaches the
# generative call at all) and execution-free (one real Claude call, no `tools` argument -- a
# judge, never a writer, same structural guarantee nova_orchestrator._review_coding_diff()
# established for its reviewer role -- catching a gamed/hardcoded solution that can pass real
# tests and still be wrong in the way that matters). Per Eval Harness Initiative 4's standing
# guardrail, this verdict is used ONLY to gate this turn loop's own accept/nudge decision -- never
# logged anywhere nova_coding_dpo_filter.py (Phase 4) or any DPO/training pipeline reads from.
GUARD_HYBRID_VERIFY_REJECTED = "hybrid_verify_rejected"

# Same "don't refuse forever" precedent as MAX_DONE_WITHOUT_EDIT_NUDGES -- shared across both a
# real test failure and a real style CONCERNS verdict, whichever the gate hits first each time.
MAX_HYBRID_VERIFY_NUDGES = 2

CORPUS_ROOT = Path(__file__).parent / "data" / "coding_specialist_eval" / "exercism_subset"

TEST_TIMEOUT_SECONDS = 30

# Real per-exercise telemetry, one JSON line per run -- same JSONL-append
# convention as agent_log.jsonl/coding_review_log.jsonl. logs/ is
# gitignored, matching every other real telemetry file in this project.
RESULTS_LOG_PATH = Path(__file__).parent / "logs" / "aci_harness_log.jsonl"

SYSTEM_PROMPT = """You are editing Python code inside a working directory to complete a task.
You interact with the directory ONLY through the following commands -- you have no direct
shell or filesystem access. Respond with EXACTLY ONE JSON object per turn, no other text,
in this shape:

{"tool": "<name>", "arguments": {...}}

Available tools:
- find_file: {"pattern": "<substring>"} -- find files whose name contains pattern
- search_file: {"path": "<relative path>", "pattern": "<substring>"} -- search one file for matching lines
- search_dir: {"pattern": "<substring>"} -- search every .py file for matching lines
- view: {"path": "<relative path>", "start_line": <int, optional>, "window": <int, optional>} --
  view a windowed, line-numbered slice of a file
- edit: {"path": "<relative path>", "start_line": <int>, "end_line": <int>, "new_content": "<text>"} --
  replace lines [start_line, end_line] (inclusive) with new_content
- done: {} -- call this when you believe the task is complete

Your entire response must be valid JSON, on one line if possible. new_content must be a
normal JSON string -- use \\n for newlines and \\" for embedded quotes. Never use Python's
triple-quote (\"\"\"...\"\"\") syntax inside a JSON value; it is not valid JSON and will fail
to parse. Example of a real, valid edit call replacing one line:

{"tool": "edit", "arguments": {"path": "f.py", "start_line": 2, "end_line": 2, "new_content": "    return 1\\n"}}

Always view a file before editing it, so your line numbers are correct. Work directly on the
file the task names, and only that file, unless told otherwise. Do not edit the test file."""

# 86bbch988's diff-format variant -- identical to SYSTEM_PROMPT except for the edit tool's own
# argument shape. Real motivation: Aider's own published benchmark found unified-diff cut GPT-4
# Turbo's lazy-completion rate 3x over search/replace-style edits (20%->61% on Aider's own
# benchmark) -- the current SYSTEM_PROMPT's explicit-line-number format is functionally closer
# to the weaker search/replace family (exact positional matching), not the stronger diff family.
# edit() itself needed zero changes for this -- it was already explicitly format-agnostic
# (nova_coding_aci.edit()'s own docstring); _resolve_diff_hunk() below is the new piece,
# converting a diff hunk into the same (start_line, end_line, new_content) triple edit() always
# took. No @@ hunk header or a/b file paths -- both are redundant with the edit call's own
# "path" argument, and 86bbch988's real open risk is hunk-to-file resolution, not header parsing.
#
# REAL RESULT, 2026-08-19 -- closed, negative, do not use by default: a real 120-run corpus
# batch (--diff-format, same 4096 ctx as baseline) came back a clear regression, not noise --
# pass rate 7.5%->4.2%, avg turns/run 7.76->13.53, max_turns_reached 25.8%->82.5%,
# repeat_failed_call guard fires 45->360 (8x). Root cause, confirmed live via a verbose bob run:
# the model made a real whitespace/indentation mistake reproducing an unchanged line in its diff
# hunk, got correctly rejected (no fuzzy matching, by design), then couldn't self-correct and
# got stuck resending the identical broken hunk. Aider's number was measured on GPT-4 Turbo,
# where diff format's real benefit is stopping LAZY full-file rewrites ("// rest unchanged") --
# a large-model failure mode. This model's actual bottleneck is different: not laziness, exact
# content reproduction -- and the line-range format sidesteps that specific weakness entirely
# (the model never retypes existing code, only supplies new content at a line number view()
# already gave it). Diff format made this model's real weak spot load-bearing instead of routing
# around it. Left in the codebase as a real, tested reference (kept off by default, opt-in via
# --diff-format only) -- not deleted, matching how nova_orchestrator_runpod.py/_devstral.py
# stayed in the codebase after their own dense-model backends were deprioritized.
SYSTEM_PROMPT_DIFF = """You are editing Python code inside a working directory to complete a task.
You interact with the directory ONLY through the following commands -- you have no direct
shell or filesystem access. Respond with EXACTLY ONE JSON object per turn, no other text,
in this shape:

{"tool": "<name>", "arguments": {...}}

Available tools:
- find_file: {"pattern": "<substring>"} -- find files whose name contains pattern
- search_file: {"path": "<relative path>", "pattern": "<substring>"} -- search one file for matching lines
- search_dir: {"pattern": "<substring>"} -- search every .py file for matching lines
- view: {"path": "<relative path>", "start_line": <int, optional>, "window": <int, optional>} --
  view a windowed, line-numbered slice of a file
- edit: {"path": "<relative path>", "diff": "<diff hunk>"} -- apply a diff hunk to the file.
  The diff hunk is one or more lines, each starting with exactly one of these three characters:
    ' ' (a space) -- an EXISTING line, unchanged, given as context so the edit location can be found
    '-' -- an EXISTING line to remove
    '+' -- a NEW line to add
  Every space-prefixed and '-'-prefixed line must match the file's real current content EXACTLY,
  including whitespace -- no fuzzy matching is done. Include at least one unchanged (space-prefixed)
  line of real context so the location is unambiguous. Do not include line numbers or @@ headers.
- done: {} -- call this when you believe the task is complete

Your entire response must be valid JSON, on one line if possible. The diff value must be a
normal JSON string -- use \\n for newlines and \\" for embedded quotes. Never use Python's
triple-quote (\"\"\"...\"\"\") syntax inside a JSON value; it is not valid JSON and will fail
to parse. Example of a real, valid edit call:

{"tool": "edit", "arguments": {"path": "f.py", "diff": " def response(hey_bob):\\n-    pass\\n+    return \\"Sure.\\""}}

Always view a file before editing it, so your context lines are correct. Work directly on the
file the task names, and only that file, unless told otherwise. Do not edit the test file."""


def _strip_code_fence(raw: str) -> str:
    """Same markdown-fence gotcha nova_task_queue.propose_tier() already found live -- strip it before parsing."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()


def _repair_unterminated_string(raw: str) -> str | None:
    """
    Real repair heuristic, added 2026-08-15 after direct evidence: across a
    full real run, the model's edit calls were consistently CORRECT,
    complete Python logic, undone by one single missing closing quote right
    before the call's trailing braces -- e.g. `..."Whatever."}}` where the
    new_content value opened with a quote that was never closed. Verified
    against all 15 real turns from that run: this exact heuristic
    (try appending each quote character right before the trailing `}` run,
    re-parse) recovered 14 of 15 -- the model's actual reasoning was right
    almost every time; only the closing punctuation was missing.

    This is deliberately a repair of the TEXT, not a guess at INTENT --
    it only tries appending a real quote character in the one place a
    genuinely truncated string would need one, then hands the result to
    ast.literal_eval() to confirm it's now valid; it never invents content.
    Returns the repaired string, or None if no repair makes it parseable.
    """
    stripped = raw.rstrip()
    if not stripped.endswith("}"):
        return None
    trailing_brace_count = len(stripped) - len(stripped.rstrip("}"))
    body = stripped[: len(stripped) - trailing_brace_count]
    for quote_char in ("'", '"'):
        candidate = body + quote_char + "}" * trailing_brace_count
        try:
            ast.literal_eval(candidate)
            return candidate
        except (ValueError, SyntaxError):
            continue
    return None


def _try_parse_raw(raw: str) -> tuple[dict | None, str]:
    """
    Three graduated attempts to turn the model's raw text into a real
    Python dict, cheapest and strictest first -- the answer to "what if
    parsing didn't care about the model's exact format, only that it can be
    turned into something valid without losing meaning": (1) strict JSON,
    the fast path when the model gets it exactly right; (2) ast.literal_eval,
    which safely accepts Python literal syntax (single/triple-quoted
    strings) that isn't valid JSON but IS valid, complete Python -- covers
    the model's real, repeated bias toward Python string conventions over
    strict JSON; (3) _repair_unterminated_string, for the specific real
    truncation pattern found live. ast.literal_eval only evaluates literal
    expressions (dicts/strings/numbers/etc.) -- unlike eval(), it cannot
    execute arbitrary code, so this stays safe against untrusted model
    output even while being lenient about its exact syntax.

    Returns (parsed_dict_or_None, method) where method is "json", "python",
    or "repaired" on success -- surfaced so a real run can see which tier
    actually did the work, not just that parsing succeeded. On total
    failure, method carries the real json.JSONDecodeError text instead (the
    most informative of the three failures, since strict JSON is the
    documented contract) so the caller doesn't need to re-parse a second
    time just to build an error message.
    """
    try:
        return json.loads(raw), "json"
    except json.JSONDecodeError as e:
        json_error = str(e)
    try:
        return ast.literal_eval(raw), "python"
    except (ValueError, SyntaxError):
        pass
    repaired = _repair_unterminated_string(raw)
    if repaired is not None:
        return ast.literal_eval(repaired), "repaired"
    return None, json_error


def _parse_tool_call(raw_content: str) -> tuple[dict | None, str | None]:
    """
    Parses one tool call out of the model's plain-text response. Returns
    (call, None) on success or (None, reason) on failure -- the reason is
    fed back to the model as a specific correction, not a generic "couldn't
    parse" message, so it has a real chance to self-correct rather than
    repeat the identical mistake.

    Real gotcha, found live (2026-08-15, first real harness run): despite
    the system prompt specifying {"tool": "<name>", "arguments": {...}},
    the model consistently emitted a FLAT shape instead --
    {"tool": "view", "path": "bob.py"} with no "arguments" key at all.
    Normalized here rather than trusting the prompt to fix it: any
    top-level key other than "tool"/"arguments" is folded into the
    arguments dict, so both shapes work. See _try_parse_raw()'s own
    docstring for the (json/python/repaired) graduated parsing this
    function now sits on top of.
    """
    raw = _strip_code_fence(raw_content)
    parsed, method = _try_parse_raw(raw)
    if parsed is None:
        return None, (
            f"Your response could not be parsed as JSON or as a Python literal: {method}. "
            "Respond with a single, COMPLETE tool call -- check that every string you open is "
            'also closed: {"tool": "<name>", "arguments": {...}}'
        )
    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None, 'Your response was parseable but had no "tool" key.'
    arguments = dict(parsed.get("arguments") or {})
    flat_extras = {k: v for k, v in parsed.items() if k not in ("tool", "arguments")}
    arguments.update(flat_extras)
    return {"tool": parsed["tool"], "arguments": arguments, "_parse_method": method}, None


def _call_key(call: dict) -> tuple:
    """
    A hashable identity for one tool call -- same shape as
    nova_orchestrator_runpod.py's own _call_key(), so an exact repeat (not
    just a repeat of the tool name) can be recognized. Argument values here
    are always strings/ints (view/edit/find_file/search_* never take
    nested structures), so sorted items are safely hashable.
    """
    return (call.get("tool"), tuple(sorted(call.get("arguments", {}).items())))


def _tool_result_failed(tool: str, result: str) -> bool:
    """
    Whether one real tool_result string represents a genuine failure worth
    refusing a repeat of -- not just an unhelpful-but-valid result. An
    empty find_file/search_dir list is a real answer (possibly to a bad
    query, per the affine-cipher transcript in
    docs/aci-failure-mechanism-analysis.md), not a mechanical failure, so
    it does NOT count here -- that is a judgment problem for the model,
    not something this guard should mask by refusing a legitimate retry
    with different arguments. Only two real failure shapes count: an
    edit() call the ACI itself rejected (accepted: false), and the
    plain-text "ERROR: ..." string _execute_tool() returns for a bad/
    missing argument or an unknown tool.
    """
    if tool == "edit":
        # Real bug found live (2026-08-19), fixed while adding diff-format support: an
        # "ERROR: ..." string (a missing argument, or _resolve_diff_hunk() rejecting an
        # unmatched/ambiguous diff) fell through json.loads()'s except clause and returned
        # False -- silently NOT a failure, so the repeat-failure guards could never catch a
        # model stuck resending the same broken diff hunk. Pre-existing gap on the original
        # line-range format too (a missing start_line/end_line had the same blind spot),
        # not something new to diff-format alone.
        if result.startswith("ERROR:"):
            return True
        try:
            return json.loads(result).get("accepted") is False
        except (json.JSONDecodeError, AttributeError):
            return False
    return result.startswith("ERROR:")


def _format_list_result(items: list) -> str:
    """
    JSON-encodes a find_file/search_file/search_dir result, with an
    explicit natural-language message on an empty result instead of a bare
    "[]" -- SWE-agent's own real pattern ("Your command ran successfully
    and did not produce any output"), ported here after the affine-cipher
    transcript (docs/aci-failure-mechanism-analysis.md) showed a model
    reading a bare empty list as decisive proof a file didn't exist, when
    it had actually just searched with the wrong pattern -- the file was
    already named correctly in the model's own first-turn prompt.
    """
    if not items:
        return (
            "No matches found. This command ran successfully -- an empty result is not an "
            "error and does not prove the file/pattern doesn't exist. Check the file list you "
            "were already given, or try a different search pattern, before concluding anything "
            "is missing."
        )
    return json.dumps(items)


def _resolve_diff_hunk(diff_text: str, path: str, root: str) -> tuple[int, int, str]:
    """
    Resolves a SYSTEM_PROMPT_DIFF-style hunk (space/-/+ prefixed lines) against
    the real current content of `path`, converting it into the same
    (start_line, end_line, new_content) triple aci.edit() always took --
    86bbch988's diff-format variant, built on the ACI's existing
    format-agnostic edit() rather than changing it.

    Matching is exact-text only, no fuzzy matching -- deliberately, so a
    silent near-match can never apply an edit in the wrong place. Raises
    ValueError with a specific, actionable message on any failure (same
    "give the model a real chance to self-correct" discipline as
    _parse_tool_call()); _execute_tool()'s existing except (ValueError, ...)
    handler turns that straight into the ERROR: string the model sees.
    """
    resolved = _resolve_within_root(path, root)
    original_lines = [line.rstrip("\n") for line in resolved.read_text(encoding="utf-8").splitlines(keepends=True)]

    search_lines: list[str] = []  # must match the file's real content, in order
    replacement_lines: list[str] = []  # what the matched range becomes
    for raw_line in diff_text.splitlines():
        marker, content = (raw_line[0], raw_line[1:]) if raw_line else (" ", "")
        if marker == " ":
            search_lines.append(content)
            replacement_lines.append(content)
        elif marker == "-":
            search_lines.append(content)
        elif marker == "+":
            replacement_lines.append(content)
        else:
            raise ValueError(
                f"Diff line {raw_line!r} doesn't start with a space (unchanged), '-' (remove), "
                "or '+' (add) -- every line in the diff must have one of those three prefixes."
            )

    if not search_lines:
        raise ValueError(
            "Diff has no context or removed lines to anchor the edit -- include at least one "
            "unchanged line (space-prefixed) or removed line ('-'-prefixed) so the exact "
            "location in the file can be found."
        )

    window = len(search_lines)
    matches = [i for i in range(len(original_lines) - window + 1) if original_lines[i : i + window] == search_lines]

    if not matches:
        raise ValueError(
            "Could not find these exact lines in the file -- view() the file again and copy "
            "the context/removed lines EXACTLY, including whitespace. No fuzzy matching is done."
        )
    if len(matches) > 1:
        raise ValueError(
            f"These lines match {len(matches)} different places in the file -- include more "
            "surrounding context lines so the edit location is unambiguous."
        )

    start_line = matches[0] + 1
    end_line = matches[0] + window
    new_content = "".join(line + "\n" for line in replacement_lines)
    return start_line, end_line, new_content


def _execute_tool(call: dict, root: str, diff_format: bool = False) -> str:
    """
    Runs one ACI command by name, returns a plain-text result string to
    feed back to the model. Unknown tool names / bad arguments come back as
    a plain error string rather than raising -- the model needs to see the
    mistake to correct it, not crash the whole run over one bad call.
    """
    tool = call.get("tool")
    args = call.get("arguments") or {}

    try:
        if tool == "find_file":
            return _format_list_result(aci.find_file(args["pattern"], root))
        if tool == "search_file":
            return _format_list_result(aci.search_file(args["path"], args["pattern"], root))
        if tool == "search_dir":
            return _format_list_result(aci.search_dir(args["pattern"], root))
        if tool == "view":
            return aci.view(
                args["path"], root, args.get("start_line", 1), args.get("window", aci.DEFAULT_VIEW_WINDOW_LINES)
            )
        if tool == "edit":
            if diff_format:
                start_line, end_line, new_content = _resolve_diff_hunk(args["diff"], args["path"], root)
                return json.dumps(aci.edit(args["path"], start_line, end_line, new_content, root))
            return json.dumps(aci.edit(args["path"], args["start_line"], args["end_line"], args["new_content"], root))
        if tool == "done":
            return "DONE"
        return f"ERROR: unknown tool '{tool}'. Valid tools: find_file, search_file, search_dir, view, edit, done."
    except KeyError as e:
        return f"ERROR: missing required argument {e} for tool '{tool}'."
    except (ValueError, OSError) as e:
        return f"ERROR: {e}"


def _prepare_working_copy(slug: str, tmp_dir: Path) -> Path:
    """
    Copies one real vendored exercise into a disposable temp directory,
    excluding .meta/ (the reference solution -- nova_coding_aci.py's own
    EXCLUDED_DIR_NAMES already keeps it out of find_file/search_dir results,
    but not copying it into the working directory at all is a second,
    independent layer -- it can't leak if it was never present).
    """
    source = CORPUS_ROOT / slug
    if not source.is_dir():
        raise RuntimeError(f"No vendored exercise '{slug}' at {source}")

    dest = tmp_dir / slug
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".meta"))
    return dest


def _read_task_description(working_copy: Path) -> str:
    """Real task text from the exercise's own .docs/instructions.md -- what the model is actually given."""
    instructions_path = working_copy / ".docs" / "instructions.md"
    return instructions_path.read_text(encoding="utf-8")


def _run_real_tests(working_copy: Path, slug: str) -> tuple[bool, str]:
    """
    Runs the exercise's REAL test file via stdlib unittest (no pytest
    dependency needed) from inside the working copy, so `from <slug> import
    ...` resolves correctly. This is the objective, runnable check that
    defines success -- never a judgment call by the model or a human
    reviewer, matching 86bbch9ak's own task-template principle.
    """
    module_name = slug.replace("-", "_")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", f"{module_name}_test", "-v"],
        cwd=str(working_copy),
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT_SECONDS,
    )
    return result.returncode == 0, result.stdout + result.stderr


def _generative_style_verifier(
    client: anthropic.Anthropic, task_description: str, solution_content: str
) -> tuple[str, str]:
    """
    Real execution-free half of Phase 5's hybrid verifier (86bbcfpd1) -- a
    generative judgment call, not a simple classifier, per Eval Harness
    Initiative 3's own explicit preference ("generative verifiers hold up
    much better on novel, out-of-domain failures"). No `tools` argument --
    a judge, never a writer. Returns (verdict, reason); reason is empty on
    ACCEPT.
    """
    system = (
        "You are reviewing a real solution to a small coding exercise for two specific things, "
        "not general code quality: (1) a gamed or hardcoded solution -- output values copied "
        "from the visible test cases rather than a genuine implementation of the described "
        "logic, and (2) clearly unidiomatic Python that a competent developer would not write. "
        "If neither applies, respond with exactly: ACCEPT. Otherwise respond with exactly: "
        "CONCERNS: <one sentence reason>."
    )
    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        max_tokens=200,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Task:\n{task_description}\n\nSolution:\n\n```python\n{solution_content}\n```",
            }
        ],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    verdict_text = text_blocks[0].strip() if text_blocks else "ACCEPT"
    if verdict_text.upper().startswith("ACCEPT"):
        return "ACCEPT", ""
    reason = verdict_text.split(":", 1)[1].strip() if ":" in verdict_text else verdict_text
    return "CONCERNS", reason


def _hybrid_verify_gate(
    client: anthropic.Anthropic, working_copy: Path, slug: str, task_description: str
) -> tuple[bool, str, bool]:
    """
    Phase 5's real hybrid gate (86bbcfpd1) -- runs right before a `done`
    call is accepted, when --hybrid-verify is on. Execution-based first
    (cheap, deterministic, the same _run_real_tests() the final scoring
    metric already uses): a real test failure never reaches the
    generative call at all, no reason to pay for a style opinion on code
    that doesn't work yet. Execution-free second, only once tests already
    pass. Returns (gate_passed, nudge_text, style_call_made) -- the caller
    tallies style_call_made into the real per-run cost count regardless
    of the verdict.
    """
    test_passed, test_output = _run_real_tests(working_copy, slug)
    if not test_passed:
        nudge = (
            "Your solution does not pass the real test suite yet. Real test output:\n\n"
            f"{test_output[:1500]}\n\nFix the real failures shown above before calling done again."
        )
        return False, nudge, False

    module_name = slug.replace("-", "_")
    solution_content = (working_copy / f"{module_name}.py").read_text(encoding="utf-8")
    verdict, reason = _generative_style_verifier(client, task_description, solution_content)
    if verdict == "ACCEPT":
        return True, "", True

    nudge = (
        f"Your solution passes the real tests, but a style review flagged a real concern: {reason} "
        "Address this before calling done again."
    )
    return False, nudge, True


def run_exercise(slug: str, verbose: bool = False, diff_format: bool = False, hybrid_verify: bool = False) -> dict:
    """
    Runs one real vendored exercise through Qwen2.5-Coder-7B via the ACI,
    end to end. The model never sees .meta/example.py (excluded before the
    working copy even exists) or is told a reference solution exists at
    all. Returns {"slug", "turns_used", "final_status", "test_passed",
    "test_output"}.

    `diff_format`: 86bbch988's edit-format A/B test -- SYSTEM_PROMPT_DIFF
    instead of SYSTEM_PROMPT, and edit() calls arrive as diff hunks resolved
    via _resolve_diff_hunk() instead of explicit line numbers. Logged on the
    result so a later comparison never needs the timestamp-cutoff trick the
    context-window A/B test needed.

    `hybrid_verify`: Phase 5's real hybrid gate (86bbcfpd1), off by
    default -- see the GUARD_HYBRID_VERIFY_REJECTED comment above. Only
    constructs a real Anthropic client (and requires ANTHROPIC_API_KEY)
    when True, so the default path stays exactly as it was before this
    flag existed -- no new dependency, no new cost.
    """
    client = ollama.Client(host=OLLAMA_HOST)
    anthropic_client = anthropic.Anthropic() if hybrid_verify else None

    with tempfile.TemporaryDirectory() as tmp:
        working_copy = _prepare_working_copy(slug, Path(tmp))
        root = str(working_copy)
        task_description = _read_task_description(working_copy)

        # Real gotcha found live (2026-08-15): .docs/instructions.md never
        # names the actual file to edit (bob's instructions talk about a
        # PERSON named Bob, never the file bob.py) -- without this, the
        # model spent its entire turn budget guessing plausible-sounding
        # wrong filenames ("task.py") and never adjusted after find_file
        # kept returning empty. A real production task would always name
        # its target file explicitly; this gives the model the same
        # starting information any real task would, rather than making it
        # guess blind.
        initial_files = aci.find_file(".py", root)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_DIFF if diff_format else SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Task:\n\n{task_description}\n\nFiles in your working directory: {initial_files}",
            },
        ]

        final_status = "max_turns_reached"
        turns_used = 0
        parse_method_counts = {"json": 0, "python": 0, "repaired": 0}
        parse_failures = 0

        # Guard state -- see GUARD_REPEAT_FAILED_CALL/GUARD_DONE_WITHOUT_EDIT/
        # GUARD_SAME_PATH_REPEATED_FAILURE/GUARD_HYBRID_VERIFY_REJECTED above.
        failed_calls: set = set()
        repeat_refusal_counts: dict = {}
        has_successful_edit = False
        done_without_edit_nudges = 0
        path_failure_counts: dict = {}
        # Real gap found live (2026-08-20): a single shared nudge budget let real test failures
        # alone exhaust it before the model ever reached a passing-tests state -- the generative
        # verifier never got a chance to participate in either of the first two real pilot runs
        # (style_verifier_calls stayed 0 both times). Separate counters/caps so a model that
        # struggles with tests first still gets its own real shot at the style check once it
        # clears them -- up to 4 real rejections total in the worst case, still well within
        # MAX_TURNS.
        test_fail_nudges = 0
        style_concern_nudges = 0
        style_verifier_calls = 0
        guard_fires = {
            GUARD_REPEAT_FAILED_CALL: 0,
            GUARD_DONE_WITHOUT_EDIT: 0,
            GUARD_SAME_PATH_REPEATED_FAILURE: 0,
            GUARD_HYBRID_VERIFY_REJECTED: 0,
        }

        for turn in range(1, MAX_TURNS + 1):
            turns_used = turn
            messages = [messages[0]] + aci.collapse_history(messages[1:], keep_recent=HISTORY_KEEP_RECENT)

            response = client.chat(model=OLLAMA_MODEL, messages=messages, options={"num_ctx": OLLAMA_NUM_CTX})
            raw_content = response["message"]["content"]
            messages.append({"role": "assistant", "content": raw_content})

            if verbose:
                print(f"\n--- turn {turn}: model said ---\n{raw_content}")

            call, parse_error = _parse_tool_call(raw_content)
            if call is None:
                parse_failures += 1
                messages.append({"role": "user", "content": f"ERROR: {parse_error}"})
                if verbose:
                    print(f"--- turn {turn}: parse failed ({parse_error}) ---")
                continue

            parse_method_counts[call.get("_parse_method", "json")] += 1
            tool = call.get("tool")

            if tool == "done":
                if not has_successful_edit:
                    if done_without_edit_nudges >= MAX_DONE_WITHOUT_EDIT_NUDGES:
                        final_status = "abandoned_after_nudge"
                        break
                    done_without_edit_nudges += 1
                    guard_fires[GUARD_DONE_WITHOUT_EDIT] += 1
                    nudge = (
                        "Refused: you have not made any successful edit yet, so nothing has "
                        "actually been attempted. View the target file and make a real edit "
                        f"before calling done again. ({done_without_edit_nudges}/{MAX_DONE_WITHOUT_EDIT_NUDGES} "
                        "warnings -- after this, done will be accepted as-is.)"
                    )
                    messages.append({"role": "user", "content": nudge})
                    if verbose:
                        print(f"--- turn {turn}: refused done (no successful edit yet) ---")
                    continue

                # has_successful_edit is True from here on -- Phase 5's real hybrid gate
                # (86bbcfpd1) only applies once there's something real to verify.
                if not hybrid_verify:
                    final_status = "completed"
                    break
                if test_fail_nudges >= MAX_HYBRID_VERIFY_NUDGES and style_concern_nudges >= MAX_HYBRID_VERIFY_NUDGES:
                    final_status = "completed"
                    break

                gate_passed, gate_nudge, style_call_made = _hybrid_verify_gate(
                    anthropic_client, working_copy, slug, task_description
                )
                if style_call_made:
                    style_verifier_calls += 1
                if gate_passed:
                    final_status = "completed"
                    break

                if style_call_made:
                    if style_concern_nudges >= MAX_HYBRID_VERIFY_NUDGES:
                        final_status = "completed"
                        break
                    style_concern_nudges += 1
                else:
                    if test_fail_nudges >= MAX_HYBRID_VERIFY_NUDGES:
                        final_status = "completed"
                        break
                    test_fail_nudges += 1

                guard_fires[GUARD_HYBRID_VERIFY_REJECTED] += 1
                messages.append({"role": "user", "content": gate_nudge})
                if verbose:
                    print(f"--- turn {turn}: hybrid-verify gate rejected done ---")
                continue

            key = _call_key(call)
            if key in failed_calls:
                repeat_refusal_counts[key] = repeat_refusal_counts.get(key, 0) + 1
                guard_fires[GUARD_REPEAT_FAILED_CALL] += 1
                refusal = (
                    f"Refused: you already tried this exact {tool} call and it failed. "
                    "Repeating it verbatim will fail again for the same reason -- take a "
                    "genuinely different next step."
                )
                if repeat_refusal_counts[key] >= 2:
                    refusal += (
                        " This is the same call again after already being refused once. "
                        "Re-view the file to see its real current content before editing "
                        "it, and change something concrete this time, not just resend the "
                        "same arguments."
                    )
                messages.append({"role": "user", "content": refusal})
                if verbose:
                    print(f"--- turn {turn}: refused repeat of already-failed {tool} call ---")
                continue

            tool_result = _execute_tool(call, root, diff_format=diff_format)
            if tool == "edit" and _tool_result_failed(tool, tool_result):
                failed_calls.add(key)
                path = call.get("arguments", {}).get("path", "")
                path_failure_counts[path] = path_failure_counts.get(path, 0) + 1
                if path_failure_counts[path] >= SAME_PATH_FAILURE_THRESHOLD:
                    guard_fires[GUARD_SAME_PATH_REPEATED_FAILURE] += 1
                    tool_result += (
                        f"\n\nNOTE: that's {path_failure_counts[path]} failed edits now on "
                        f"'{path}' -- not necessarily the same call each time, but you keep "
                        "failing in the same place. Stop guessing at small variations. Use view "
                        "to re-read the file's real current content in full before your next "
                        "edit, and think through the change before submitting it."
                    )
            elif _tool_result_failed(tool, tool_result):
                failed_calls.add(key)
            elif tool == "edit":
                has_successful_edit = True
            messages.append({"role": "user", "content": tool_result})
            if verbose:
                method = call.get("_parse_method", "?")
                print(f"--- turn {turn}: ran {tool} (parsed via {method}) -> {tool_result[:300]}")

        test_passed, test_output = _run_real_tests(working_copy, slug)

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "slug": slug,
            "diff_format": diff_format,
            "turns_used": turns_used,
            "final_status": final_status,
            "test_passed": test_passed,
            "test_output": test_output,
            "parse_method_counts": parse_method_counts,
            "parse_failures": parse_failures,
            "guard_fires": guard_fires,
            "hybrid_verify_enabled": hybrid_verify,
            "style_verifier_calls": style_verifier_calls,
            "test_fail_nudges": test_fail_nudges,
            "style_concern_nudges": style_concern_nudges,
        }
        _log_result(result)
        return result


def _log_result(result: dict) -> None:
    """
    Appends one real run's result to RESULTS_LOG_PATH. test_output is
    dropped from the logged copy -- verbose, not needed for analysis.
    """
    RESULTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {k: v for k, v in result.items() if k != "test_output"}
    with open(RESULTS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_all_exercises(
    verbose: bool = False, repeats: int = 1, diff_format: bool = False, hybrid_verify: bool = False
) -> list[dict]:
    """
    Runs every real vendored exercise under CORPUS_ROOT through
    run_exercise(), `repeats` times each, in slug-sorted order (not
    difficulty order -- a fixed, reproducible order across runs matters
    more here than grouping by difficulty, which SELECTED_EXERCISES in
    nova_pull_exercism_corpus.py already records separately for anyone
    cross-referencing results by tier). NOTICE.md (the corpus's own
    attribution file, not an exercise) is skipped.

    Real motivation for `repeats` (2026-08-15): a single-pass run showed
    the exact same exercise (bob) pass 26/26 once and fail completely the
    next time, with identical code -- Ollama's default sampling is not
    deterministic, so any single pass/fail per exercise is a noisy
    one-sample estimate, not a trustworthy result. Repeating turns each
    exercise's outcome into a real rate (nova_aci_stats.py computes this
    from the accumulated log) instead of a coin-flip.
    """
    slugs = sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir())
    results = []
    total_runs = len(slugs) * repeats
    run_number = 0
    for slug in slugs:
        for rep in range(1, repeats + 1):
            run_number += 1
            print(f"\n[{run_number}/{total_runs}] Running {slug} (rep {rep}/{repeats})...")
            result = run_exercise(slug, verbose=verbose, diff_format=diff_format, hybrid_verify=hybrid_verify)
            status = "PASS" if result["test_passed"] else "FAIL"
            print(f"  -> {status} ({result['final_status']}, {result['turns_used']} turn(s))")
            results.append(result)
    return results


def _print_summary(results: list[dict]) -> None:
    """
    Real aggregate numbers from a run_all_exercises() batch -- per-exercise
    pass RATE (not just a single pass/fail) when repeats > 1, plus overall
    totals and parse-method breakdown.
    """
    total = len(results)
    passed = sum(1 for r in results if r["test_passed"])
    totals = {"json": 0, "python": 0, "repaired": 0}
    total_parse_failures = 0
    guard_totals = {
        GUARD_REPEAT_FAILED_CALL: 0,
        GUARD_DONE_WITHOUT_EDIT: 0,
        GUARD_SAME_PATH_REPEATED_FAILURE: 0,
        GUARD_HYBRID_VERIFY_REJECTED: 0,
    }
    status_totals: dict[str, int] = {}
    total_style_verifier_calls = 0
    for r in results:
        for method, count in r["parse_method_counts"].items():
            totals[method] += count
        total_parse_failures += r["parse_failures"]
        for guard, count in r.get("guard_fires", {}).items():
            guard_totals[guard] = guard_totals.get(guard, 0) + count
        status_totals[r["final_status"]] = status_totals.get(r["final_status"], 0) + 1
        total_style_verifier_calls += r.get("style_verifier_calls", 0)

    print(f"\n=== Summary: {passed}/{total} runs passed ===")

    by_slug: dict[str, list[dict]] = {}
    for r in results:
        by_slug.setdefault(r["slug"], []).append(r)
    for slug in sorted(by_slug):
        runs = by_slug[slug]
        slug_passed = sum(1 for r in runs if r["test_passed"])
        print(f"  {slug:<24} {slug_passed}/{len(runs)} passed")

    print("\nParse method totals across all successful tool calls:")
    print(f"  json (strict, first try): {totals['json']}")
    print(f"  python (ast.literal_eval fallback): {totals['python']}")
    print(f"  repaired (targeted fix): {totals['repaired']}")
    print(f"  total parse failures (no tier recovered it): {total_parse_failures}")

    print("\nFinal status breakdown:")
    for status in sorted(status_totals, key=lambda s: -status_totals[s]):
        print(f"  {status:<24} {status_totals[status]}")

    print("\nGuard fire totals (docs/aci-failure-mechanism-analysis.md):")
    print(f"  {GUARD_REPEAT_FAILED_CALL}: {guard_totals[GUARD_REPEAT_FAILED_CALL]}")
    print(f"  {GUARD_DONE_WITHOUT_EDIT}: {guard_totals[GUARD_DONE_WITHOUT_EDIT]}")
    print(f"  {GUARD_SAME_PATH_REPEATED_FAILURE}: {guard_totals[GUARD_SAME_PATH_REPEATED_FAILURE]}")
    print(f"  {GUARD_HYBRID_VERIFY_REJECTED}: {guard_totals[GUARD_HYBRID_VERIFY_REJECTED]}")

    if total_style_verifier_calls:
        print(f"\nReal style-verifier (Claude) calls this batch: {total_style_verifier_calls}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Run one or all vendored exercises through Qwen2.5-Coder-7B via the ACI."
    )
    parser.add_argument(
        "slug",
        nargs="?",
        help="Exercise slug, e.g. 'bob' (must exist under the vendored exercise corpus). Omit with --all.",
    )
    parser.add_argument("--all", action="store_true", help="Run every vendored exercise, not just one.")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="With --all: run each exercise N times, for a real pass rate instead of one noisy sample.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print each turn's raw model output and tool result.")
    parser.add_argument(
        "--diff-format",
        action="store_true",
        help="86bbch988 A/B test: use SYSTEM_PROMPT_DIFF (unified-diff-style edits) instead of explicit line numbers.",
    )
    parser.add_argument(
        "--hybrid-verify",
        action="store_true",
        help=(
            "86bbcfpd1 (Phase 5): gate `done` on real test execution + a real generative style "
            "verifier before accepting it. Off by default -- this is the first thing in this "
            "file that spends real Anthropic API money, requires ANTHROPIC_API_KEY."
        ),
    )
    args = parser.parse_args()

    if args.all:
        results = run_all_exercises(
            verbose=args.verbose, repeats=args.repeat, diff_format=args.diff_format, hybrid_verify=args.hybrid_verify
        )
        _print_summary(results)
    elif args.slug:
        result = run_exercise(
            args.slug, verbose=args.verbose, diff_format=args.diff_format, hybrid_verify=args.hybrid_verify
        )
        print(f"\n=== {result['slug']} ===")
        print(f"Status: {result['final_status']} ({result['turns_used']} turn(s) used)")
        print(f"Tests passed: {result['test_passed']}")
        print(f"Guard fires: {result['guard_fires']}")
        if result["hybrid_verify_enabled"]:
            print(f"Style-verifier (Claude) calls: {result['style_verifier_calls']}")
        print(f"\n--- Test output ---\n{result['test_output']}")
    else:
        parser.error("Provide a slug or use --all.")
