# nova_tool_call_log.py
# Tool-call logging schema for Nova's coding sub-agent (ClickUp 86bawntpb) —
# one JSONL entry per tool call, feeding the future Nova audit process
# (86bawntpm).
#
# was_necessary/was_used are filled in asynchronously by a later judge-pass
# or manual flag (same pattern as the tutor-domain blend_flag log) — that
# fill mechanism isn't built yet, so both start null on every entry. The log
# itself stays cheap: pure instrumentation, no blocking eval at call time.
#
# Interim schema, built knowingly temporary: 86bax697m (Langfuse adoption,
# confirmed as the definite direction 2026-07-14) is expected to eventually
# absorb this as trace/observation instrumentation instead of a custom JSONL
# schema. Built now per Marvin's explicit sequencing call the same day —
# unblocks 86bax0exx's orchestrator today, accepted as throwaway once
# Langfuse actually lands.
#
# CLI-visibility bridge (2026-07-14): nova_orchestrator.py's own
# _execute_tool wrapper only ever saw its own hand-rolled Python tool loop —
# it has zero visibility into what claude -p/--worktree headless dispatch
# sessions actually do, since those run Claude Code's own internal tool
# dispatch. The __main__ block below is invoked as a PostToolUse/
# PostToolUseFailure hook (see .claude/settings.json) to close that gap —
# one JSONL entry per real CLI tool call, same schema as _execute_tool's
# own logging.

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path


def _resolve_log_dir() -> Path:
    """
    Determine where to write tool_call_log.jsonl. If this script is
    currently running from inside a `claude --worktree` checkout (detected
    via a .claude/worktrees/<name>/ path segment), redirect to the MAIN
    repo's logs/ directory instead of the worktree's own — worktrees get
    discarded after review, so a worktree-local log would be lost the
    moment that happens, and this data (unlike usage stats) can't be
    recomputed afterward. No env var or hardcoded machine path needed:
    the main repo root is just everything before the .claude/worktrees/
    segment, which is structurally identical on every machine.
    """
    script_path = Path(__file__).resolve()
    parts = script_path.parts
    if ".claude" in parts:
        claude_index = parts.index(".claude")
        if parts[claude_index + 1] == "worktrees":
            main_repo_root = Path(*parts[:claude_index])
            return main_repo_root / "logs"
    return script_path.parent / "logs"


LOG_PATH = _resolve_log_dir() / "tool_call_log.jsonl"


def log_tool_call(
    agent: str,
    session_id: str | None,
    tool: str,
    args: dict,
    result: str,
    error_detail: str | None,
    latency_ms: float | None = None,
) -> str:
    """
    Append one tool-call entry to tool_call_log.jsonl and return its
    tool_call_id. was_necessary/was_used both start null — filled in later
    by an async judge-pass or manual flag, not built yet (see 86bawntpm).

    `result` is "success" or "error" — "timeout" from the schema's own spec
    isn't distinguishable from a generic error today, since nova_tools.py's
    run_command doesn't surface a separate timeout signal. Known gap, not
    faked here.
    """
    tool_call_id = f"toolcall_{uuid.uuid4().hex[:16]}"
    entry = {
        "tool_call_id": tool_call_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "agent": agent,
        "session_id": session_id,
        "tool": tool,
        "args": args,
        "result": result,
        "error_detail": error_detail,
        "latency_ms": latency_ms,
        "was_necessary": None,
        "was_used": None,
    }
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return tool_call_id


if __name__ == "__main__":
    # Invoked as a PostToolUse/PostToolUseFailure hook — reads the hook's
    # JSON payload from stdin (session_id, tool_name, tool_input,
    # tool_response) and logs it via log_tool_call(). Which of the two
    # hooks fired IS the success/error signal — passed as argv[1] by
    # .claude/settings.json's own hook registration, not inferred from the
    # payload (PostToolUse's tool_response shape for errors isn't
    # documented reliably enough to guess at).
    #
    # Never raises — a failed log write must not block the tool-use loop
    # that triggered it.
    result_status = sys.argv[1] if len(sys.argv) > 1 else "success"
    try:
        hook_input = json.load(sys.stdin)
        tool_response = hook_input.get("tool_response", {})
        log_tool_call(
            agent="claude_cli",
            session_id=hook_input.get("session_id"),
            tool=hook_input.get("tool_name", "unknown"),
            args=hook_input.get("tool_input", {}),
            result=result_status,
            error_detail=json.dumps(tool_response) if result_status == "error" else None,
        )
    # Logging must never crash the caller it's instrumenting.
    except Exception:  # nosec B110
        pass
