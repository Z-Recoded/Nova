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

import nova_remote_inference

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


def _parse_tool_json(raw: str):
    """
    Fallback chain per vLLM PR #32931: single JSON object/array (tried both
    strict and lenient -- a real observed failure mode is the model
    embedding a literal newline inside a string value instead of a \\n
    escape, which strict JSON rejects as an invalid control character),
    else JSONL (one object per line).
    """
    raw = raw.strip()
    for strict in (True, False):
        try:
            return json.loads(raw, strict=strict)
        except json.JSONDecodeError:
            continue

    objects = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objects.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    return objects or None


def _parse_tool_calls(content: str) -> list[dict]:
    """Extract every <tools>...</tools> block from a response and parse each into tool-call dicts."""
    calls: list[dict] = []
    for raw_block in _TOOLS_BLOCK_RE.findall(content):
        parsed = _parse_tool_json(raw_block)
        if isinstance(parsed, dict):
            calls.append(parsed)
        elif isinstance(parsed, list):
            calls.extend(item for item in parsed if isinstance(item, dict))
    return calls


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


def _execute_tool_guarded(
    name: str, args: dict, root: str, session_id: str, task_description: str, read_paths: set, failed_calls: set
) -> dict:
    """
    Wraps nova_orchestrator._execute_tool() (deferred import -- avoids
    import-time circularity with nova_orchestrator.py lazily importing this
    module) with three extra checks: refuse write_file/file_replace on a
    path that already exists on disk but hasn't been read_file'd yet this
    task run; refuse a second read_file on a path already read (closes a
    real observed loop: the model re-reading an already-read file up to 8
    times instead of proceeding to the edit); and refuse an exact repeat of
    a call that already failed (closes a second real observed loop: the
    model retrying an identical failing file_replace 8 times in a row --
    e.g. targeting an old_str for a route in a module it never actually
    wrote -- instead of recognizing the missing step). All three return a
    synthetic is_error result rather than dispatching -- list_files/
    run_command and edits to brand-new paths are never gated by the first
    two checks (run_command repeats can still legitimately fail the same
    way twice for a different reason, so only the failed-repeat check
    applies to it).
    """
    key = _call_key(name, args)
    if key in failed_calls:
        return {
            "content": (
                f"Refused: you already tried this exact {name} call and it failed. Repeating "
                f"it verbatim will fail again for the same reason. If you're editing content "
                f"that doesn't exist yet, write it first with write_file instead of guessing at "
                f"file_replace's old_str. Take a genuinely different next step."
            ),
            "is_error": True,
        }

    if name in ("write_file", "file_replace"):
        path = args.get("path", "")
        if path and _worktree_has_file(root, path) and path not in read_paths:
            return {
                "content": (
                    f"Refused: '{path}' already exists and has not been read this session. "
                    f"Call read_file('{path}') first, then retry your edit."
                ),
                "is_error": True,
            }

    if name == "read_file" and args.get("path", "") in read_paths:
        already_read_path = args.get("path", "")
        return {
            "content": (
                f"You already read '{already_read_path}' earlier -- its contents have not "
                f"changed. Do not call read_file on it again. Make your edit now with "
                f"write_file or file_replace."
            ),
            "is_error": True,
        }

    from nova_orchestrator import _execute_tool

    result = _execute_tool(name, args, root, session_id=session_id, task_description=task_description)
    if result.get("is_error", False):
        failed_calls.add(key)
    if name == "read_file" and not result.get("is_error", False):
        read_paths.add(args.get("path", ""))

    # Post-dispatch check (unlike the three above): the edit has already
    # landed on disk by the time this fires, so it can't be refused -- it
    # can only tell the model to fix it on the next turn, same corrective-
    # nudge shape as the other guards. Only file_replace on a .py path is
    # checked: write_file is a deliberate full-file rewrite (not the
    # partial-content-substitution failure mode this guards against), and
    # non-Python files can't be parsed by _find_duplicate_functions() anyway.
    path = args.get("path", "")
    if name == "file_replace" and not result.get("is_error", False) and path.endswith(".py"):
        try:
            new_source = open(os.path.join(root, path), encoding="utf-8").read()
        except OSError:
            new_source = ""
        duplicates = _find_duplicate_functions(new_source)
        if duplicates:
            return {
                "content": (
                    f"Warning: after this edit, '{path}' has what looks like leftover duplicate "
                    f"function(s): {', '.join(duplicates)}. This usually means file_replace left "
                    f"the old version behind next to a new, similar one. Re-read the file, remove "
                    f"the stale version, and keep only the correct one before moving on."
                ),
                "is_error": True,
            }

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
    """
    from nova_orchestrator import AGENT_LOG_PATH

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "turn": turn,
        "task": task,
        "skill_category": skill_category,
        "skill_version": skill_version,
        "stop_reason": "tool_use" if tool_calls else "end_turn",
        "tool_calls": [{"name": c.get("name"), "input": c.get("arguments")} for c in tool_calls],
        "input_tokens": usage.get("prompt_eval_count"),
        "output_tokens": usage.get("eval_count"),
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "model": nova_remote_inference.MODEL_NAME,
        "pruned_pairs": pruned_pairs,
        "cost_usd": cost_usd,
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
) -> tuple[str, int]:
    """
    Runs the turn loop via Nova's RunPod-hosted Qwen2.5-Coder-32B endpoint
    instead of Claude, using the prompted <tools>-tag format above. Same
    (final_status, turns_used) contract as run_via_langgraph(). No
    client/model params -- this endpoint has one deployed model and no SDK
    client object, unlike the Claude/LangGraph paths.
    """
    from nova_token_budget import get_budget_status

    # `messages` arrives as [{"role": "user", "content": task_description}]
    # (nova_orchestrator.py never includes a system-role entry -- Claude's
    # system prompt is passed via a separate `system=` kwarg instead). This
    # backend's raw messages list has no equivalent slot, so the system
    # prompt is prepended here as a real system-role message.
    messages = [
        {"role": "system", "content": system_prompt + TOOLS_FORMAT_PROMPT + READ_BEFORE_WRITE_GUARD_PROMPT},
        *messages,
    ]
    read_paths: set = set()
    failed_calls: set = set()
    total_cost_usd = 0.0
    total_execution_time_ms = 0

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

        response = nova_remote_inference.chat(messages, num_ctx=NUM_CTX, max_tokens=max_output_tokens)
        if response is None:
            # Infra/network failure, not a model stop condition -- a distinct
            # string so a report reader can tell the two apart.
            final_status = "stopped_runpod_call_failed"
            break

        content = response["message"]["content"]
        tool_calls = _parse_tool_calls(content)

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
            else:
                final_status = "completed"
            break

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            result = _execute_tool_guarded(name, args, root, slug, task_description, read_paths, failed_calls)
            messages.append({"role": "user", "content": f"<tool_response>\n{result['content']}\n</tool_response>"})
    else:
        final_status = "max_turns_reached"

    _log_runpod_cost_summary(
        slug, branch_name, task_description, final_status, turn, total_cost_usd, total_execution_time_ms
    )

    return final_status, turn
