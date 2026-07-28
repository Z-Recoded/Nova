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

import json
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
    import os

    return os.path.isfile(os.path.join(root, path))


def _execute_tool_guarded(
    name: str, args: dict, root: str, session_id: str, task_description: str, read_paths: set
) -> dict:
    """
    Wraps nova_orchestrator._execute_tool() (deferred import -- avoids
    import-time circularity with nova_orchestrator.py lazily importing this
    module) with two extra checks: refuse write_file/file_replace on a path
    that already exists on disk but hasn't been read_file'd yet this task
    run, and refuse a second read_file on a path already read (closes a
    real observed loop: the model re-reading an already-read file up to 8
    times instead of proceeding to the edit). Both return a synthetic
    is_error result rather than dispatching -- list_files/run_command and
    edits to brand-new paths are never gated.
    """
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
    if name == "read_file" and not result.get("is_error", False):
        read_paths.add(args.get("path", ""))
    return result


# ── Turn logging ─────────────────────────────────────────────────


class _RunpodUsage:
    """
    Adapts nova_remote_inference's usage dict shape (prompt_eval_count/
    eval_count, no caching concept) into the attribute interface
    nova_token_budget.record_usage() expects (an Anthropic response.usage
    object, or anything with the same four attributes). Cache fields are
    real zeros here (no prompt caching on this endpoint), not an
    approximation.
    """

    def __init__(self, prompt_eval_count: int | None, eval_count: int | None):
        self.input_tokens = prompt_eval_count or 0
        self.output_tokens = eval_count or 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


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
) -> None:
    """
    Sibling to nova_orchestrator._log_agent_turn(), writing the identical
    agent_log.jsonl schema from this backend's real response shape instead
    of Anthropic SDK objects. tool_calls are re-keyed to {"name", "input"}
    (not "arguments") to stay consistent with nova_coding_dataset_curator.py's
    existing reader of this file. cache_* fields are explicitly None (no
    prompt caching on this endpoint -- distinct from record_usage()'s own
    real-zero convention above, since a log entry should say "not
    applicable" honestly rather than imply caching happened and yielded 0).
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
    }
    with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    del content  # not logged today, same as _log_agent_turn's own omission of response text


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
    from nova_token_budget import get_budget_status, record_usage

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

    final_status = "incomplete"
    turn = 0
    for turn in range(1, max_turns + 1):
        if budget_gate_enabled and get_budget_status().get("mode") == "halt":
            final_status = "stopped_budget_halt"
            break

        response = nova_remote_inference.chat(messages, num_ctx=NUM_CTX, max_tokens=max_output_tokens)
        if response is None:
            # Infra/network failure, not a model stop condition -- a distinct
            # string so a report reader can tell the two apart.
            final_status = "stopped_runpod_call_failed"
            break

        content = response["message"]["content"]
        tool_calls = _parse_tool_calls(content)

        _log_agent_turn_runpod(
            slug, branch_name, turn, task_description, content, tool_calls, response, skill_category, skill_version
        )
        if budget_gate_enabled:
            record_usage(_RunpodUsage(response.get("prompt_eval_count"), response.get("eval_count")))

        messages.append({"role": "assistant", "content": content})

        if not tool_calls:
            final_status = "completed"
            break

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            result = _execute_tool_guarded(name, args, root, slug, task_description, read_paths)
            messages.append({"role": "user", "content": f"<tool_response>\n{result['content']}\n</tool_response>"})
    else:
        final_status = "max_turns_reached"

    return final_status, turn
