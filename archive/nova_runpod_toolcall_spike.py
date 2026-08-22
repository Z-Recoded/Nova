# nova_runpod_toolcall_spike.py
# Throwaway Phase 1 spike, RunPod variant (Qwen3 8B coding-agent eval harness
# plan) -- tests whether Nova's RunPod-hosted Qwen2.5-Coder-32B-Instruct-AWQ
# endpoint (nova_remote_inference.py) can drive Nova's real 5 coding tools.
#
# Real research finding before writing this (2026-07-27, see PR discussion
# for the qwen3:8b/Ollama spike): RunPod's worker-vllm template *can* enable
# native tool-calling (ENABLE_AUTO_TOOL_CHOICE + TOOL_CALL_PARSER env vars),
# but there is no reliable built-in vLLM tool-call parser for Qwen2.5-Coder
# specifically (open vLLM issue #32926) -- the hermes parser it's usually
# paired with expects <tool_call> tags, but Qwen2.5-Coder doesn't reliably
# produce them (100% plain ```json``` code blocks with no format guidance).
# The issue's own proposed fix (unmerged PR #32931) instead prompts the
# model with <tools>{"name": ..., "arguments": {...}}</tools> few-shot
# examples, reporting ~100% compliance across model sizes including
# 32B-AWQ -- the exact deployment Nova already has on RunPod. This script
# reproduces that same prompted format and parses it manually, since
# nova_remote_inference.py's endpoint has no native tools= parameter at all
# (confirmed: it's RunPod's raw /runsync job schema, not an OpenAI-compatible
# chat-completions route).
#
# Not wired into any production import graph; delete/archive once its
# go/no-go checkpoint is done, same as nova_ollama_toolcall_spike.py.
#
# Usage:
#   python nova_runpod_toolcall_spike.py

import json
import re
import sys

import nova_orchestrator
import nova_remote_inference

# Same recurring Windows-console gotcha hit and fixed during the Ollama spike.
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_MAX_TURNS = 8
NUM_CTX = 8192  # accepted by nova_remote_inference.chat() but not forwarded -- kept for parity

# Real, tested format from vLLM issue #32926 / PR #32931's proposed
# qwen2_5_coder parser -- <tools>{"name": ..., "arguments": {...}}</tools>,
# parallel calls as multiple back-to-back <tools> blocks. Delivered here as
# plain system-prompt text (few-shot, not a real chat-template change) since
# Nova's RunPod deployment doesn't have that custom tool-parser/template
# installed -- we can't change what the server does, only what we ask the
# model to do in-context.
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

# Same two safety guards proven out during the Ollama spike (round 2/3):
# refuse editing an existing file that hasn't been read_file'd yet this run,
# and refuse re-reading a path already read. Duplicated here rather than
# imported from nova_ollama_toolcall_spike.py -- each throwaway spike stays
# self-contained, same precedent as nova_agent_log_status.py's own comment
# on duplicating rather than importing small constants/logic across
# one-off scripts.
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


def _worktree_has_file(root: str, path: str) -> bool:
    import os

    return os.path.isfile(os.path.join(root, path))


def _execute_tool_guarded(
    name: str, args: dict, root: str, session_id: str, task_description: str, read_paths: set
) -> dict:  # noqa: E501
    """Same guard behavior as nova_ollama_toolcall_spike.py's version -- see its docstring."""
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

    result = nova_orchestrator._execute_tool(name, args, root, session_id=session_id, task_description=task_description)
    if name == "read_file" and not result.get("is_error", False):
        read_paths.add(args.get("path", ""))
    return result


# Regex per PR #32931: extract <tools>...</tools> content, tolerating an
# unclosed trailing tag (the model's final block sometimes never gets a
# closing tag before it stops generating).
_TOOLS_BLOCK_RE = re.compile(r"<tools>(.*?)(?:</tools>|\Z)", re.DOTALL)


def _parse_tool_json(raw: str):
    """
    Fallback chain per PR #32931: single JSON object/array, else JSONL (one
    object per line). Tries strict=False first for the single-object/array
    case -- a real, observed failure mode is the model embedding a literal
    newline inside a string value (e.g. new_str spanning multiple lines)
    instead of escaping it as \\n, which strict JSON rejects as an invalid
    control character but strict=False accepts.
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


def run_spike_task(task_description: str, max_turns: int = DEFAULT_MAX_TURNS) -> dict:
    """
    Run one task through Nova's RunPod-hosted Qwen2.5-Coder-32B-Instruct-AWQ
    endpoint, using a prompted <tools>-tag format (no native function-calling
    available on this deployment) parsed manually and dispatched through the
    same guarded, backend-agnostic _execute_tool() the Ollama spike used.
    Prints a full per-turn transcript -- that transcript is the pass/fail
    signal, not the quality of the resulting code.
    """
    slug = nova_orchestrator._slugify(task_description)
    worktree_path, branch_name = nova_orchestrator._create_worktree(slug)
    root = str(worktree_path)
    system_prompt = nova_orchestrator._build_system_prompt(root) + TOOLS_FORMAT_PROMPT + READ_BEFORE_WRITE_GUARD_PROMPT
    read_paths: set = set()

    print(f"\n=== Spike task (RunPod/Qwen2.5-Coder-32B): {task_description!r} ===")
    print(f"Worktree: {root}")
    print(f"Branch: {branch_name}\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_description},
    ]

    final_status = "incomplete"
    turn = 0
    for turn in range(1, max_turns + 1):
        response = nova_remote_inference.chat(messages, num_ctx=NUM_CTX)
        if response is None:
            final_status = "runpod_call_failed"
            print(f"--- turn {turn}: RunPod call failed (see printed error above) ---")
            break

        content = response["message"]["content"]
        tool_calls = _parse_tool_calls(content)

        print(f"--- turn {turn} ---")
        print(f"content: {content!r}")
        print(f"parsed tool_calls: {tool_calls}")

        messages.append({"role": "assistant", "content": content})

        if not tool_calls:
            final_status = "completed"
            break

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            result = _execute_tool_guarded(name, args, root, slug, task_description, read_paths)
            print(f"  dispatched {name}({args}) -> is_error={result.get('is_error', False)}")
            print(f"  result content: {result['content'][:500]!r}")
            messages.append({"role": "user", "content": f"<tool_response>\n{result['content']}\n</tool_response>"})
    else:
        final_status = "max_turns_reached"

    print(f"\n=== Status: {final_status} after {turn} turn(s) ===")
    print(f"Inspect the worktree directly: git -C {root} status  /  git -C {root} diff master\n")

    return {
        "task": task_description,
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turn,
    }


if __name__ == "__main__":
    # Same literal historical smoke-test wording used for the Ollama spike,
    # for a like-for-like comparison between the two candidate brains.
    SPIKE_TASKS = [
        "Add a GET /healthz endpoint to nova_api.py that returns {'status': 'ok'}. "
        "Keep it minimal and follow the file's existing route style.",
        "Add a one-line comment above the SOURCES list in nova_sources.py explaining "
        "it configures which directories ingest.py scans.",
    ]
    for task in SPIKE_TASKS:
        run_spike_task(task)
