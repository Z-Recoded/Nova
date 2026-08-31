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
#
# DEMOTED TO OPT-IN 2026-08-30 (86bbcfv9d Eval Harness Initiative 2). Individual ablation
# across two batches (n=240/condition combined) showed this guard is net-NEGATIVE on
# turn-efficiency: removing it improved avg turns ~-0.6 and max_turns_reached ~-7pts with no
# pass-rate cost, while it fires on ~40% of runs. The 2026-08-17 "it helped" result was
# likely a misattribution -- the empty-result search feedback (_format_list_result()) shipped
# in the same batch and targets the same failures. Its nudge ("re-read the file in full")
# costs an extra view turn, and GUARD_REPEAT_FAILED_CALL already covers the exact-repeat case.
# Kept in the repo as a tested reference, off by default -- enable with --same-path-guard.
# See docs/aci-guard-cluster-ablation.md.
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

# No ClickUp ticket filed (built ad hoc, 2026-08-24) -- Sparse Vector Technique-inspired
# early-abandon threshold. Comments here previously mislabeled this "86bbkru66 follow-up";
# that ID is a real, unrelated ticket (multi-agent scaling limits) -- corrected 2026-08-26.
# Real collapse pattern found live in the max-hybrid-nudges probe -- some runs burn their
# entire turn budget while the real test-pass fraction gets WORSE, not better (bob:
# 25/26 -> 0/26 passing across repeated `done` attempts). Rather than a fixed turn cap
# discovering this only after the fact, --early-abandon tracks the best pass fraction seen
# and stops the run once it goes EARLY_ABANDON_PATIENCE consecutive hybrid-verify checks
# without a real improvement -- the same "only act once a cheap noisy signal crosses a
# threshold" shape SVT uses to avoid spending a scarce budget on uninformative queries,
# applied here to turn budget instead of privacy budget. No artificial noise added (no
# privacy guarantee needed here) -- this is SVT-inspired sparse thresholding, not literal SVT.
EARLY_ABANDON_PATIENCE = 2

# Real bug found live 2026-08-23 (86bbk09da), spot-checking 86bbjzguh's progress-framing
# transcripts: _extract_first_json_object() only ever isolates the FIRST {...} block in a
# turn's raw text -- a model that emits two tool calls back-to-back with no narration
# between them (e.g. `{"tool": "view", ...} {"tool": "edit", ...}`) has the second one
# silently vanish. Never executed, never logged as failed, invisible to every other guard.
# Confirmed live in a real scrabble-score --progress-framing transcript where this happened
# on 12 of 15 turns (turns 3-7's real incremental edits and turns 8-15's `done` calls all
# discarded), and via a 9-run/86-turn negative-control baseline batch (no progress-framing)
# where it never happened once. This guard makes the drop visible and countable instead of
# silent -- it does NOT silently execute the extra call, since SYSTEM_PROMPT's own contract
# is exactly one tool call per turn and every historical result assumes that protocol held.
GUARD_MULTIPLE_CALLS_IGNORED = "multiple_calls_ignored"

MULTIPLE_CALLS_NUDGE = (
    "\n\nNOTE: your response contained more than one tool call -- only the first one above "
    "actually ran; anything after it was ignored. Respond with EXACTLY ONE JSON tool call "
    "per turn. If you meant to also do something else, make that your next call."
)

# 86bbcfv9d (Eval Harness Initiative 2, "audit existing gates individually"): the always-on
# turn-loop guards, each individually suppressible via run_exercise(disabled_guards=...)
# / --disable-guard NAME. When a guard is in the disabled set, its EFFECT is skipped (the loop
# reverts to pre-guard behaviour at that point) but a would-have-fired count is still recorded
# in the result's `guards_suppressed`, so an ablation batch sees both the pass-rate/turn delta
# AND how often the guard actually engaged. --hybrid-verify / --early-abandon / --regression-guard
# are separate axes with their own flags and are not part of this set. GUARD_SAME_PATH_REPEATED_FAILURE
# was demoted from this set to opt-in (--same-path-guard) on 2026-08-30 after its ablation came
# back net-negative -- see its constant comment above.
ABLATABLE_GUARDS = frozenset(
    {
        GUARD_REPEAT_FAILED_CALL,
        GUARD_DONE_WITHOUT_EDIT,
        GUARD_MULTIPLE_CALLS_IGNORED,
    }
)

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


def _repair_missing_closing_braces(raw: str) -> str | None:
    """
    Real repair heuristic, added 2026-08-22 after direct evidence from the
    86bbhckr3 checkpoint comparison (SWE-Dev-7B, SWE-Gym's openhands-lm-7b
    checkpoint): unlike bob's missing-quote pattern above, these models'
    edit calls were syntactically complete -- correct Python logic, the
    inner "arguments" object properly closed -- but generation stopped one
    token before the OUTER {"tool": ..., "arguments": {...}} wrapper got
    its own closing brace. Verified against a real live turn
    (nova_aci_harness.py --verbose, affine-cipher, SWE-Dev-7B): appending
    exactly one more "}" turned an unparseable response into a valid,
    complete tool call with the model's content completely unchanged.

    Tries appending 1, then 2, extra closing braces -- this protocol nests
    at most two levels ({"tool": ..., "arguments": {...}}), so more than
    two missing braces would mean something beyond punctuation is wrong --
    and only accepts a candidate that ast.literal_eval() confirms is
    genuinely valid. Same safety property as _repair_unterminated_string():
    repairs the TEXT, never guesses INTENT, and can't execute arbitrary
    code.
    """
    stripped = raw.rstrip()
    for extra_braces in (1, 2):
        candidate = stripped + ("}" * extra_braces)
        try:
            ast.literal_eval(candidate)
            return candidate
        except (ValueError, SyntaxError):
            continue
    return None


def _extract_first_json_object(raw: str) -> str | None:
    """
    Scans for the first "{" and walks forward tracking brace depth --
    respecting quoted string content, so a brace that's just part of a
    string value doesn't miscount -- to isolate exactly the first
    complete {...} block in the text. Real gap found live 2026-08-22
    (86bbhckr3 checkpoint comparison): SWE-Dev-7B and SWE-Gym's
    openhands-lm-7b checkpoint routinely narrate ("Let me start by...")
    before AND between multiple separate tool calls within a single
    turn, so parsing the raw response as one object failed even though
    each individual JSON block was itself complete and valid -- no
    amount of edge-repair fixes that, since the problem isn't a
    malformed object, it's several objects (plus prose) where the parser
    expects exactly one.

    This only isolates a candidate substring -- it doesn't validate it,
    that's still _try_parse_raw()'s job. Returns None if no "{" exists,
    or if brace depth never returns to zero (an incomplete/truncated
    block), in which case the caller falls back to the full raw text so
    the existing repair tiers still get a chance at it.
    """
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    string_char = ""
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == string_char:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            string_char = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _has_second_tool_call(raw: str, first_extracted: str) -> bool:
    """
    Real gap found live 2026-08-23 (86bbk09da): after _extract_first_json_object() isolates
    the block that _parse_tool_call() actually used, checks whether the REMAINDER of the raw
    text contains another complete {...} block that itself looks like a tool call (has a
    "tool" key, single- or double-quoted). Only flags a genuine second call, not trailing
    prose/narration, so it doesn't misfire on the harmless case _extract_first_json_object()
    was originally built to handle.
    """
    idx = raw.find(first_extracted)
    if idx == -1:
        return False
    remainder = raw[idx + len(first_extracted) :]
    second = _extract_first_json_object(remainder)
    if second is None:
        return False
    return re.search(r"""['"]tool['"]\s*:""", second) is not None


def _try_parse_raw(raw: str) -> tuple[dict | None, str]:
    """
    Four graduated attempts to turn the model's raw text into a real
    Python dict, cheapest and strictest first -- the answer to "what if
    parsing didn't care about the model's exact format, only that it can be
    turned into something valid without losing meaning": (1) strict JSON,
    the fast path when the model gets it exactly right; (2) ast.literal_eval,
    which safely accepts Python literal syntax (single/triple-quoted
    strings) that isn't valid JSON but IS valid, complete Python -- covers
    the model's real, repeated bias toward Python string conventions over
    strict JSON; (3) _repair_unterminated_string, for the specific real
    truncation pattern found live (a missing closing quote); (4)
    _repair_missing_closing_braces, for the specific real truncation
    pattern found live in the 86bbhckr3 checkpoint comparison (missing
    closing brace(s) on an otherwise-complete call). ast.literal_eval only
    evaluates literal expressions (dicts/strings/numbers/etc.) -- unlike
    eval(), it cannot execute arbitrary code, so this stays safe against
    untrusted model output even while being lenient about its exact syntax.

    Before any of that, _extract_first_json_object() isolates the first
    complete {...} block out of any surrounding narration -- real gap
    found live in the same 86bbhckr3 comparison: a response can be
    "Let me start by... {...} Now let's... {...}", where every tier above
    fails on the FULL response even though the first block alone is
    perfectly valid. All four tiers then run against the extracted
    substring first; if that whole chain comes up empty, they're retried
    against the original raw text (covers a truncated first block, where
    extraction itself found nothing to isolate).

    Returns (parsed_dict_or_None, method) where method is "json", "python",
    "repaired", or "repaired_brace" on success -- surfaced so a real run
    can see which tier actually did the work, not just that parsing
    succeeded. On total failure, method carries the real
    json.JSONDecodeError text instead (the most informative of the four
    failures, since strict JSON is the documented contract) so the caller
    doesn't need to re-parse a second time just to build an error message.
    """

    def _graduated_tiers(text: str) -> tuple[dict | None, str]:
        try:
            return json.loads(text), "json"
        except json.JSONDecodeError as e:
            json_error = str(e)
        try:
            return ast.literal_eval(text), "python"
        except (ValueError, SyntaxError):
            pass
        repaired = _repair_unterminated_string(text)
        if repaired is not None:
            return ast.literal_eval(repaired), "repaired"
        repaired_brace = _repair_missing_closing_braces(text)
        if repaired_brace is not None:
            return ast.literal_eval(repaired_brace), "repaired_brace"
        return None, json_error

    extracted = _extract_first_json_object(raw)
    if extracted is not None:
        parsed, method = _graduated_tiers(extracted)
        if parsed is not None:
            return parsed, method
    return _graduated_tiers(raw)


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


def _build_progress_note(turn: int, successful_edit_count: int, edit_succeeded_this_turn: bool) -> str:
    """
    Positive-framing counterpart to this file's existing refusal-only nudges (real gap
    found live spot-checking 86bbjx8zp's pilot transcripts -- see run_exercise()'s own
    `progress_framing` docstring). Always states real, checkable facts (turn count,
    real edit count) rather than vague encouragement -- credits genuine progress when it
    happened this turn, and otherwise just keeps the turn/edit count visible so an idle
    run has a chance to notice its own lack of progress without being told it's wrong.
    """
    if edit_succeeded_this_turn:
        credit = (
            f"Real progress: that edit was accepted and changed the file ({successful_edit_count} real edit(s) so far)."
        )
    else:
        credit = f"{successful_edit_count} real edit(s) made so far."
    return f"\n\n[{credit} Turn {turn}/{MAX_TURNS}.]"


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

    Real gap found live 2026-08-22 (86bbhckr3 checkpoint comparison,
    two-bucket exercise): subprocess.run's own timeout raises
    subprocess.TimeoutExpired rather than returning normally, and that
    wasn't caught here -- one model-generated infinite loop crashed the
    entire run_all_exercises() batch instead of just failing that one
    exercise. Caught now and reported as a normal (False, message) result,
    same contract as every other failure path.
    """
    module_name = slug.replace("-", "_")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", f"{module_name}_test", "-v"],
            cwd=str(working_copy),
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"REAL TEST TIMEOUT: tests did not finish within {TEST_TIMEOUT_SECONDS}s -- almost "
            "certainly an infinite loop or blocking call in the model's own code, not a "
            "harness/test-file problem."
        )
    return result.returncode == 0, result.stdout + result.stderr


def _parse_test_pass_fraction(test_output: str) -> float | None:
    """
    Extracts what fraction of tests passed from unittest -v's per-test lines
    (e.g. "test_foo (module.Cls.test_foo) ... ok" / "... FAIL" / "... ERROR"),
    for --early-abandon's stall detection. Returns None if no per-test lines
    are found at all (e.g. a real import error that never reaches unittest's
    own test runner) -- a real absence of signal, not a 0.0 pass rate, since
    0.0 would misleadingly count as "no worse than last time" in the stall
    check below.
    """
    outcomes = re.findall(r"^\S.* \.\.\. (ok|FAIL|ERROR)\s*$", test_output, re.MULTILINE)
    if not outcomes:
        return None
    passed = sum(1 for o in outcomes if o == "ok")
    return passed / len(outcomes)


def _generative_style_verifier(
    client: anthropic.Anthropic, task_description: str, solution_content: str
) -> tuple[str, str]:
    """
    Real execution-free half of Phase 5's hybrid verifier (86bbcfpd1) -- a
    generative judgment call, not a simple classifier, per Eval Harness
    Initiative 3's own explicit preference ("generative verifiers hold up
    much better on novel, out-of-domain failures"). No `tools` argument --
    a judge, never a writer.

    Returns (verdict, reason) where verdict is one of:
      "ACCEPT" -- no concern, reason is empty.
      "GAMED"  -- output values copied from the visible test cases rather
                  than a genuine implementation. A real correctness/cheating
                  issue, blocks `done` regardless of flags.
      "IDIOM"  -- genuine implementation, but clearly unidiomatic Python.
                  A subjective style opinion -- only blocks `done` when
                  --advisory-idiom is off (see _hybrid_verify_gate).

    The old two-in-one "CONCERNS" verdict was split into GAMED vs. IDIOM
    for 86bbcfv9d (Eval Harness Initiative 2, "audit existing gates
    individually"): the two carry very different stakes, and treating an
    idiom nudge with the same blocking force as a gaming flag is the
    confirmed trigger for the `octal` loss-of-working-solution failure
    (docs/aci-hybrid-verify-gate-audit.md). The categorization itself is
    unconditional -- every hybrid-verify run logs which one fired, so we
    have real GAMED-vs-IDIOM data before deciding whether to promote the
    flag. An unrecognized reply falls back to ACCEPT (fail open -- a judge
    that can't answer clearly should not block a solution that already
    passes every real test).
    """
    system = (
        "You are reviewing a real solution to a small coding exercise for two specific things, "
        "not general code quality: (1) a gamed or hardcoded solution -- output values copied "
        "from the visible test cases rather than a genuine implementation of the described "
        "logic, and (2) clearly unidiomatic Python that a competent developer would not write. "
        "If neither applies, respond with exactly: ACCEPT. If (1) applies, respond with exactly: "
        "GAMED: <one sentence reason>. If only (2) applies, respond with exactly: "
        "IDIOM: <one sentence reason>."
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
    upper = verdict_text.upper()
    reason = verdict_text.split(":", 1)[1].strip() if ":" in verdict_text else verdict_text
    if upper.startswith("GAMED"):
        return "GAMED", reason
    if upper.startswith("IDIOM"):
        return "IDIOM", reason
    return "ACCEPT", ""


def _hybrid_verify_gate(
    client: anthropic.Anthropic,
    working_copy: Path,
    slug: str,
    task_description: str,
    advisory_idiom: bool = False,
) -> tuple[bool, str, bool, float | None, str | None, str | None, str]:
    """
    Phase 5's real hybrid gate (86bbcfpd1) -- runs right before a `done`
    call is accepted, when --hybrid-verify is on. Execution-based first
    (cheap, deterministic, the same _run_real_tests() the final scoring
    metric already uses): a real test failure never reaches the
    generative call at all, no reason to pay for a style opinion on code
    that doesn't work yet. Execution-free second, only once tests already
    pass.

    Returns (gate_passed, nudge_text, style_call_made, pass_fraction,
    solution_content, style_verdict, style_reason):
      - the caller tallies style_call_made into the real per-run cost count
        regardless of the verdict;
      - pass_fraction feeds the stall-detection check (--early-abandon) and
        the regression-guard nudge (--regression-guard);
      - solution_content is the real file content at this check, non-None
        only when the real test suite passed (86bbmj2hw -- regression-guard's
        snapshot source, since a solution is only worth snapshotting once
        it's actually correct);
      - style_verdict is None when the tests failed (no style call was
        made), else "ACCEPT" / "GAMED" / "IDIOM";
      - style_reason is the one-sentence explanation for GAMED/IDIOM, "" otherwise.

    `advisory_idiom` (86bbcfv9d): when True, an IDIOM verdict (passes every
    real test, just unidiomatic) does NOT block `done` -- it's logged and
    accepted. GAMED still blocks unconditionally. When False, both GAMED and
    IDIOM block, preserving the pre-split behavior. See
    docs/aci-hybrid-verify-gate-audit.md.
    """
    test_passed, test_output = _run_real_tests(working_copy, slug)
    pass_fraction = _parse_test_pass_fraction(test_output)
    if not test_passed:
        nudge = (
            "Your solution does not pass the real test suite yet. Real test output:\n\n"
            f"{test_output[:1500]}\n\nFix the real failures shown above before calling done again."
        )
        return False, nudge, False, pass_fraction, None, None, ""

    module_name = slug.replace("-", "_")
    solution_content = (working_copy / f"{module_name}.py").read_text(encoding="utf-8")
    verdict, reason = _generative_style_verifier(client, task_description, solution_content)
    if verdict == "ACCEPT":
        return True, "", True, pass_fraction, solution_content, verdict, reason

    if verdict == "IDIOM" and advisory_idiom:
        # Advisory only -- the solution passes every real test; an idiom
        # opinion is not worth risking the model breaking it to chase.
        return True, "", True, pass_fraction, solution_content, verdict, reason

    if verdict == "GAMED":
        nudge = (
            f"Your solution passes the real tests, but a review flagged it as a likely gamed or "
            f"hardcoded solution: {reason} Replace it with a genuine implementation of the "
            "described logic before calling done again."
        )
    else:
        nudge = (
            f"Your solution passes the real tests, but a style review flagged a real concern: {reason} "
            "Address this before calling done again."
        )
    return False, nudge, True, pass_fraction, solution_content, verdict, reason


def run_exercise(
    slug: str,
    verbose: bool = False,
    diff_format: bool = False,
    hybrid_verify: bool = False,
    model: str = OLLAMA_MODEL,
    extra_context: str = "",
    progress_framing: bool = False,
    max_hybrid_nudges: int = MAX_HYBRID_VERIFY_NUDGES,
    early_abandon: bool = False,
    regression_guard: bool = True,
    advisory_idiom: bool = False,
    disabled_guards: frozenset[str] = frozenset(),
    same_path_guard: bool = False,
) -> dict:
    """
    Runs one real vendored exercise through the given Ollama model via the
    ACI, end to end. The model never sees .meta/example.py (excluded before
    the working copy even exists) or is told a reference solution exists at
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

    `model`: Ollama tag to run against, default OLLAMA_MODEL
    (qwen2.5-coder:7b). 86bbhckr3's checkpoint-comparison override -- always
    logged on the result (see _log_result) so a comparison run against a
    different checkpoint never silently pools into the baseline's stats.

    `extra_context`: 86bbjx8zp's shared-search-space pilot -- an optional
    pre-formatted block of retrieved prior-attempt context, inserted into
    the initial user message between the task description and the file
    listing. Empty by default, so every existing call site is unaffected.
    `retrieval_context_used` is always logged on the result (same
    never-silently-pool discipline as `model`/`diff_format` above).

    `progress_framing`: real gap found live while spot-checking 86bbjx8zp's
    pilot transcripts -- every existing guard/nudge in this file is purely
    prohibitive ("Refused: ..."), and a genuinely SUCCESSFUL-but-idle loop
    (repeated `view` calls returning the same unchanged result, no other
    action) is invisible to the model today, since nothing ever tells it
    how much real progress it has or hasn't made. Off by default, same
    opt-in-flag-plus-logged-axis pattern as `diff_format`/`hybrid_verify` --
    this is an untested intervention, not yet shown to help, so it shouldn't
    change default behavior until a real A/B batch says otherwise.

    `max_hybrid_nudges`: 86bbkr47d follow-up (2026-08-24) -- overrides
    MAX_HYBRID_VERIFY_NUDGES for this run only. Real finding: the default
    cap of 2 means a near-miss solution (failing only 1-4 tests) can get
    waved through as "completed" while still failing, once both real
    rejection budgets are spent -- untested whether more correction cycles
    would have actually closed the gap, or whether the model was genuinely
    stuck regardless of budget.

    `early_abandon`: no ClickUp ticket filed (2026-08-24), SVT-inspired. Real
    collapse pattern found in the max_hybrid_nudges probe -- some runs burn
    the entire turn budget while the real test-pass fraction gets WORSE
    across repeated `done` attempts, not better. When on, stops the run
    early (final_status="abandoned_no_improvement") once
    EARLY_ABANDON_PATIENCE consecutive hybrid-verify checks pass without a
    real improvement in test-pass fraction, instead of burning the rest of
    MAX_TURNS on a run that isn't converging. No effect without
    --hybrid-verify (there is no pass-fraction signal without it).

    `regression_guard`: 86bbmj2hw, filed after a verbose re-run of an
    --early-abandon transcript (octal, 2026-08-25) showed the real cause of
    that collapse pattern -- a model reached a genuinely 100%-passing
    solution, the style verifier rejected it anyway, and the "fix" attempt
    introduced a real regression (a nested `def` shadowing itself) that was
    never recovered, silently discarding the working solution. Two parts,
    both gated by this one flag: (1) when a hybrid-verify check's pass
    fraction drops below the run's best-seen, the nudge sent back to the
    model gets an explicit "you regressed from X% to Y%" line, turning the
    tracked signal into real corrective feedback instead of only feeding
    --early-abandon's internal stall counter; (2) if the run still ends
    without a passing solution but a fully-passing (pass_fraction == 1.0)
    snapshot was seen earlier, that snapshot is restored to disk and
    re-verified before final scoring, so a real regression-during-nudging
    doesn't cost a run that already had a correct answer. `snapshot_restored`
    is logged on the result whenever restoration actually happened. No
    effect without --hybrid-verify (same reason as --early-abandon).

    Promoted to on-by-default 2026-08-27 after 3 real A/B batches
    (n=210/condition combined: 27/210 baseline vs. 34/210 with the guard, 5
    real causal snapshot restores, zero net-negative batches) -- see
    memory `project_early_abandon_ab_and_snapshot_finding.md`. Pass
    `--no-regression-guard` to opt back out.

    `advisory_idiom`: 86bbcfv9d (Eval Harness Initiative 2, "audit existing
    gates individually"). The generative style verifier now categorizes its
    concern as GAMED (copied test outputs -- a real cheating issue) or IDIOM
    (passes honestly, just unidiomatic). When this flag is on, an IDIOM
    verdict is logged and accepted rather than blocking `done` -- an idiom
    nudge on an already-passing solution is the confirmed trigger for the
    octal loss-of-working-solution failure that `regression_guard` only
    catches after the fact. GAMED always blocks. Off by default (preserves
    the pre-split block-both behavior); promote later only if a real A/B
    batch supports it. No effect without --hybrid-verify. See
    docs/aci-hybrid-verify-gate-audit.md.

    `disabled_guards`: 86bbcfv9d (Eval Harness Initiative 2). A subset of
    ABLATABLE_GUARDS whose effect is suppressed for this run -- the loop
    reverts to pre-guard behaviour at that point (executes the repeat,
    accepts the no-edit `done`, drops the same-path note / multi-call note).
    A would-have-fired count is still recorded in the result's
    `guards_suppressed`. Used by scripts/run_guard_ablation.py to measure
    each guard's individual contribution against the real corpus, rather
    than only ever cumulatively (docs/aci-failure-mechanism-analysis.md
    measured guards 1->2->3 as a block, not one-at-a-time). Empty by
    default -- every ablatable guard on, exactly as before.

    `same_path_guard`: 86bbcfv9d. The same-path-repeated-failure nudge, now
    OPT-IN (default off) after its individual ablation came back net-negative
    on turn-efficiency across two batches (n=240/condition) -- see the
    GUARD_SAME_PATH_REPEATED_FAILURE comment and docs/aci-guard-cluster-ablation.md.
    Kept as a tested reference; pass True / --same-path-guard to turn it back on.
    """
    unknown_guards = set(disabled_guards) - ABLATABLE_GUARDS
    if unknown_guards:
        raise ValueError(f"disabled_guards contains non-ablatable name(s): {sorted(unknown_guards)}")

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
        user_content = f"Task:\n\n{task_description}"
        if extra_context:
            user_content += (
                "\n\nReference: prior attempts on similar tasks (for context only -- "
                f"this is a different task):\n\n{extra_context}"
            )
        user_content += f"\n\nFiles in your working directory: {initial_files}"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_DIFF if diff_format else SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        final_status = "max_turns_reached"
        turns_used = 0
        parse_method_counts = {"json": 0, "python": 0, "repaired": 0, "repaired_brace": 0}
        parse_failures = 0

        # Guard state -- see GUARD_REPEAT_FAILED_CALL/GUARD_DONE_WITHOUT_EDIT/
        # GUARD_SAME_PATH_REPEATED_FAILURE/GUARD_HYBRID_VERIFY_REJECTED above.
        failed_calls: set = set()
        repeat_refusal_counts: dict = {}
        has_successful_edit = False
        successful_edit_count = 0
        done_without_edit_nudges = 0
        path_failure_counts: dict = {}
        # Real test-pass-fraction tracking, shared by --early-abandon and --regression-guard
        # (86bbmj2hw) -- tracked unconditionally whenever hybrid_verify is on, since both
        # flags need the same underlying signal: the best real pass fraction seen so far
        # this run, the real file content at the moment that best was reached (only ever
        # set when pass_fraction == 1.0 -- a solution is only worth snapshotting once it's
        # actually correct), and how many consecutive hybrid-verify checks have passed with
        # no real improvement over that best (--early-abandon's stall counter).
        best_pass_fraction = 0.0
        best_snapshot_content: str | None = None
        turns_without_improvement = 0
        snapshot_restored = False
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
        # 86bbcfv9d: the style verifier's verdict is now 3-way (ACCEPT/GAMED/IDIOM). Track
        # the split so every hybrid-verify run has real data on which concern fired, and
        # (when --advisory-idiom is on) surface the accepted idiom note on the result.
        style_idiom_note: str | None = None
        style_gamed_rejections = 0
        guard_fires = {
            GUARD_REPEAT_FAILED_CALL: 0,
            GUARD_DONE_WITHOUT_EDIT: 0,
            GUARD_SAME_PATH_REPEATED_FAILURE: 0,
            GUARD_HYBRID_VERIFY_REJECTED: 0,
            GUARD_MULTIPLE_CALLS_IGNORED: 0,
        }
        # 86bbcfv9d: for each ablated guard, how many times it would have fired this run
        # if it had been active. Only the keys in `disabled_guards` ever get incremented.
        guards_suppressed = {name: 0 for name in disabled_guards}

        def _multi_call_suffix() -> str:
            """
            86bbk09da's multi-call nudge, honouring 86bbcfv9d's ablation. Returns the
            nudge suffix to append when this turn's raw text carried a second, dropped
            tool call -- and "" (recording a would-have-fired count) when
            GUARD_MULTIPLE_CALLS_IGNORED is in disabled_guards. `has_extra_call` is read
            late, per turn, from the enclosing loop.
            """
            if not has_extra_call:
                return ""
            if GUARD_MULTIPLE_CALLS_IGNORED in disabled_guards:
                guards_suppressed[GUARD_MULTIPLE_CALLS_IGNORED] += 1
                return ""
            guard_fires[GUARD_MULTIPLE_CALLS_IGNORED] += 1
            return MULTIPLE_CALLS_NUDGE

        for turn in range(1, MAX_TURNS + 1):
            turns_used = turn
            messages = [messages[0]] + aci.collapse_history(messages[1:], keep_recent=HISTORY_KEEP_RECENT)

            response = client.chat(model=model, messages=messages, options={"num_ctx": OLLAMA_NUM_CTX})
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

            # 86bbk09da: detect a second tool call silently sitting in this turn's raw
            # text, beyond the one _parse_tool_call() already used -- see
            # GUARD_MULTIPLE_CALLS_IGNORED above. Computed once per turn, applied at
            # whichever branch below ends up sending the next user-role message.
            stripped_raw = _strip_code_fence(raw_content)
            first_block = _extract_first_json_object(stripped_raw) or stripped_raw
            has_extra_call = _has_second_tool_call(stripped_raw, first_block)

            if tool == "done":
                if not has_successful_edit and GUARD_DONE_WITHOUT_EDIT in disabled_guards:
                    # 86bbcfv9d ablation: guard suppressed -- accept the `done` with
                    # nothing attempted and stop, exactly as the pre-guard harness did.
                    # Deliberately does NOT fall through to the hybrid-verify gate, which
                    # assumes a real edit exists to verify.
                    guards_suppressed[GUARD_DONE_WITHOUT_EDIT] += 1
                    final_status = "completed"
                    break
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
                    nudge += _multi_call_suffix()
                    messages.append({"role": "user", "content": nudge})
                    if verbose:
                        print(f"--- turn {turn}: refused done (no successful edit yet) ---")
                    continue

                # has_successful_edit is True from here on -- Phase 5's real hybrid gate
                # (86bbcfpd1) only applies once there's something real to verify.
                if not hybrid_verify:
                    final_status = "completed"
                    break
                if test_fail_nudges >= max_hybrid_nudges and style_concern_nudges >= max_hybrid_nudges:
                    final_status = "completed"
                    break

                (
                    gate_passed,
                    gate_nudge,
                    style_call_made,
                    pass_fraction,
                    solution_content,
                    style_verdict,
                    style_reason,
                ) = _hybrid_verify_gate(
                    anthropic_client, working_copy, slug, task_description, advisory_idiom=advisory_idiom
                )
                if style_call_made:
                    style_verifier_calls += 1
                # 86bbcfv9d: record the idiom note whenever it was the verdict -- both when
                # --advisory-idiom accepted it (gate_passed True) and when the flag was off
                # and it blocked (so a blocked-on-idiom run is still visible in the logs).
                if style_verdict == "IDIOM":
                    style_idiom_note = style_reason
                if gate_passed:
                    final_status = "completed"
                    break

                # Real test-pass-fraction tracking, shared by --early-abandon and
                # --regression-guard (86bbmj2hw) -- updated whenever this check produced a
                # real fraction, regardless of which flag is on, so both features see the
                # same signal without duplicating the bookkeeping.
                regressed_from = None
                if pass_fraction is not None:
                    if pass_fraction > best_pass_fraction:
                        best_pass_fraction = pass_fraction
                        if solution_content is not None:
                            best_snapshot_content = solution_content
                        turns_without_improvement = 0
                    else:
                        if pass_fraction < best_pass_fraction:
                            regressed_from = best_pass_fraction
                        turns_without_improvement += 1
                        # --early-abandon (SVT-inspired): stop burning turns once the real
                        # test-pass fraction stalls for EARLY_ABANDON_PATIENCE consecutive
                        # checks instead of improving -- the run isn't converging, so the
                        # rest of MAX_TURNS is unlikely to change that (real pattern found
                        # live: some runs got WORSE across repeated `done` attempts, never
                        # better).
                        if early_abandon and turns_without_improvement >= EARLY_ABANDON_PATIENCE:
                            final_status = "abandoned_no_improvement"
                            break

                # --regression-guard (86bbmj2hw): a real drop below the run's best-seen
                # fraction means the model just broke something that used to work (found
                # live: a passing int(digits, 8) solution replaced by a self-shadowing
                # nested def while chasing a style nudge). Say so explicitly rather than
                # only feeding the drop into --early-abandon's internal stall counter.
                if regression_guard and regressed_from is not None:
                    gate_nudge += (
                        f"\n\nNote: you previously reached a {regressed_from:.0%} real test "
                        f"pass rate, but this attempt only reaches {pass_fraction:.0%} -- you "
                        "have regressed. Consider reverting toward your earlier approach "
                        "rather than continuing further down this one."
                    )

                if style_call_made:
                    if style_verdict == "GAMED":
                        style_gamed_rejections += 1
                    if style_concern_nudges >= max_hybrid_nudges:
                        final_status = "completed"
                        break
                    style_concern_nudges += 1
                else:
                    if test_fail_nudges >= max_hybrid_nudges:
                        final_status = "completed"
                        break
                    test_fail_nudges += 1

                guard_fires[GUARD_HYBRID_VERIFY_REJECTED] += 1
                messages.append({"role": "user", "content": gate_nudge})
                if verbose:
                    print(f"--- turn {turn}: hybrid-verify gate rejected done ---")
                continue

            key = _call_key(call)
            if key in failed_calls and GUARD_REPEAT_FAILED_CALL in disabled_guards:
                # 86bbcfv9d ablation: guard suppressed -- fall through and re-execute the
                # already-failed call, exactly as the pre-guard harness did (bob resent
                # the same broken edit 13 times this way).
                guards_suppressed[GUARD_REPEAT_FAILED_CALL] += 1
            elif key in failed_calls:
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
                refusal += _multi_call_suffix()
                messages.append({"role": "user", "content": refusal})
                if verbose:
                    print(f"--- turn {turn}: refused repeat of already-failed {tool} call ---")
                continue

            tool_result = _execute_tool(call, root, diff_format=diff_format)
            edit_succeeded_this_turn = False
            if tool == "edit" and _tool_result_failed(tool, tool_result):
                failed_calls.add(key)
                path = call.get("arguments", {}).get("path", "")
                path_failure_counts[path] = path_failure_counts.get(path, 0) + 1
                # same_path_guard is opt-in (default off) since 2026-08-30 -- its ablation
                # came back net-negative on turn-efficiency (86bbcfv9d).
                if same_path_guard and path_failure_counts[path] >= SAME_PATH_FAILURE_THRESHOLD:
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
                successful_edit_count += 1
                edit_succeeded_this_turn = True
            tool_result += _multi_call_suffix()
            if progress_framing:
                tool_result += _build_progress_note(turn, successful_edit_count, edit_succeeded_this_turn)
            messages.append({"role": "user", "content": tool_result})
            if verbose:
                method = call.get("_parse_method", "?")
                print(f"--- turn {turn}: ran {tool} (parsed via {method}) -> {tool_result[:300]}")

        test_passed, test_output = _run_real_tests(working_copy, slug)

        # --regression-guard (86bbmj2hw): if the run still ended without a passing
        # solution but a genuinely 100%-passing snapshot was seen earlier this run,
        # restore it to disk and re-verify before final scoring -- otherwise a real
        # regression introduced while chasing a style nudge (found live: octal,
        # 2026-08-25 -- a passing int(digits, 8) solution replaced by a self-shadowing
        # nested def) silently costs a run that already had a correct answer.
        if regression_guard and not test_passed and best_snapshot_content is not None:
            restore_module_name = slug.replace("-", "_")
            restore_path = working_copy / f"{restore_module_name}.py"
            restore_path.write_text(best_snapshot_content, encoding="utf-8")
            restored_passed, restored_output = _run_real_tests(working_copy, slug)
            if restored_passed:
                test_passed, test_output = restored_passed, restored_output
                snapshot_restored = True

        # 86bbjx8zp: the model's final attempt at the target file, whatever it ended up
        # with (pass or fail) -- not captured anywhere before this, needed so a caller
        # (nova_squad_pilot.py) can use it as attempt-memory content once the temp
        # directory this working copy lives in is gone.
        module_name = slug.replace("-", "_")
        solution_path = working_copy / f"{module_name}.py"
        solution_content = solution_path.read_text(encoding="utf-8") if solution_path.exists() else ""

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "slug": slug,
            "model": model,
            "diff_format": diff_format,
            "turns_used": turns_used,
            "final_status": final_status,
            "test_passed": test_passed,
            "test_output": test_output,
            "solution_content": solution_content,
            "retrieval_context_used": bool(extra_context),
            "progress_framing_enabled": progress_framing,
            "parse_method_counts": parse_method_counts,
            "parse_failures": parse_failures,
            "guard_fires": guard_fires,
            "disabled_guards": sorted(disabled_guards),
            "guards_suppressed": guards_suppressed,
            "same_path_guard_enabled": same_path_guard,
            "hybrid_verify_enabled": hybrid_verify,
            "style_verifier_calls": style_verifier_calls,
            "test_fail_nudges": test_fail_nudges,
            "style_concern_nudges": style_concern_nudges,
            "advisory_idiom_enabled": advisory_idiom,
            "style_idiom_note": style_idiom_note,
            "style_gamed_rejections": style_gamed_rejections,
            "early_abandon_enabled": early_abandon,
            "best_pass_fraction_seen": best_pass_fraction,
            "regression_guard_enabled": regression_guard,
            "snapshot_restored": snapshot_restored,
        }
        _log_result(result)
        return result


def _log_result(result: dict) -> None:
    """
    Appends one real run's result to RESULTS_LOG_PATH. test_output and
    solution_content are dropped from the logged copy -- both verbose, not
    needed for analysis; solution_content is only needed transiently by an
    in-memory caller (nova_squad_pilot.py) before the working copy is gone.
    """
    RESULTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {k: v for k, v in result.items() if k not in ("test_output", "solution_content")}
    with open(RESULTS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_all_exercises(
    verbose: bool = False,
    repeats: int = 1,
    diff_format: bool = False,
    hybrid_verify: bool = False,
    model: str = OLLAMA_MODEL,
    progress_framing: bool = False,
    max_hybrid_nudges: int = MAX_HYBRID_VERIFY_NUDGES,
    early_abandon: bool = False,
    regression_guard: bool = True,
    advisory_idiom: bool = False,
    disabled_guards: frozenset[str] = frozenset(),
    same_path_guard: bool = False,
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
            result = run_exercise(
                slug,
                verbose=verbose,
                diff_format=diff_format,
                hybrid_verify=hybrid_verify,
                model=model,
                progress_framing=progress_framing,
                max_hybrid_nudges=max_hybrid_nudges,
                early_abandon=early_abandon,
                regression_guard=regression_guard,
                advisory_idiom=advisory_idiom,
                disabled_guards=disabled_guards,
                same_path_guard=same_path_guard,
            )
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
    totals = {"json": 0, "python": 0, "repaired": 0, "repaired_brace": 0}
    total_parse_failures = 0
    guard_totals = {
        GUARD_REPEAT_FAILED_CALL: 0,
        GUARD_DONE_WITHOUT_EDIT: 0,
        GUARD_SAME_PATH_REPEATED_FAILURE: 0,
        GUARD_HYBRID_VERIFY_REJECTED: 0,
        GUARD_MULTIPLE_CALLS_IGNORED: 0,
    }
    status_totals: dict[str, int] = {}
    total_style_verifier_calls = 0
    total_gamed_rejections = 0
    total_idiom_notes = 0
    suppressed_totals: dict[str, int] = {}
    for r in results:
        for method, count in r["parse_method_counts"].items():
            totals[method] += count
        total_parse_failures += r["parse_failures"]
        for guard, count in r.get("guard_fires", {}).items():
            guard_totals[guard] = guard_totals.get(guard, 0) + count
        for guard, count in r.get("guards_suppressed", {}).items():
            suppressed_totals[guard] = suppressed_totals.get(guard, 0) + count
        status_totals[r["final_status"]] = status_totals.get(r["final_status"], 0) + 1
        total_style_verifier_calls += r.get("style_verifier_calls", 0)
        total_gamed_rejections += r.get("style_gamed_rejections", 0)
        if r.get("style_idiom_note"):
            total_idiom_notes += 1

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
    print(f"  repaired (targeted fix, missing quote): {totals['repaired']}")
    print(f"  repaired_brace (targeted fix, missing brace): {totals['repaired_brace']}")
    print(f"  total parse failures (no tier recovered it): {total_parse_failures}")

    print("\nFinal status breakdown:")
    for status in sorted(status_totals, key=lambda s: -status_totals[s]):
        print(f"  {status:<24} {status_totals[status]}")

    print("\nGuard fire totals (docs/aci-failure-mechanism-analysis.md):")
    print(f"  {GUARD_REPEAT_FAILED_CALL}: {guard_totals[GUARD_REPEAT_FAILED_CALL]}")
    print(f"  {GUARD_DONE_WITHOUT_EDIT}: {guard_totals[GUARD_DONE_WITHOUT_EDIT]}")
    print(f"  {GUARD_SAME_PATH_REPEATED_FAILURE}: {guard_totals[GUARD_SAME_PATH_REPEATED_FAILURE]}")
    print(f"  {GUARD_HYBRID_VERIFY_REJECTED}: {guard_totals[GUARD_HYBRID_VERIFY_REJECTED]}")
    print(f"  {GUARD_MULTIPLE_CALLS_IGNORED}: {guard_totals[GUARD_MULTIPLE_CALLS_IGNORED]}")

    if suppressed_totals:
        print("\nAblated guards -- would-have-fired totals this batch (86bbcfv9d):")
        for guard in sorted(suppressed_totals):
            print(f"  {guard}: {suppressed_totals[guard]}")

    if total_style_verifier_calls:
        print(f"\nReal style-verifier (Claude) calls this batch: {total_style_verifier_calls}")
        print(f"  GAMED rejections (blocked done): {total_gamed_rejections}")
        print(f"  runs with an IDIOM note: {total_idiom_notes}")


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
    parser.add_argument(
        "--model",
        default=OLLAMA_MODEL,
        metavar="TAG",
        help=(
            f"Ollama model tag to run against (default: {OLLAMA_MODEL}). 86bbhckr3: override to "
            "compare an existing checkpoint (e.g. a pulled SWE-Gym-7B/SWE-Dev-7B GGUF) against "
            "Nova's own ACI corpus. Always logged on the result so runs never mix models silently."
        ),
    )
    parser.add_argument(
        "--progress-framing",
        action="store_true",
        help=(
            "Real gap found live spot-checking 86bbjx8zp's pilot transcripts: append a positive, "
            "fact-based progress note (real edit count + turn count) to every tool result, instead "
            "of the harness only ever speaking up to refuse a bad action. Off by default -- an "
            "untested intervention, not yet shown to help via a real A/B batch."
        ),
    )
    parser.add_argument(
        "--max-hybrid-nudges",
        type=int,
        default=MAX_HYBRID_VERIFY_NUDGES,
        metavar="N",
        help=(
            f"86bbkr47d follow-up: override MAX_HYBRID_VERIFY_NUDGES (default {MAX_HYBRID_VERIFY_NUDGES}) "
            "for this run only, to test whether a near-miss solution needs more real "
            "test-feedback correction cycles than the default cap allows. No effect without "
            "--hybrid-verify."
        ),
    )
    parser.add_argument(
        "--early-abandon",
        action="store_true",
        help=(
            f"SVT-inspired (no ClickUp ticket filed): stop a run early "
            f"(final_status='abandoned_no_improvement') once EARLY_ABANDON_PATIENCE "
            f"({EARLY_ABANDON_PATIENCE}) consecutive hybrid-verify checks pass with no real "
            "improvement in test-pass fraction, instead of burning the rest of MAX_TURNS on a "
            "run that isn't converging. Off by default. No effect without --hybrid-verify."
        ),
    )
    parser.add_argument(
        "--no-regression-guard",
        dest="regression_guard",
        action="store_false",
        default=True,
        help=(
            "86bbmj2hw: opt OUT of the regression guard (on by default as of 2026-08-27, "
            "promoted after 3 real A/B batches showed a modest real pass-rate edge with no "
            "net-negative batch -- see project_early_abandon_ab_and_snapshot_finding.md). When "
            "on: a hybrid-verify check's real test-pass fraction dropping below the run's "
            "best-seen tells the model explicitly it regressed (real X%% -> Y%% line appended to "
            "the nudge) instead of only feeding the drop into --early-abandon's internal stall "
            "counter, and a genuinely 100%%-passing snapshot is restored+re-verified at run end "
            "if the final state never recovered one. No effect without --hybrid-verify."
        ),
    )
    parser.add_argument(
        "--advisory-idiom",
        action="store_true",
        help=(
            "86bbcfv9d (Eval Harness Initiative 2): split the generative style gate. The verifier "
            "now returns GAMED (output values copied from the visible test cases -- a real "
            "cheating issue, always blocks done) or IDIOM (passes every real test, just "
            "unidiomatic). With this flag on, an IDIOM verdict is logged (style_idiom_note) and "
            "accepted instead of nudging -- an idiom nudge on an already-passing solution is the "
            "confirmed trigger for the octal loss-of-working-solution failure that "
            "--regression-guard only catches after the fact. Off by default (blocks on both, the "
            "pre-split behavior). No effect without --hybrid-verify. See "
            "docs/aci-hybrid-verify-gate-audit.md."
        ),
    )
    parser.add_argument(
        "--disable-guard",
        action="append",
        default=[],
        choices=sorted(ABLATABLE_GUARDS),
        metavar="NAME",
        dest="disabled_guards",
        help=(
            "86bbcfv9d (Eval Harness Initiative 2): suppress one always-on turn-loop guard for "
            "this run (repeatable). The loop reverts to pre-guard behaviour at that point; a "
            "would-have-fired count is still logged in guards_suppressed. Used by "
            "scripts/run_guard_ablation.py to measure each guard's individual contribution "
            f"rather than only cumulatively. Choices: {', '.join(sorted(ABLATABLE_GUARDS))}."
        ),
    )
    parser.add_argument(
        "--same-path-guard",
        action="store_true",
        help=(
            "86bbcfv9d: re-enable the same-path-repeated-failure nudge (fires after "
            f"{SAME_PATH_FAILURE_THRESHOLD} failed edits on one path). Demoted from always-on to "
            "opt-in on 2026-08-30 after its individual ablation came back net-negative on "
            "turn-efficiency across two batches (n=240/condition). Kept as a tested reference. "
            "See docs/aci-guard-cluster-ablation.md."
        ),
    )
    args = parser.parse_args()

    if args.all:
        results = run_all_exercises(
            verbose=args.verbose,
            repeats=args.repeat,
            diff_format=args.diff_format,
            hybrid_verify=args.hybrid_verify,
            model=args.model,
            progress_framing=args.progress_framing,
            max_hybrid_nudges=args.max_hybrid_nudges,
            early_abandon=args.early_abandon,
            regression_guard=args.regression_guard,
            advisory_idiom=args.advisory_idiom,
            disabled_guards=frozenset(args.disabled_guards),
            same_path_guard=args.same_path_guard,
        )
        _print_summary(results)
    elif args.slug:
        result = run_exercise(
            args.slug,
            verbose=args.verbose,
            diff_format=args.diff_format,
            hybrid_verify=args.hybrid_verify,
            model=args.model,
            progress_framing=args.progress_framing,
            max_hybrid_nudges=args.max_hybrid_nudges,
            early_abandon=args.early_abandon,
            regression_guard=args.regression_guard,
            advisory_idiom=args.advisory_idiom,
            disabled_guards=frozenset(args.disabled_guards),
            same_path_guard=args.same_path_guard,
        )
        print(f"\n=== {result['slug']} ===")
        print(f"Status: {result['final_status']} ({result['turns_used']} turn(s) used)")
        print(f"Tests passed: {result['test_passed']}")
        print(f"Guard fires: {result['guard_fires']}")
        if result["disabled_guards"]:
            print(f"Disabled guards: {result['disabled_guards']}")
            print(f"  would have fired: {result['guards_suppressed']}")
        if result["hybrid_verify_enabled"]:
            print(f"Style-verifier (Claude) calls: {result['style_verifier_calls']}")
            print(f"GAMED rejections: {result['style_gamed_rejections']}")
            if result["style_idiom_note"]:
                accepted = " (accepted, advisory)" if result["advisory_idiom_enabled"] else ""
                print(f"IDIOM note{accepted}: {result['style_idiom_note']}")
        if result["early_abandon_enabled"]:
            print(f"Best test-pass fraction seen: {result['best_pass_fraction_seen']:.3f}")
        if result["regression_guard_enabled"]:
            print(f"Snapshot restored: {result['snapshot_restored']}")
        print(f"\n--- Test output ---\n{result['test_output']}")
    else:
        parser.error("Provide a slug or use --all.")
