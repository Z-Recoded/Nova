# nova_orchestrator_runpod.py
# RunPod (Qwen2.5-Coder-32B-Instruct-AWQ) backend for nova_orchestrator.py's
# coding sub-agent turn loop -- an alternative to the Claude-API-backed
# inline loop and the LangGraph port (nova_orchestrator_graph.py).
#
# Only imported by nova_orchestrator.py when framework_integrations.
# runpod_coding_agent is enabled in nova_config.json -- importing this
# module never happens when the flag is off (the default).
#
# Why a separate mechanism from the Claude/LangGraph paths: this RunPod
# endpoint (nova_remote_inference.py) has no native tool-calling API at all
# -- it's RunPod's raw /runsync vLLM-worker job schema, not an
# OpenAI-compatible chat-completions route, and no reliable built-in vLLM
# tool-call parser exists for Qwen2.5-Coder specifically (open vLLM issue
# #32926 -- the usual "hermes" parser expects <tool_call> tags this model
# doesn't reliably produce). Tool calls are instead requested via a prompted
# <tools>{"name": ..., "arguments": {...}}</tools> text format -- the exact
# convention that issue's own testing showed ~100% compliant for this
# model/quantization -- and parsed out of the plain-text response manually.
#
# This mechanism, the two safety guards below, and the JSON-parsing fallback
# chain were all proven live in nova_runpod_toolcall_spike.py before being
# promoted here: a clean re-run of 5 real historical tasks scored 4/5 fully
# correct (verified via git diff), 1/5 with one reproducible minor defect,
# 0 destructive edits, 0 hallucinations, 0 stalls.

import ast
import json
import os
import re
from datetime import datetime

from vulture import Vulture

import nova_remote_inference
from nova_backend_profiles import RUNPOD_PROFILE
from nova_completion_gate import check_ground_truth_completion
from nova_config import is_framework_integration_enabled
from nova_laminar_client import log_guard_events as laminar_log_guard_events
from nova_laminar_client import log_turn as laminar_log_turn
from nova_langfuse_client import log_guard_events, log_turn
from nova_orchestrator import _git_diff_against_master

# ── Config ─────────────────────────────────────────────────────

# Matches nova_query.py's own NUM_CTX -- accepted by nova_remote_inference.chat()
# for interface parity with Ollama but not actually forwarded to the RunPod
# request (context-window size is a property of the deployed model, not a
# per-request param there).
NUM_CTX = 8192

# A single write_file tool call can carry a whole file's contents as its
# argument -- several thousand tokens on its own for a file the size of
# nova_api.py. Matches NOVA_AGENT_MAX_TOKENS's own reasoning in
# nova_orchestrator.py. Real bug found and fixed 2026-07-27 in
# nova_remote_inference.py before this mattered: max_tokens was previously
# silently ignored by the RunPod endpoint regardless of what was requested
# (payload schema mismatch, now fixed) -- confirmed this parameter now
# genuinely controls real output length.
CODING_AGENT_MAX_OUTPUT_TOKENS = 8192

# Real, tested format from vLLM issue #32926 / PR #32931's proposed
# qwen2_5_coder tool parser -- <tools>{"name": ..., "arguments": {...}}</tools>,
# parallel calls as multiple back-to-back <tools> blocks. Delivered as plain
# system-prompt text (few-shot, not a real chat-template change) since this
# RunPod deployment doesn't have that custom tool-parser/template installed.
TOOLS_FORMAT_PROMPT = (
    "\n\n---\n"
    "TOOL-CALL FORMAT: this deployment has no native function-calling -- to "
    "call a tool, output EXACTLY this format (a real, tested convention, "
    "not an arbitrary choice):\n\n"
    "<tools>\n"
    '{"name": "read_file", "arguments": {"path": "nova_api.py"}}\n'
    "</tools>\n\n"
    "To call more than one tool in the same turn, use multiple <tools>...</tools> "
    "blocks back to back. Do NOT use ```json code blocks. Do NOT use <tool_call> "
    "tags. Use exactly <tools>...</tools> as shown. After a tool result arrives "
    "(wrapped in <tool_response>...</tool_response>), continue the task. When "
    "you are completely finished, reply with a plain-text summary and NO "
    "<tools> block at all.\n\n"
    "Available tools and their exact argument shapes:\n"
    '- read_file: {"path": "<file path, relative to worktree root>"}\n'
    '- write_file: {"path": "<file path>", "content": "<full file contents>"}\n'
    '- file_replace: {"path": "<file path>", "old_str": "<exact text, must appear '
    'exactly once>", "new_str": "<replacement text>"}\n'
    '- list_files: {"path": "<directory path, use \'.\' for the worktree root>"}\n'
    '- run_command: {"cmd": "<shell command>"}'
)

# Real failure modes observed live before this guard existed: the model
# calling write_file/file_replace on a file it had never read (once
# destructively overwriting a real 1644-line file with a guessed 7-line
# stub), and separately re-reading an already-read file instead of
# proceeding to the edit.
# Self-verification affordance (86bb71x2a). Deliberately a soft nudge, not
# a hard requirement -- research cited on this ticket found reasoning
# models tend to produce traces that "rationalize completion rather than
# verify it," so self-checking is a real improvement over nothing but not
# a sufficient defense on its own. The authoritative backstop is the
# harness-level ground-truth gate (nova_completion_gate.py), which doesn't
# rely on the model reporting on itself at all -- this prompt is the
# complementary, cheaper first line of defense.
SELF_VERIFICATION_PROMPT = (
    "\n\n---\n"
    "SELF-VERIFICATION: once you believe your edits are complete, do not "
    "immediately stop. First call run_command to check your own work -- e.g. "
    "a Python syntax check on every .py file you edited (python -m py_compile "
    "<path>), or re-read a file you claim to have created to confirm it's "
    "really there. Only respond with a final summary and no <tools> block "
    "after you've done this. This matters because prior runs have reported "
    "being done while a file was left with a real syntax error, or a claimed "
    "edit was never actually applied -- catching that yourself here is much "
    "cheaper than a human finding it later."
)

READ_BEFORE_WRITE_GUARD_PROMPT = (
    "\n\n---\n"
    "HARD RULE (safety guard): before calling write_file or file_replace on a "
    "path that already exists in this worktree, you MUST call read_file on "
    "that exact path first. Never guess a file's existing contents. If you "
    "try to edit an existing file without having read it first, the tool "
    "call will be refused and you will be told to read it first, instead.\n\n"
    "Once you have read a file, its contents will not change again this "
    "task -- do NOT call read_file on the same path a second time, and do "
    "NOT respond with a summary, explanation, or analysis of what you read. "
    "A read_file call exists only so your next tool call (write_file or "
    "file_replace) is well-informed -- make that edit immediately, in your "
    "very next turn. This task asks you to make a change, not explain code."
)


# ── System prompt ────────────────────────────────────────────────

# Real bug found live: nova_orchestrator._build_system_prompt() bakes the
# full CLAUDE.md in verbatim (~58,000 characters, ~14,500 tokens on its
# own) -- on this endpoint's 32,768-token context window, that alone left
# too little room for a real multi-turn task once the task description,
# tool-call/response content, and the 8192 reserved output tokens were also
# counted (confirmed live: a genuine feature-build task failed outright,
# "your prompt contains at least 32769 input tokens"). This condensed
# summary covers the same essential rules (CLAUDE.md Sections 3/4/9) in a
# fraction of the space -- used only by this backend; the Claude/LangGraph
# paths keep the full CLAUDE.md unchanged.
CONDENSED_CODING_STANDARDS = (
    "Nova coding standards (condensed -- the full project CLAUDE.md is too "
    "large for this model's context window, so only the essential rules "
    "are included here):\n\n"
    "Legibility first: code must be easy to read, understand, troubleshoot, "
    "and edit by a human at any time. Prioritize this over cleverness, "
    "brevity, or performance.\n"
    "- One job per function. If a function does two things, split it.\n"
    "- Name things like sentences: retrieve_with_graph(query), not ret(q).\n"
    "- Plain English comment above every function describing what it does "
    "and why it exists.\n"
    "- No magic numbers -- every constant gets a named variable "
    "(NUM_CTX = 8192, not a bare 8192).\n"
    "- No clever one-liners -- if it takes more than a moment to parse, "
    "rewrite it as steps.\n"
    "- Explicit over implicit. Write what you mean.\n"
    "- Avoid deep nesting -- use early returns and guard clauses.\n\n"
    "Python style: snake_case for variables/functions, PascalCase for "
    "classes, SCREAMING_SNAKE_CASE for constants. Type hints on function "
    "signatures. Imports grouped stdlib -> third-party -> local. Private "
    "helpers prefixed with a single underscore.\n\n"
    "Never do: refactor code that isn't part of the task; rename things "
    "without a clear reason; add new pip/npm dependencies casually; write "
    "to the Second Brain Obsidian vault (read-only, always); call Chroma "
    "or Ollama directly instead of going through the existing FastAPI "
    "routes; leave a TODO comment unimplemented; optimize prematurely."
)


def build_condensed_system_prompt() -> str:
    """
    RunPod-backend system prompt: the same worktree-scoped tool-use
    guidance nova_orchestrator._build_system_prompt() gives every backend,
    with CONDENSED_CODING_STANDARDS in place of the full CLAUDE.md. No
    file read needed (unlike the Claude path) since nothing here depends
    on the specific worktree.
    """
    return (
        "You are Nova's coding sub-agent, operating inside a disposable git "
        "worktree. You have file read/write/list tools and a run_command tool, "
        "all scoped to this worktree only — nothing you do here touches the "
        "live Nova codebase until a human reviews and merges your branch. "
        "run_command executes via Git Bash, so use Unix-style commands (ls, "
        "grep, cat), not cmd.exe/PowerShell syntax. Your worktree has no "
        "virtualenv of its own (nova-env/ isn't git-tracked) — plain `python` "
        "and `pip` in run_command already resolve to the live project's venv, "
        "so just use them directly; no need to hunt for an interpreter path. "
        "All file edits must go through write_file or file_replace, scoped to "
        "this worktree. For edits to a file that already exists, prefer "
        "file_replace over write_file — it only sends the changed "
        "old_str/new_str pair as output instead of the whole file. old_str "
        "must match exactly once; if it doesn't, either pick a larger, more "
        "specific old_str or fall back to write_file. Reserve write_file for "
        "brand-new files.\n\n"
        "Read and follow the project's own coding standards below exactly:\n\n"
        f"{CONDENSED_CODING_STANDARDS}\n\n"
        "---\n"
        "You have a limited number of turns. Prefer writing files directly "
        "with write_file over exploring the shell environment — don't spend "
        "turns probing tool availability or paths defensively; write the "
        "code, then verify it with one focused run_command call. Work the "
        "task to completion within your available turns. When finished, "
        "reply with a short plain-text summary of what you changed and why "
        "— no more tool calls after that summary."
    )


# Extracts <tools>...</tools> content, tolerating an unclosed trailing tag
# (the model's final block sometimes never gets a closing tag before it
# stops generating).
_TOOLS_BLOCK_RE = re.compile(r"<tools>(.*?)(?:</tools>|\Z)", re.DOTALL)


# ── Tool-call parsing ────────────────────────────────────────────


def _parse_one(raw: str):
    """
    Try, in order: strict JSON, lenient JSON, then Python's own literal
    parser. ast.literal_eval recovers two real, verified defect classes
    json.loads can never parse under any strictness setting: single-quoted
    string values (valid Python, not valid JSON -- happens when a value
    needs to contain a literal double-quote and the model switches
    delimiter instead of escaping it, e.g. "new_str": 'CODING_AGENT_PREFIX =
    "/code "...'), and trailing '# comment' text (valid Python comment
    syntax, meaningless to JSON). Confirmed live 2026-08-02 against two real
    captured near-miss bursts before this was built -- not a theoretical
    fallback. Returns the parsed value, or None if every parser failed.
    """
    for parser in (
        lambda s: json.loads(s, strict=True),
        lambda s: json.loads(s, strict=False),
        ast.literal_eval,
    ):
        try:
            return parser(raw)
        except (json.JSONDecodeError, ValueError, SyntaxError):
            continue
    return None


def _parse_tool_json(raw: str) -> tuple[list[dict], list[str]]:
    """
    Returns (calls, failed_fragments) -- real bug found live 2026-08-02: the
    previous JSONL-per-line fallback aborted the ENTIRE burst the instant
    any single line failed to parse, discarding every other genuinely
    well-formed call alongside it (verified against a real 4-line burst: 3
    valid lines were lost because 1 line carried a trailing comment). This
    now recovers whatever parses at each level -- only fragments that fail
    every parser in _parse_one() (whole-block, then per-line) come back as
    failed_fragments for the caller to flag as a near-miss.
    """
    raw = raw.strip()

    whole = _parse_one(raw)
    if isinstance(whole, dict):
        return [whole], []
    if isinstance(whole, list):
        return [item for item in whole if isinstance(item, dict)], []

    calls: list[dict] = []
    failed: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed_line = _parse_one(line)
        if isinstance(parsed_line, dict):
            calls.append(parsed_line)
        else:
            failed.append(line)
    return calls, failed


def _parse_tool_calls(content: str) -> tuple[list[dict], list[str]]:
    """
    Extract every <tools>...</tools> block from a response and parse each
    into tool-call dicts. Returns (calls, near_misses).

    near_misses (86bb71x1j, Level 3 -- lenient parsing with visible
    near-misses) is the raw text of any fragment that produced zero usable
    tool-call dicts, even after _parse_tool_json()'s full recovery chain. A
    <tools> block existing at all means the model was clearly attempting a
    tool call -- previously a parse failure here was silently dropped,
    indistinguishable from the model genuinely having nothing left to do.
    That silence is the same shape as the E1/G2 false-completion failure (a
    stopped turn misread as real completion), just from a different root
    cause -- surfacing it lets the caller react instead of falsely closing
    out the task. A single block can now yield BOTH real calls and
    near-misses at once (partial recovery), unlike before this fix.
    """
    calls: list[dict] = []
    near_misses: list[str] = []
    for raw_block in _TOOLS_BLOCK_RE.findall(content):
        block_calls, block_failed = _parse_tool_json(raw_block)
        calls.extend(block_calls)
        near_misses.extend(block_failed)
    return calls, near_misses


# ── Tool dispatch, guarded ───────────────────────────────────────


def _worktree_has_file(root: str, path: str) -> bool:
    return os.path.isfile(os.path.join(root, path))


def _call_key(name: str, args: dict) -> tuple:
    """Hashable identity for one tool call, used to detect an exact repeat."""
    return (name, json.dumps(args, sort_keys=True, default=str))


# Functions shorter than this are excluded from duplicate detection -- a
# handful of trivial one- or two-line helpers/properties coincidentally
# matching isn't the failure mode this guards against.
MIN_DUPLICATE_FUNCTION_STATEMENTS = 3


def _normalized_function_bodies(source: str) -> dict[str, list[str]]:
    """
    Parse `source` as Python and return {qualified_name: [normalized_body, ...]}
    for every function/method with at least MIN_DUPLICATE_FUNCTION_STATEMENTS
    statements in its body. A qualified name with more than one entry means
    that name was defined more than once in this file. Returns {} if the
    file isn't valid Python (e.g. a non-Python path, or content mid-edit
    with a real syntax error that will surface elsewhere) -- this guard is
    Python-source-only, not a general-purpose parser.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    bodies: dict[str, list[str]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = f"{prefix}{child.name}"
                if len(child.body) >= MIN_DUPLICATE_FUNCTION_STATEMENTS:
                    normalized = "\n".join(ast.dump(statement) for statement in child.body)
                    bodies.setdefault(qualified_name, []).append(normalized)
                visit(child, prefix=f"{qualified_name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix=f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, prefix="")
    return bodies


def _find_duplicate_functions(source: str) -> list[str]:
    """
    Real names of functions/methods that either (a) share an identical
    normalized body with a DIFFERENT function elsewhere in the file, or
    (b) are defined more than once under the same qualified name -- the two
    concrete shapes of the "leftover duplicate after file_replace" defect
    observed in the 2026-07-27 held-out eval (project_qwen3_coding_spike_
    result.md: 3 of 6 tasks left old code behind next to new, similar code).
    Returns a sorted, deduped list of offending names (empty if none found).
    """
    bodies = _normalized_function_bodies(source)
    flagged: set[str] = set()
    seen_bodies: dict[str, str] = {}
    for name, body_list in bodies.items():
        if len(body_list) > 1:
            flagged.add(name)
            continue
        body = body_list[0]
        earlier_name = seen_bodies.get(body)
        if earlier_name is not None and earlier_name != name:
            flagged.add(name)
            flagged.add(earlier_name)
        else:
            seen_bodies[body] = name
    return sorted(flagged)


def _find_unreachable_code(path_on_disk: str) -> list[str]:
    """
    Real dead statements left after a return/break/continue/raise -- the C2
    failure-registry entry, a different shape from _find_duplicate_functions()'s
    whole-duplicate-function class and one that reproduced twice against the
    real held-out eval even after that guard shipped (2026-07-29's tasks 4/6:
    an old function's body pasted below a `return`, not re-defined as a whole
    second function). Verified live before building this (86bb72wer): vulture
    reports this exact defect as a distinct item.typ == "unreachable_code" at
    a flat 100% confidence -- deliberately the ONLY item type read here.
    Everything else vulture reports (unused function/variable/import, ~60%
    confidence) needs whole-project call-graph context to be reliable and
    would false-positive on nearly every function in a single freshly-edited
    file, so it's ignored entirely. Needs a real on-disk path, not a source
    string -- fine here, since the edit has already landed by the time this
    post-dispatch check runs.
    """
    v = Vulture()
    v.scavenge([path_on_disk])
    return [item.get_report() for item in v.get_unused_code() if item.typ == "unreachable_code"]


# Real observed loop (86bb728nj, found while auditing the 2026-08-01 held-out
# eval): the model repeatedly re-attempts file_replace against the same path
# after earlier attempts on that same path already failed, instead of
# switching strategy. Reproduced on both the fine-tuned and the stock model
# -- the stock model eventually discovered the write_file fallback on its
# own (by turn 5 of an 8-turn capped test run) and recovered; the fine-tuned
# model never did within its full 25-turn budget and stalled out entirely.
# Rather than only detecting/refusing the loop (the existing failed_calls
# guard below already does that for an *exact* repeat), proactively tell the
# model the way out once a path has accumulated this many real failures --
# cheaper and more reliable than waiting for the model to rediscover it.
FILE_REPLACE_FALLBACK_THRESHOLD = 2


def _maybe_suggest_write_file_fallback(content: str, path: str, failed_replace_counts: dict) -> tuple[str, bool]:
    """
    Appends a nudge toward write_file to `content` once `path` has
    accumulated FILE_REPLACE_FALLBACK_THRESHOLD or more failed file_replace
    attempts this task run -- see FILE_REPLACE_FALLBACK_THRESHOLD's own
    comment for why. Returns `(content, False)` unchanged below that
    threshold; the bool return (added for guard-firing attribution) tells a
    caller whether the nudge was genuinely appended, since incrementing the
    counter and this function being called at all don't by themselves mean
    the guard fired.
    """
    count = failed_replace_counts.get(path, 0)
    if count < FILE_REPLACE_FALLBACK_THRESHOLD:
        return content, False
    return (
        f"{content}\n\nYou have now failed file_replace on '{path}' {count} time(s) in this "
        f"task. Stop guessing at old_str -- call write_file('{path}', ...) with the complete "
        f"corrected file contents instead."
    ), True


# Real observed loop (86bb72wdx, generalizing the 86bb728nj/B3 nudge above):
# a SINGLE refused file_replace against a path that doesn't exist at all can
# never be fixed by a better old_str guess, no matter how many times it's
# retried -- there is nothing to match against. The 2026-07-27 held-out
# eval's task 1 hit exactly this: one refused file_replace on a path that
# didn't exist yet, then the model never tried write_file at all across the
# rest of its budget -- seven unproductive `ls` calls and an unrelated
# throwaway file instead. FILE_REPLACE_FALLBACK_THRESHOLD's nudge never
# fired because the model never repeated the SAME failing call twice; it
# just wandered off. Unlike that threshold (which stays at 2 for the
# "file exists but old_str keeps not matching" case -- a real edit might
# still be one better guess away there), a missing target file has no such
# ambiguity, so this nudges on the very first occurrence.
def _suggest_write_file_for_missing_target(content: str, path: str) -> str:
    """Nudge toward write_file immediately when file_replace failed because `path` doesn't exist at all."""
    return (
        f"{content}\n\n'{path}' doesn't exist in this worktree yet -- file_replace can never "
        f"succeed against a file that isn't there, no matter how old_str is reworded. Call "
        f"write_file('{path}', ...) to create it instead."
    )


# Goal re-anchoring (86bb72wfm): as a run's context fills with tool-call
# output, the original task statement -- still technically in context, never
# dropped -- becomes relatively less salient than the accumulated turn-by-
# turn detail around it. Real incident this targets: qwen3:8b, think=True,
# drifted off the actual task after ~2 turns into a fabricated unrelated one
# ("add .rst extension support"), then destructively overwrote a real file
# in service of the hallucinated task. F1's prompt-only fix attempt for a
# related problem (over-explaining instead of editing) didn't hold up even
# after explicit "stay focused" wording, so this is deliberately structural
# instead: the original task is re-injected verbatim on a fixed cadence,
# independent of whether the model would think to re-read it on its own.
GOAL_REANCHOR_INTERVAL_TURNS = 6


def _goal_reanchor_note(task_description: str) -> str:
    """Verbatim restatement of the original task, appended periodically so it stays salient."""
    return (
        f"\n\n<reminder>\nYour original task, restated in full (context has filled with tool "
        f"output since it was last shown):\n{task_description}\n</reminder>"
    )


# Task-scoped file allowlist (86bb72wd5): the single most severe entry in
# the failure registry (D1) was the model disregarding an explicit
# "preserve all existing behavior, only touch X" instruction and rewriting
# unrelated working code from scratch, deleting a live RAG pipeline in the
# process. The ground-truth completion gate's narrow-scope check (see
# nova_completion_gate._check_narrow_scope_not_exceeded) catches this
# AFTER the fact, from the diff. This is the pre-action complement: refuse
# a write attempt against a path the task's own spec never named at all,
# before the damage happens, matching the same deny-before-action pattern
# VS Code's agent sandboxing, OpenAI Codex's FileSystemSandboxPolicy, and
# Claude Code's own sandboxed Bash tool all use.
#
# Deliberately file-EXISTENCE-scoped, not edit-SIZE-scoped: it stops a write
# to a file the task never mentioned, but does not (and structurally
# cannot, pre-action) limit how much of an explicitly-named file gets
# rewritten -- that remains the post-hoc narrow-scope check's job. The two
# are complementary, not duplicates.
def _in_scope_basenames(requirements: dict | None) -> set | None:
    """
    Basenames of every file extract_task_requirements() found explicitly
    named in the task spec (required_files + narrow_scope_files) -- the
    declared write allowlist for this task. Returns None (meaning: no
    allowlist enforced, fail open) when `requirements` is None or the task
    didn't name any specific files at all -- same fail-open discipline as
    extract_task_requirements() itself: an under-populated extraction
    should silently skip this guard, not block otherwise-legitimate work on
    a loosely-scoped task ("explore and fix the bug") that never named
    files up front.
    """
    if not requirements:
        return None
    names = {os.path.basename(f.strip()) for f in requirements.get("required_files", []) if f.strip()}
    names |= {os.path.basename(f.strip()) for f in requirements.get("narrow_scope_files", []) if f.strip()}
    return names or None


# Guard-firing attribution: every guard below has been iterated on blind all
# week -- readable error text a human happens to notice while reading a
# transcript, but no record anywhere of which specific guard actually fired
# in a given run. These ids give each firing point a stable, greppable
# identity so nova_guard_stats.py can tally which fixes are actually pulling
# their weight across re-runs, instead of that being re-derived by hand every
# time (see nova_guard_stats.py's own header for the full rationale).
GUARD_REPEAT_FAILED_CALL = "repeat_failed_call"
GUARD_FILE_ALLOWLIST = "file_allowlist"
GUARD_READ_BEFORE_WRITE = "read_before_write"
GUARD_REPEAT_READ = "repeat_read"
GUARD_CONTENT_SYNTAX_INVALID = "content_syntax_invalid"
GUARD_CONTENT_DUPLICATE_FUNCTION = "content_duplicate_function"
GUARD_CONTENT_UNREACHABLE_CODE = "content_unreachable_code"
GUARD_WRITE_FILE_NUDGE_MISSING_TARGET = "write_file_nudge_missing_target"
GUARD_WRITE_FILE_NUDGE_THRESHOLD = "write_file_nudge_threshold"
GUARD_NEAR_MISS_PARSE = "near_miss_parse"
GUARD_GOAL_REANCHOR = "goal_reanchor"
GUARD_SELF_VERIFY_NUDGE = "self_verify_nudge"


def _execute_tool_guarded(
    name: str,
    args: dict,
    root: str,
    session_id: str,
    task_description: str,
    read_paths: set,
    failed_calls: set,
    failed_replace_counts: dict,
    in_scope_basenames: set | None = None,
    guard_events: list | None = None,
) -> dict:
    """
    Wraps nova_orchestrator._execute_tool() (deferred import -- avoids
    import-time circularity with nova_orchestrator.py lazily importing this
    module) with five extra checks: refuse write_file/file_replace on a path
    outside the task's own declared file scope, when one was extracted
    (86bb72wd5 -- see _in_scope_basenames()'s own comment); refuse
    write_file/file_replace on a path that already exists on disk but
    hasn't been read_file'd yet this task run; refuse a second read_file on
    a path already read (closes a real observed loop: the model re-reading
    an already-read file up to 8 times instead of proceeding to the edit);
    refuse an exact repeat of a call that already failed (closes a second
    real observed loop: the model retrying an identical failing file_replace
    8 times in a row -- e.g. targeting an old_str for a route in a module it
    never actually wrote -- instead of recognizing the missing step); and,
    once a path has failed file_replace FILE_REPLACE_FALLBACK_THRESHOLD+
    times (not necessarily via an *exact* repeat -- a third real loop shape,
    86bb728nj, where each attempt targets a slightly different old_str
    against the same stuck path), append a proactive write_file suggestion
    to the error content. The first four checks return a synthetic is_error
    result rather than dispatching -- list_files/run_command are never
    gated by the scope/read-before-write checks (run_command repeats can
    still legitimately fail the same way twice for a different reason, so
    only the failed-repeat check applies to it).

    guard_events (default None, backward-compatible): a caller-owned list
    this function appends {"guard": <GUARD_* id>, "detail": ...} to every
    time one of the checks above actually fires -- see the GUARD_* constants'
    own comment for why. None means "don't track" (a no-op guard).
    """
    if guard_events is None:
        guard_events = []
    key = _call_key(name, args)
    path = args.get("path", "")

    if key in failed_calls:
        guard_events.append({"guard": GUARD_REPEAT_FAILED_CALL, "detail": f"{name} {path}"})
        content = (
            f"Refused: you already tried this exact {name} call and it failed. Repeating "
            f"it verbatim will fail again for the same reason. If you're editing content "
            f"that doesn't exist yet, write it first with write_file instead of guessing at "
            f"file_replace's old_str. Take a genuinely different next step."
        )
        if name == "file_replace":
            # Counts toward the same fallback threshold as a fresh dispatch
            # failure below -- otherwise a model that repeats the *exact*
            # same failing call, rather than varying old_str each time,
            # would never accumulate enough failures to trigger the nudge.
            failed_replace_counts[path] = failed_replace_counts.get(path, 0) + 1
            content, nudge_fired = _maybe_suggest_write_file_fallback(content, path, failed_replace_counts)
            if nudge_fired:
                guard_events.append({"guard": GUARD_WRITE_FILE_NUDGE_THRESHOLD, "detail": path})
        return {"content": content, "is_error": True}

    if name in ("write_file", "file_replace") and path and in_scope_basenames is not None:
        if os.path.basename(path) not in in_scope_basenames:
            guard_events.append({"guard": GUARD_FILE_ALLOWLIST, "detail": path})
            return {
                "content": (
                    f"Refused: '{path}' was not named in this task's spec as a file to create or "
                    f"modify. This task is scoped to a specific set of files -- if you genuinely "
                    f"need to touch a different file to complete it, explain why in your final "
                    f"summary instead of editing it directly."
                ),
                "is_error": True,
            }

    if name in ("write_file", "file_replace"):
        if path and _worktree_has_file(root, path) and path not in read_paths:
            guard_events.append({"guard": GUARD_READ_BEFORE_WRITE, "detail": path})
            return {
                "content": (
                    f"Refused: '{path}' already exists and has not been read this session. "
                    f"Call read_file('{path}') first, then retry your edit."
                ),
                "is_error": True,
            }

    if name == "read_file" and path in read_paths:
        guard_events.append({"guard": GUARD_REPEAT_READ, "detail": path})
        return {
            "content": (
                f"You already read '{path}' earlier -- its contents have not "
                f"changed. Do not call read_file on it again. Make your edit now with "
                f"write_file or file_replace."
            ),
            "is_error": True,
        }

    from nova_orchestrator import _execute_tool

    result = _execute_tool(name, args, root, session_id=session_id, task_description=task_description)
    if result.get("is_error", False):
        failed_calls.add(key)
        if name == "file_replace":
            failed_replace_counts[path] = failed_replace_counts.get(path, 0) + 1
            if not _worktree_has_file(root, path):
                # 86bb72wdx: no old_str rewording can ever fix a file_replace
                # against a path that doesn't exist -- nudge on this very
                # first failure rather than waiting for
                # FILE_REPLACE_FALLBACK_THRESHOLD to accumulate.
                guard_events.append({"guard": GUARD_WRITE_FILE_NUDGE_MISSING_TARGET, "detail": path})
                result = {**result, "content": _suggest_write_file_for_missing_target(result["content"], path)}
            else:
                nudge_content, nudge_fired = _maybe_suggest_write_file_fallback(
                    result["content"], path, failed_replace_counts
                )
                if nudge_fired:
                    guard_events.append({"guard": GUARD_WRITE_FILE_NUDGE_THRESHOLD, "detail": path})
                result = {**result, "content": nudge_content}
    elif name == "file_replace" and not path.endswith(".py"):
        # Non-.py files skip the content-validity check below entirely (no
        # syntax/duplicate-function check applies), so this is the only
        # place their success resets the counter. .py files reset inside
        # that check instead, only once confirmed genuinely valid.
        failed_replace_counts.pop(path, None)
    if name == "read_file" and not result.get("is_error", False):
        read_paths.add(path)

    # Post-dispatch content-validity check (unlike the three checks above):
    # the edit has already landed on disk by the time this fires, so a bad
    # result here can't be refused pre-dispatch -- it can only be reported.
    # Only file_replace on a .py path is checked: write_file is a deliberate
    # full-file rewrite (not the partial-content-substitution failure mode
    # this guards against), and non-Python files can't be parsed here anyway.
    #
    # Real gap closed here, found live 2026-08-01 (86bb72gpa): a file_replace
    # call that matches its old_str exactly always reports success, even if
    # the substituted content is syntactically broken (e.g. missing
    # indentation) or leaves a leftover duplicate function behind. Because
    # success/failure accounting above only looks at whether the TOOL CALL
    # itself errored, a model could run its own syntax check (86bb71x2a),
    # find the same real bug three turns in a row, and never accumulate
    # enough recorded failures to trigger the write_file fallback nudge
    # (86bb728nj) -- every file_replace call had "succeeded." Folding this
    # check's result into the same failed_calls/failed_replace_counts
    # accounting means a content-level defect now counts the same way a
    # dispatch failure already does, and gets the same corrective nudge.
    if name == "file_replace" and not result.get("is_error", False) and path.endswith(".py"):
        invalidity_reason = None
        try:
            new_source = open(os.path.join(root, path), encoding="utf-8").read()
        except OSError:
            new_source = None

        guard_fired = None
        if new_source is not None:
            try:
                ast.parse(new_source)
            except SyntaxError as e:
                invalidity_reason = f"the file no longer parses as valid Python: {e}"
                guard_fired = GUARD_CONTENT_SYNTAX_INVALID
            else:
                duplicates = _find_duplicate_functions(new_source)
                if duplicates:
                    invalidity_reason = (
                        f"it has what looks like leftover duplicate function(s): {', '.join(duplicates)} "
                        f"-- file_replace likely left the old version behind next to a new, similar one"
                    )
                    guard_fired = GUARD_CONTENT_DUPLICATE_FUNCTION
                else:
                    unreachable = _find_unreachable_code(os.path.join(root, path))
                    if unreachable:
                        invalidity_reason = (
                            f"it has what looks like unreachable dead code: {'; '.join(unreachable)} "
                            f"-- file_replace likely left old statements behind after a "
                            f"return/break/continue/raise"
                        )
                        guard_fired = GUARD_CONTENT_UNREACHABLE_CODE

        if invalidity_reason:
            guard_events.append({"guard": guard_fired, "detail": path})
            failed_calls.add(key)
            failed_replace_counts[path] = failed_replace_counts.get(path, 0) + 1
            content = (
                f"Warning: after this edit, '{path}' is not in a good state -- {invalidity_reason}. "
                f"Re-read the file, and either fix the specific problem directly or replace the whole "
                f"broken section with write_file before moving on."
            )
            content, nudge_fired = _maybe_suggest_write_file_fallback(content, path, failed_replace_counts)
            if nudge_fired:
                guard_events.append({"guard": GUARD_WRITE_FILE_NUDGE_THRESHOLD, "detail": path})
            return {"content": content, "is_error": True}

        # Only reached once the edit is confirmed syntax-valid and duplicate-
        # free -- a successful-but-still-broken edit (handled above) must
        # NOT reset this counter, or a model bouncing between two broken
        # variants of the same edit would never accumulate toward the
        # fallback threshold.
        failed_replace_counts.pop(path, None)

    return result


# ── Turn logging ─────────────────────────────────────────────────
#
# No usage-object adapter for nova_token_budget.record_usage() here
# (removed 2026-07-29, 86bb4gy0y punch-list item #5) -- that module's
# session/daily ceilings and cache-discount weighting are calibrated for
# Anthropic's per-token pricing, but RunPod actually bills per GPU-second of
# real execution time, not per token. Feeding real RunPod token counts
# through a budget model built for a different pricing structure produced a
# session/daily "budget %" with no relationship to real dollars spent --
# exactly the "stand-in" the punch list flagged, not a second, disagreeing
# number worth keeping alongside real tracking. Real cost now comes from
# nova_remote_inference.py's own execution-time-based cost_usd (see
# RUNPOD_GPU_HOURLY_RATE_USD there) via _log_runpod_cost_summary() below.
# The budget_gate_enabled halt-check earlier in run_via_runpod()'s loop is
# untouched -- that respects a shared, global halt state any caller
# (including the Claude lane) may have already tripped, which stays valid
# even though this backend no longer writes into that state itself.


def _log_agent_turn_runpod(
    slug: str,
    branch: str,
    turn: int,
    task: str,
    content: str,
    tool_calls: list[dict],
    usage: dict,
    skill_category: str | None,
    skill_version: str | None,
    pruned_pairs: int = 0,
    cost_usd: float | None = None,
    near_miss_count: int = 0,
) -> None:
    """
    Sibling to nova_orchestrator._log_agent_turn(), writing the identical
    agent_log.jsonl schema from this backend's real response shape instead
    of Anthropic SDK objects. tool_calls are re-keyed to {"name", "input"}
    (not "arguments") to stay consistent with nova_coding_dataset_curator.py's
    existing reader of this file. cache_* fields are explicitly None (no
    prompt caching on this endpoint, and no equivalent real-zero convention
    to borrow from since this backend no longer calls
    nova_token_budget.record_usage() at all -- a log entry should say "not
    applicable" honestly rather than imply caching happened and yielded 0).

    pruned_pairs (default 0, backward-compatible with any other caller):
    how many turn-pairs _prune_history_if_needed() removed before this
    turn's request was sent -- real, otherwise-invisible context-window
    pruning events, visible here for anyone reading eval transcripts later.

    cost_usd (default None, backward-compatible): this turn's real dollar
    cost from nova_remote_inference.py's execution-time-based calculation
    (86bb4gy0y punch-list item #5) -- None if the call failed before a
    cost could be computed.

    near_miss_count (default 0, backward-compatible; 86bb71x1j Level 3): how
    many <tools> blocks this turn produced zero usable tool-call dicts --
    see _parse_tool_calls()'s own docstring. stop_reason distinguishes this
    case ("near_miss") from a genuine "end_turn" so a reader of this log
    doesn't need to re-derive it from tool_calls being empty alone.
    """
    from nova_orchestrator import AGENT_LOG_PATH

    if tool_calls:
        stop_reason = "tool_use"
    elif near_miss_count:
        stop_reason = "near_miss"
    else:
        stop_reason = "end_turn"

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "turn": turn,
        "task": task,
        "skill_category": skill_category,
        "skill_version": skill_version,
        "stop_reason": stop_reason,
        "tool_calls": [{"name": c.get("name"), "input": c.get("arguments")} for c in tool_calls],
        "input_tokens": usage.get("prompt_eval_count"),
        "output_tokens": usage.get("eval_count"),
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "model": nova_remote_inference.MODEL_NAME,
        "backend_profile": RUNPOD_PROFILE.name,
        "pruned_pairs": pruned_pairs,
        "cost_usd": cost_usd,
        "near_miss_count": near_miss_count,
    }
    with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    del content  # not logged today, same as _log_agent_turn's own omission of response text


def _log_runpod_cost_summary(
    slug: str,
    branch: str,
    task: str,
    final_status: str,
    turns_used: int,
    total_cost_usd: float,
    total_execution_time_ms: int,
) -> None:
    """
    Append one real-cost summary for the whole task run to
    logs/runpod_cost_log.jsonl (86bb4gy0y punch-list item #5) -- the actual
    cost-accounting record the punch list asked for, replacing the removed
    fake-token-budget stand-in (_RunpodUsage, deleted above). Same
    JSONL-append pattern as nova_orchestrator._log_coding_review()/
    record_task_outcome().
    """
    from nova_orchestrator import LOGS_DIR

    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "task": task,
        "final_status": final_status,
        "turns_used": turns_used,
        "total_cost_usd": round(total_cost_usd, 6),
        "total_execution_time_ms": total_execution_time_ms,
    }
    with open(f"{LOGS_DIR}/runpod_cost_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_guard_events(slug: str, branch: str, task: str, final_status: str, guard_events: list) -> None:
    """
    Append one entry per task run to logs/guard_events_log.jsonl -- every
    guard/nudge that fired anywhere in this run (see the GUARD_* constants
    above), so nova_guard_stats.py can tally which fixes are actually
    pulling their weight across re-runs without anyone re-deriving it by
    hand from a transcript. Same JSONL-append, one-entry-per-task-run
    pattern as _log_runpod_cost_summary()/nova_orchestrator._log_ground_
    truth_gate() -- kept as its own log rather than folded into either,
    since this is neither a cost record nor a gate verdict.
    """
    from nova_orchestrator import LOGS_DIR

    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "task": task,
        "final_status": final_status,
        "guard_events": guard_events,
    }
    with open(f"{LOGS_DIR}/guard_events_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Observability Phase 2 (86bb7pazm) -- additive, tags the same events
    # onto Langfuse keyed to the real failure registry. Fails open on its
    # own; never disturbs the JSONL write above.
    log_guard_events(branch, final_status, guard_events)
    # 86bb7qudh: additive alongside Langfuse, same data, same fail-open
    # discipline -- see nova_laminar_client.log_guard_events()'s docstring.
    # Covers both the RunPod and Devstral backends, same as the Langfuse call.
    laminar_log_guard_events(branch, final_status, guard_events)


# ── Context-window management ────────────────────────────────────

# The real deployment ceiling for this endpoint (distinct from NUM_CTX above,
# which nova_remote_inference.chat() accepts for interface parity only and
# never actually forwards to the request -- confirmed by reading that
# function's own docstring). Cited in several comments elsewhere in this
# file; finally given a real name here.
CODING_AGENT_CONTEXT_WINDOW_TOKENS = 32768

# No live tokenizer call is available for this endpoint without an extra
# round trip -- this is a standard, documented rough approximation for
# English/code-mixed content, not exact tiktoken-level precision.
CHARS_PER_TOKEN_ESTIMATE = 4

# Buffer against the char-based estimate's real slop on code-heavy content --
# comfortably larger than the estimate's typical error margin at this scale.
CONTEXT_SAFETY_MARGIN_TOKENS = 2000


def _estimate_tokens(text: str) -> int:
    """Rough, cheap token-count estimate -- see CHARS_PER_TOKEN_ESTIMATE's own comment."""
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def _estimate_message_list_tokens(messages: list[dict]) -> int:
    """
    Sum of _estimate_tokens() across every message's content. Safe to assume
    a plain string here -- unlike Claude's structured content blocks, every
    message this backend builds (system prompt, task, assistant response,
    tool_response) has a plain-string content field.
    """
    return sum(_estimate_tokens(message["content"]) for message in messages)


def _prune_history_if_needed(messages: list[dict], max_output_tokens: int) -> int:
    """
    Proactively drop the oldest turn history from `messages` in place, so a
    request is never sent already knowing it will overflow this endpoint's
    real 32,768-token context window -- the real failure mode the 2026-07-27
    held-out eval hit mid-task (project_qwen3_coding_spike_result.md).

    messages[0] (the system prompt) and messages[1] (the original task) are
    never touched. From index 2 onward, messages are always built as strict
    (assistant, user-tool-response) pairs by run_via_runpod()'s own loop --
    removing whole pairs from the front preserves role alternation exactly,
    with no placeholder-message insertion needed. Stops once the estimated
    total fits under CODING_AGENT_CONTEXT_WINDOW_TOKENS minus the reserved
    output budget and safety margin, or once only one pair remains (pruning
    can't help a single turn that's too large on its own -- the caller
    checks for that case separately).

    If any pairs were pruned, prepends an honest note to the new earliest
    remaining pair's user-role (tool-response) content, so the model knows
    history was trimmed rather than silently losing context -- never the
    assistant-role message, which would put words in the model's own past
    turn that it never actually said. Returns the number of pairs pruned
    (0 if none were needed).
    """
    budget = CODING_AGENT_CONTEXT_WINDOW_TOKENS - max_output_tokens - CONTEXT_SAFETY_MARGIN_TOKENS
    pairs_pruned = 0

    while _estimate_message_list_tokens(messages) > budget and len(messages) > 4:
        del messages[2:4]
        pairs_pruned += 1

    if pairs_pruned:
        # messages[2] is the earliest remaining pair's assistant turn,
        # messages[3] is its user-role tool-response -- the note belongs on
        # the latter.
        note = f"[Note: {pairs_pruned} earlier turn(s) of tool output were removed to fit the context window.]\n\n"
        messages[3]["content"] = note + messages[3]["content"]

    return pairs_pruned


# ── Entry point used by nova_orchestrator.py ─────────────────────


def run_via_runpod(
    system_prompt: str,
    messages: list[dict],
    root: str,
    slug: str,
    branch_name: str,
    task_description: str,
    skill_category: str | None,
    skill_version: str | None,
    budget_gate_enabled: bool,
    max_turns: int,
    max_output_tokens: int,
    requirements: dict | None = None,
) -> tuple[str, int]:
    """
    Runs the turn loop via Nova's RunPod-hosted Qwen2.5-Coder-32B endpoint
    instead of Claude, using the prompted <tools>-tag format above. Same
    (final_status, turns_used) contract as run_via_langgraph(). No
    client/model params -- this endpoint has one deployed model and no SDK
    client object, unlike the Claude/LangGraph paths.

    requirements (86bb72wd5, default None -- backward-compatible): the
    extract_task_requirements() dict nova_orchestrator.run_coding_task()
    computes once up front for this backend, reused here to build the
    task-scoped file allowlist (_in_scope_basenames()) and passed through
    unchanged to check_ground_truth_completion() at the end of the task, so
    the extraction only ever runs once per task, not twice.
    """
    from nova_token_budget import get_budget_status

    in_scope_basenames = _in_scope_basenames(requirements)

    # Resolved once per task run, not per turn -- matches this loop's own
    # budget_gate_enabled parameter convention. When on, every real call
    # this turn loop makes routes through nova_remote_inference.
    # chat_with_logprobs() (the openai_route passthrough) instead of the
    # raw-schema chat(), so log_turn() below has real per-token logprobs to
    # report -- see chat_with_logprobs()'s own docstring for why the raw
    # schema can't supply them at all (a real, confirmed finding, not an
    # assumption). Off by default, so this endpoint's existing production
    # request shape is unchanged unless tracing is actually turned on.
    langfuse_tracing_enabled = is_framework_integration_enabled("langfuse_tracing")

    # `messages` arrives as [{"role": "user", "content": task_description}]
    # (nova_orchestrator.py never includes a system-role entry -- Claude's
    # system prompt is passed via a separate `system=` kwarg instead). This
    # backend's raw messages list has no equivalent slot, so the system
    # prompt is prepended here as a real system-role message.
    messages = [
        {
            "role": "system",
            "content": (
                system_prompt + TOOLS_FORMAT_PROMPT + READ_BEFORE_WRITE_GUARD_PROMPT + SELF_VERIFICATION_PROMPT
            ),
        },
        *messages,
    ]
    read_paths: set = set()
    failed_calls: set = set()
    failed_replace_counts: dict = {}
    edited_paths: set = set()
    verification_nudge_used = False
    total_cost_usd = 0.0
    total_execution_time_ms = 0
    # Guard-firing attribution: accumulated across the whole run (like
    # total_cost_usd above), not per-turn -- _log_agent_turn_runpod() below
    # is called before this turn's own tool-dispatch loop runs, so a given
    # turn's guard events don't exist yet at that point. Logged once at the
    # end via _log_guard_events(), same "one summary entry per task run"
    # precedent as _log_runpod_cost_summary()/_log_ground_truth_gate().
    guard_events: list = []

    final_status = "incomplete"
    turn = 0
    for turn in range(1, max_turns + 1):
        if budget_gate_enabled and get_budget_status().get("mode") == "halt":
            final_status = "stopped_budget_halt"
            break

        pairs_pruned = _prune_history_if_needed(messages, max_output_tokens)
        remaining_budget = CODING_AGENT_CONTEXT_WINDOW_TOKENS - max_output_tokens - CONTEXT_SAFETY_MARGIN_TOKENS
        if _estimate_message_list_tokens(messages) > remaining_budget:
            # Pruning already dropped every prunable pair and the request is
            # still over budget -- a single turn (e.g. one enormous
            # write_file call) is too large on its own, not fixable by more
            # pruning. Stop here rather than spend a paid call on a request
            # already known to overflow this endpoint's real context window.
            final_status = "stopped_context_overflow"
            break

        if langfuse_tracing_enabled:
            response = nova_remote_inference.chat_with_logprobs(messages, max_tokens=max_output_tokens)
        else:
            response = nova_remote_inference.chat(messages, num_ctx=NUM_CTX, max_tokens=max_output_tokens)
        if response is None:
            # Infra/network failure, not a model stop condition -- a distinct
            # string so a report reader can tell the two apart.
            final_status = "stopped_runpod_call_failed"
            break

        content = response["message"]["content"]
        tool_calls, near_misses = _parse_tool_calls(content)

        if near_misses:
            # Logged unconditionally now (real fix, 2026-08-02): a burst can
            # partially succeed since _parse_tool_json()'s recovery chain
            # was hardened -- some fragments parse into real tool_calls,
            # others still don't -- so this must fire even when tool_calls
            # also has entries, not just when the whole turn came back
            # empty (the retry-nudge message below still only fires in that
            # narrower case).
            guard_events.append(
                {"guard": GUARD_NEAR_MISS_PARSE, "detail": f"turn {turn}, {len(near_misses)} fragment(s)"}
            )

        total_cost_usd += response.get("cost_usd") or 0.0
        total_execution_time_ms += response.get("execution_time_ms") or 0

        _log_agent_turn_runpod(
            slug,
            branch_name,
            turn,
            task_description,
            content,
            tool_calls,
            response,
            skill_category,
            skill_version,
            pruned_pairs=pairs_pruned,
            cost_usd=response.get("cost_usd"),
            near_miss_count=len(near_misses),
        )
        log_turn(
            branch_name,
            turn,
            RUNPOD_PROFILE.name,
            nova_remote_inference.MODEL_NAME,
            content,
            tool_calls,
            response.get("prompt_eval_count"),
            response.get("eval_count"),
            logprobs=response.get("logprobs"),
            cost_usd=response.get("cost_usd"),
        )
        # 86bb7qudh: additive alongside Langfuse, same normalized data, same
        # fail-open discipline -- see nova_laminar_client.log_turn()'s docstring.
        laminar_log_turn(
            branch_name,
            turn,
            RUNPOD_PROFILE.name,
            nova_remote_inference.MODEL_NAME,
            content,
            tool_calls,
            response.get("prompt_eval_count"),
            response.get("eval_count"),
            logprobs=response.get("logprobs"),
            cost_usd=response.get("cost_usd"),
        )

        messages.append({"role": "assistant", "content": content})

        if not tool_calls:
            # This endpoint has no stop_reason concept -- a response cut off
            # by hitting max_output_tokens looks identical to a genuine
            # "no more tool calls, done" response (empty tool_calls either
            # way). Real bug found live: a truncated mid-generation response
            # was being read as "completed" here. eval_count landing at (or
            # past) the requested cap is the only signal available that
            # generation was cut off rather than finishing on its own.
            eval_count = response.get("eval_count")
            if eval_count is not None and eval_count >= max_output_tokens:
                final_status = "stopped_max_output_tokens"
                break

            if near_misses:
                # Lenient parsing / visible near-misses (86bb71x1j, Level 3):
                # a <tools> block existed but didn't parse -- the model
                # clearly attempted a tool call, so this is an unfinished
                # turn, not genuine completion. Directly targets the E1/G2
                # false-completion shape from a different angle: a
                # malformed attempt must never be silently indistinguishable
                # from "no more tool calls, done." Re-shown every occurrence
                # (not nudged-once like the checks below) since a parse
                # failure is a mechanical problem with a mechanical fix, not
                # a behavioral pattern that needs a single correction. Only
                # reached here when tool_calls is ALSO empty -- guard-event
                # logging itself already happened above regardless.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "<tool_response>\nYour last <tools> block did not parse as valid "
                            "tool-call JSON, so no tool was actually called. Re-send it using "
                            'EXACTLY this format:\n<tools>\n{"name": "...", "arguments": '
                            "{...}}\n</tools>\nwith valid JSON inside the tags.\n</tool_response>"
                        ),
                    }
                )
                continue

            if edited_paths and not verification_nudge_used:
                # Self-verification nudge (86bb71x2a), strengthened 2026-08-06: the
                # old condition just checked whether the model ran ANY command after
                # editing -- real bug found investigating why this almost never fired
                # (1 of 53 real runs) while 63.5% of un-nudged runs still failed an
                # incompleteness check anyway: a single unrelated syntax check already
                # satisfied the old condition, letting real incompleteness (missing
                # required files, dead imports, empty diff) sail through uncaught.
                # Reuses the exact same check the final gate runs
                # (check_ground_truth_completion(), nova_completion_gate.py) against
                # the real diff-so-far -- whatever this catches is guaranteed to
                # matter at the end too, and costs nothing extra since `requirements`
                # is already extracted once and passed through, not re-extracted here.
                diff_so_far = _git_diff_against_master(root)
                gate_result = check_ground_truth_completion(
                    diff_so_far, task_description, root, requirements=requirements
                )
                real_issues = gate_result["hard_fails"] + gate_result["warnings"]

                if real_issues:
                    # Give it exactly one more turn to address the SPECIFIC real
                    # issues found -- deliberately only once, so an ignored nudge
                    # doesn't burn the whole turn budget (same "nudge once, don't
                    # force forever" instinct as the file_replace fallback nudge
                    # above).
                    verification_nudge_used = True
                    guard_events.append(
                        {"guard": GUARD_SELF_VERIFY_NUDGE, "detail": f"turn {turn}: {'; '.join(real_issues)}"}
                    )
                    issues_text = "\n".join(f"- {issue}" for issue in real_issues)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "<tool_response>\nBefore finishing: a completeness check found "
                                f"real issues with your work so far:\n{issues_text}\nAddress "
                                "these specifically before your final summary.\n</tool_response>"
                            ),
                        }
                    )
                    continue

            final_status = "completed"
            break

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            result = _execute_tool_guarded(
                name,
                args,
                root,
                slug,
                task_description,
                read_paths,
                failed_calls,
                failed_replace_counts,
                in_scope_basenames,
                guard_events,
            )
            if name in ("write_file", "file_replace") and not result.get("is_error", False):
                edited_paths.add(args.get("path", ""))
            messages.append({"role": "user", "content": f"<tool_response>\n{result['content']}\n</tool_response>"})

        if turn % GOAL_REANCHOR_INTERVAL_TURNS == 0:
            # Goal re-anchoring (86bb72wfm) -- appended onto the last
            # tool-response message just added, not as a new message: the
            # pruning logic above depends on messages staying in strict
            # (assistant, user-tool-response) pairs.
            guard_events.append({"guard": GUARD_GOAL_REANCHOR, "detail": f"turn {turn}"})
            messages[-1]["content"] += _goal_reanchor_note(task_description)
    else:
        final_status = "max_turns_reached"

    _log_runpod_cost_summary(
        slug, branch_name, task_description, final_status, turn, total_cost_usd, total_execution_time_ms
    )
    _log_guard_events(slug, branch_name, task_description, final_status, guard_events)

    return final_status, turn
