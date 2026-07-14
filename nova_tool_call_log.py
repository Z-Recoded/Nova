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

import json
import os
import uuid
from datetime import datetime

LOG_PATH = "C:/Nova/logs/tool_call_log.jsonl"


def log_tool_call(
    agent: str,
    session_id: str | None,
    tool: str,
    args: dict,
    result: str,
    error_detail: str | None,
    latency_ms: float,
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
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return tool_call_id
