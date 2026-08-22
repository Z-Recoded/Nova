# nova_ollama_toolcall_spike.py
# Throwaway Phase 1 spike (Qwen3 8B coding-agent eval harness plan) — proves
# or disproves, as cheaply as possible, that qwen3:8b can drive Nova's real
# 5 coding tools through Ollama's function-calling API. Not wired into any
# production import graph; delete/archive once its go/no-go checkpoint is
# done. See the approved plan for full context.
#
# Usage:
#   python nova_ollama_toolcall_spike.py

import os
import sys

import ollama

import nova_orchestrator

# Same recurring Windows-console gotcha this repo has hit and fixed before
# (nova_benchmark.py's ✓/✗ output, this project's own print()s with em/en
# dashes) — cp1252's default codepage can't encode arrows/em-dashes that
# show up verbatim in real file contents printed to the transcript.
sys.stdout.reconfigure(encoding="utf-8")

# ── Config ─────────────────────────────────────────────────────
SPIKE_MODEL = "qwen3:8b"
DEFAULT_MAX_TURNS = 8

# Same bind-all guard as nova_query.py's OLLAMA_HOST resolution, duplicated
# here rather than imported — importing nova_query would also import
# chromadb/graph_builder and open a live Chroma HttpClient at module load,
# entirely unrelated to what this spike is testing.
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"
_raw_ollama_host = os.environ.get("OLLAMA_HOST")
_host_is_bind_all = _raw_ollama_host == "0.0.0.0" or (_raw_ollama_host or "").startswith("0.0.0.0:")  # nosec B104
OLLAMA_HOST = LOCAL_OLLAMA_URL if (not _raw_ollama_host or _host_is_bind_all) else _raw_ollama_host

# Hand-translated from nova_orchestrator.TOOL_DEFINITIONS's 5 Anthropic-shaped
# tool defs into Ollama's OpenAI-style shape ({"type":"function","function":
# {name, description, parameters}}) — confirmed by hand against the real
# source rather than a generic translator, per the Phase 1 plan.
OLLAMA_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file's contents, relative to the task's worktree root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the worktree root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file, relative to the task's worktree root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the worktree root."},
                    "content": {"type": "string", "description": "Full file contents to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_replace",
            "description": (
                "Replace a single unique occurrence of old_str with new_str in an "
                "existing file, relative to the task's worktree root. old_str must "
                "appear exactly once. Prefer this over write_file for edits to "
                "existing files — it sends only the changed text as output instead "
                "of the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the worktree root."},
                    "old_str": {
                        "type": "string",
                        "description": "Exact text to replace — must appear exactly once in the file.",
                    },
                    "new_str": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a directory, relative to the task's worktree root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path, relative to the worktree root. Use '.' for the whole worktree.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command with cwd pinned to the task's worktree root (e.g. running tests).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run."},
                },
                "required": ["cmd"],
            },
        },
    },
]


# Spike-only safety guard (round 2): the first run showed qwen3:8b calling
# write_file/file_replace on files it had never read, guessing at their
# contents — once destructively (wiped a real 1644-line file down to 7
# lines), once with a wrong guessed old_str that just silently went nowhere.
# read_file/list_files/run_command are left alone; only edits to an
# already-existing file are gated.
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
    return os.path.isfile(os.path.join(root, path))


def _execute_tool_guarded(
    name: str, args: dict, root: str, session_id: str, task_description: str, read_paths: set
) -> dict:  # noqa: E501
    """
    Wraps nova_orchestrator._execute_tool() with two extra checks beyond
    nova_orchestrator._execute_tool(): (1) refuse write_file/file_replace on
    a path that already exists on disk but hasn't been read_file'd yet this
    task run, (2) refuse a second read_file on a path already read this run
    (round 1 of this guard fixed a destructive blind overwrite; a follow-up
    run then showed the model re-reading the same already-read file up to
    8 times in a row instead of proceeding to the edit -- this second check
    closes that loop by forcing a corrective message instead of re-serving
    the same content). Both return a synthetic is_error result (same shape
    _execute_tool already returns on a real error) rather than dispatching --
    list_files/run_command and edits to brand-new paths are never gated.
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

    result = nova_orchestrator._execute_tool(name, args, root, session_id=session_id, task_description=task_description)
    if name == "read_file" and not result.get("is_error", False):
        read_paths.add(args.get("path", ""))
    return result


def run_spike_task(task_description: str, max_turns: int = DEFAULT_MAX_TURNS) -> dict:
    """
    Run one task through qwen3:8b via Ollama's tool-calling API, dispatching
    every tool call through the read-before-write guard above to
    nova_orchestrator's real, backend-agnostic _execute_tool() against a real
    disposable worktree. Prints a full per-turn transcript — that printed
    transcript is the pass/fail signal for this spike, not the quality of
    the resulting code.
    """
    client = ollama.Client(host=OLLAMA_HOST)

    slug = nova_orchestrator._slugify(task_description)
    worktree_path, branch_name = nova_orchestrator._create_worktree(slug)
    root = str(worktree_path)
    system_prompt = nova_orchestrator._build_system_prompt(root) + READ_BEFORE_WRITE_GUARD_PROMPT
    read_paths: set = set()

    print(f"\n=== Spike task: {task_description!r} ===")
    print(f"Worktree: {root}")
    print(f"Branch: {branch_name}\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_description},
    ]

    final_status = "incomplete"
    turn = 0
    for turn in range(1, max_turns + 1):
        response = client.chat(
            model=SPIKE_MODEL,
            messages=messages,
            tools=OLLAMA_TOOL_DEFINITIONS,
            think=True,
        )
        msg = response["message"]
        tool_calls = msg.get("tool_calls") or []

        print(f"--- turn {turn} ---")
        if msg.get("thinking"):
            print(f"thinking: {msg.get('thinking')!r}")
        print(f"content: {msg.get('content')!r}")
        print(f"tool_calls: {tool_calls}")

        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

        if not tool_calls:
            final_status = "completed"
            break

        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]  # already a dict, no json.loads needed
            result = _execute_tool_guarded(name, args, root, slug, task_description, read_paths)
            print(f"  dispatched {name}({args}) -> is_error={result.get('is_error', False)}")
            print(f"  result content: {result['content'][:500]!r}")
            messages.append({"role": "tool", "tool_name": name, "content": result["content"]})
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
    # Literal wording reused from real historical smoke-test tasks in
    # logs/agent_task_outcomes.jsonl, so a later comparison against how the
    # Claude-backed loop handled the exact same task is apples-to-apples.
    SPIKE_TASKS = [
        "Add a GET /healthz endpoint to nova_api.py that returns {'status': 'ok'}. "
        "Keep it minimal and follow the file's existing route style.",
        "Add a one-line comment above the SOURCES list in nova_sources.py explaining "
        "it configures which directories ingest.py scans.",
    ]
    for task in SPIKE_TASKS:
        run_spike_task(task)
