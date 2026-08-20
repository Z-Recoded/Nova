# nova_tool_calling_execution.py
# Nova Training Pipeline Phase 2, tool-calling module (86bbcfpbg) -- minimal
# Phase-1-equivalent. Real gap found while scoping: unlike coding, NO
# upstream task pool exists for tool-calling anywhere in this pipeline
# (checked live -- every Training Pipeline / Eval Harness / specialist
# ticket on the board is coding-specific) and nova_mcp_server.py, the one
# piece of real tool-calling infra that exists, is genuinely unwired --
# zero real usage history to mine. This script builds the smallest real
# version of that missing Phase 1: a small hand-authored task pool, real
# MCP tool execution against nova_mcp_server.py's actual tool functions,
# and real pass/fail grading -- mirroring how coding only got a real
# Phase 2 after Phase 0/1/3 built up real data first.
#
# Deliberately scoped to 3 of nova_mcp_server.py's 5 tools --
# nova_graph/nova_neighbors/nova_context_budget are read-only and
# side-effect-free. nova_query and nova_ingest are excluded: nova_query
# persists every real call to Nova's actual conversation history
# (nova_memory_store.save_history()) and real query telemetry
# (nova_log.py's query_log.jsonl, the Nova Log Health dashboard's real
# data source); nova_ingest triggers a real ingestion run against the
# Second Brain, and /ingest is marked "Untested" in CLAUDE.md's own route
# table. Live-executing either repeatedly here would pollute real
# production state or exercise an unverified path -- a real
# scope-narrowing decision, not an oversight.
#
# Real tool schemas come from FastMCP's own introspection
# (app.list_tools(), confirmed live to return name/description/inputSchema
# in the exact shape Claude's native tool-use API expects) -- never
# hand-duplicated. The real tool functions are called directly as plain
# Python (confirmed live that @app.tool() returns the function unchanged),
# so this never needs the actual MCP server process running on port 8100.
# It DOES need nova_api.py running locally on port 8000 -- nova_mcp_server
# .py's tools call it via httpx unchanged, and that's the real thing being
# tested (schema validation + real execution against the live routes).
#
# Usage:
#   python nova_tool_calling_execution.py --curate   # real run, all 15 tasks
#   python nova_tool_calling_execution.py --report

import argparse
import asyncio
import hashlib
import json
import os
import sys

import anthropic
import httpx
from dotenv import load_dotenv

from nova_mcp_server import NOVA_API_BASE_URL, app, nova_context_budget, nova_graph, nova_neighbors
from nova_orchestrator import NOVA_AGENT_MODEL

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))


# ── Config ─────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "data", "coding_training", "tool_calling")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "phase2_tool_calling_trajectories.jsonl")

# The 3 in-scope, side-effect-free tools -- see header comment for why
# nova_query/nova_ingest are excluded. Maps real tool name -> real callable.
IN_SCOPE_TOOLS = {
    "nova_graph": nova_graph,
    "nova_neighbors": nova_neighbors,
    "nova_context_budget": nova_context_budget,
}

# 1 initial attempt + 1 retry, and only when schema validation itself
# failed -- that's the one failure mode here with a natural, concrete
# error message to learn from (unlike a wrong tool *selection*, which
# Claude has no way to know was wrong).
MAX_SCHEMA_RETRY_ATTEMPTS = 2

SONNET_5_INPUT_COST_PER_MTOK = 2.00
SONNET_5_OUTPUT_COST_PER_MTOK = 10.00

SYSTEM_PROMPT = (
    "You are Nova's assistant, deciding which tool (if any) to call to satisfy the user's "
    "request. Use exactly one tool call that best satisfies the request."
)

# Real node ids confirmed live against nova_graph.json (2026-08-20) -- KAS's
# real id has parentheses, not the "KAS.md" shorthand CLAUDE.md's hub-node
# list uses informally.
_NULL_MD = "Null.md"
_MASTER_TIMELINE = "Master Timeline.md"
_FATALE_WILDMAN = "Fatale Wildman.md"
_SYS_SYMPHONY = "SYS_Symphony.EXE.md"
_KAS = "Knowledge Acquisition System (KAS).md"

# Hand-authored, small and fixed -- same spirit as the Exercism corpus
# being a known, fixed set rather than synthetically generated, appropriate
# for a "minimal" first build across only 3 tools.
TASK_POOL = [
    {
        "task": "Show me the full wikilink graph -- every node and edge in the Second Brain.",
        "expected_tool": "nova_graph",
    },
    {"task": "Give me the complete node and edge map of Nova's knowledge graph.", "expected_tool": "nova_graph"},
    {"task": "I want to see the whole graph structure, not just one file's neighbors.", "expected_tool": "nova_graph"},
    {"task": "Dump every node and edge Nova currently has in its wikilink graph.", "expected_tool": "nova_graph"},
    {"task": "What does the entire knowledge graph look like right now?", "expected_tool": "nova_graph"},
    {"task": f"What files link to or from '{_NULL_MD}'?", "expected_tool": "nova_neighbors"},
    {
        "task": f"Show me the incoming and outgoing wikilinks for '{_MASTER_TIMELINE}'.",
        "expected_tool": "nova_neighbors",
    },
    {
        "task": f"Which notes reference '{_FATALE_WILDMAN}', and which does it reference?",
        "expected_tool": "nova_neighbors",
    },
    {"task": f"Find the neighbors of the file '{_SYS_SYMPHONY}' in the graph.", "expected_tool": "nova_neighbors"},
    {"task": f"What's connected to '{_KAS}'?", "expected_tool": "nova_neighbors"},
    {
        "task": "What files would graph-guided retrieval consider relevant to a question about Null?",
        "expected_tool": "nova_context_budget",
    },
    {
        "task": "Which files are most relevant to 'Tell me about the Master Timeline', without actually answering it?",
        "expected_tool": "nova_context_budget",
    },
    {
        "task": "Rank the files relevant to a query about Fatale Wildman -- just the file list, no generation.",
        "expected_tool": "nova_context_budget",
    },
    {
        "task": "Before running full retrieval, show me which files a query about SYS_Symphony.EXE would pull in.",
        "expected_tool": "nova_context_budget",
    },
    {
        "task": "Get me the context-budget file ranking for a question about the Knowledge Acquisition System.",
        "expected_tool": "nova_context_budget",
    },
]


# ── Real tool schemas (never hand-duplicated) ──────────────────
def _build_claude_tools() -> list[dict]:
    """
    Real, introspected tool schemas from FastMCP itself
    (app.list_tools(), confirmed live to return name/description/
    inputSchema), filtered to IN_SCOPE_TOOLS and reshaped into the
    name/description/input_schema keys Claude's native tool-use API
    expects.
    """
    mcp_tools = asyncio.run(app.list_tools())
    claude_tools = []
    for tool in mcp_tools:
        if tool.name not in IN_SCOPE_TOOLS:
            continue
        claude_tools.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
        )
    return claude_tools


# ── Preflight ──────────────────────────────────────────────────
def _check_nova_api_reachable() -> None:
    """Real reachability check against the live nova_api.py, so a run
    fails with a clear message instead of 15 confusing httpx tracebacks."""
    try:
        response = httpx.get(f"{NOVA_API_BASE_URL}/graph", timeout=5)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise OSError(
            f"nova_api.py is not reachable at {NOVA_API_BASE_URL} ({e}). "
            "Start it first: nova-env\\Scripts\\python -m uvicorn nova_api:app --host 0.0.0.0 --port 8000"
        ) from e


# ── Generate ───────────────────────────────────────────────────
def request_tool_call(client: anthropic.Anthropic, tools: list[dict], task: str, prior_error: str | None = None):
    """
    One Claude call choosing a tool + arguments via native tool use.
    prior_error carries a real schema-binding failure message from a
    previous attempt, when this is a retry. Returns
    (tool_name, arguments, usage) or (None, None, usage) if Claude didn't
    call a tool at all.
    """
    user_content = task
    if prior_error is not None:
        user_content = (
            f"{task}\n\nYour previous tool call failed with this real error:\n{prior_error}\n"
            "Try again with corrected arguments."
        )

    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=tools,
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": user_content}],
    )
    tool_use_blocks = [block for block in message.content if block.type == "tool_use"]
    if not tool_use_blocks:
        return None, None, message.usage
    call = tool_use_blocks[0]
    return call.name, call.input, message.usage


# ── Real execution + grading ───────────────────────────────────
def attempt_and_grade(tool_name: str | None, arguments: dict | None) -> dict:
    """
    Real execution against the real tool function (which itself hits the
    real nova_api.py route via httpx, unchanged from nova_mcp_server.py).
    schema_valid is false on a TypeError from real Python argument
    binding; execution_succeeded is false on any other exception or an
    empty/falsy real response.
    """
    if tool_name is None:
        return {
            "schema_valid": False,
            "execution_succeeded": False,
            "response_preview": None,
            "error": "no tool call made",
        }

    func = IN_SCOPE_TOOLS.get(tool_name)
    if func is None:
        # A real tool name Claude invented outside the 3 offered -- schema
        # mismatch at the tool-selection level, not argument level.
        return {
            "schema_valid": False,
            "execution_succeeded": False,
            "response_preview": None,
            "error": f"unknown tool '{tool_name}'",
        }

    try:
        result = func(**(arguments or {}))
    except TypeError as e:
        return {"schema_valid": False, "execution_succeeded": False, "response_preview": None, "error": str(e)}
    except Exception as e:
        return {"schema_valid": True, "execution_succeeded": False, "response_preview": None, "error": str(e)}

    return {
        "schema_valid": True,
        "execution_succeeded": bool(result),
        "response_preview": str(result)[:300],
        "error": None,
    }


def run_task(client: anthropic.Anthropic, tools: list[dict], task_row: dict) -> dict:
    task = task_row["task"]
    expected_tool = task_row["expected_tool"]

    total_input_tokens = 0
    total_output_tokens = 0
    prior_error = None
    final_grade = None
    final_tool_name = None
    final_arguments = None
    attempt_count = 0

    for _ in range(MAX_SCHEMA_RETRY_ATTEMPTS):
        attempt_count += 1
        tool_name, arguments, usage = request_tool_call(client, tools, task, prior_error)
        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens

        grade = attempt_and_grade(tool_name, arguments)
        final_grade = grade
        final_tool_name = tool_name
        final_arguments = arguments

        if grade["schema_valid"]:
            break
        prior_error = grade["error"]

    tool_selected_correct = final_tool_name == expected_tool
    verification_status = (
        "verified_pass"
        if tool_selected_correct and final_grade["schema_valid"] and final_grade["execution_succeeded"]
        else "verified_fail"
    )

    return {
        "task": task,
        "expected_tool": expected_tool,
        "tool_called": final_tool_name,
        "arguments": final_arguments,
        "tool_selected_correct": tool_selected_correct,
        "schema_valid": final_grade["schema_valid"],
        "execution_succeeded": final_grade["execution_succeeded"],
        "verification_status": verification_status,
        "attempts": attempt_count,
        "source": "phase2_tool_calling_execution",
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }


# ── JSONL read / write ─────────────────────────────────────────
def _task_key(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


def _load_processed_keys() -> set[str]:
    if not os.path.exists(OUTPUT_PATH):
        return set()
    processed = set()
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                processed.add(_task_key(json.loads(line)["task"]))
    return processed


def _append_row(row: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


# ── Main ───────────────────────────────────────────────────────
def curate() -> None:
    _check_nova_api_reachable()

    already_processed = _load_processed_keys()
    remaining = [row for row in TASK_POOL if _task_key(row["task"]) not in already_processed]

    print(f"{len(TASK_POOL)} task(s) in the pool, {len(already_processed)} already processed.")
    print(f"{len(remaining)} remaining unprocessed task(s).")
    if not remaining:
        print("Nothing to do.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set. Export it before running this script.")
    client = anthropic.Anthropic(api_key=api_key)
    tools = _build_claude_tools()

    total_input_tokens = 0
    total_output_tokens = 0
    written = 0
    verified_pass_count = 0

    for task_row in remaining:
        print(f"\n{task_row['task'][:80]}")
        result = run_task(client, tools, task_row)
        total_input_tokens += result.pop("total_input_tokens")
        total_output_tokens += result.pop("total_output_tokens")

        _append_row(result)
        written += 1
        if result["verification_status"] == "verified_pass":
            verified_pass_count += 1

        status = "PASS" if result["verification_status"] == "verified_pass" else "FAIL"
        print(
            f"  -> {status} (tool_called={result['tool_called']}, expected={result['expected_tool']}, "
            f"schema_valid={result['schema_valid']}, execution_succeeded={result['execution_succeeded']})"
        )

    cost = (
        total_input_tokens / 1_000_000 * SONNET_5_INPUT_COST_PER_MTOK
        + total_output_tokens / 1_000_000 * SONNET_5_OUTPUT_COST_PER_MTOK
    )
    print(f"\n{written} row(s) written to {OUTPUT_PATH} ({verified_pass_count}/{written} verified_pass)")
    print(
        f"Real usage this run: {total_input_tokens} input token(s), {total_output_tokens} output token(s), "
        f"~${cost:.4f} at Sonnet 5's introductory list rate (visibility only, not budget-enforced)."
    )


def report() -> None:
    if not os.path.exists(OUTPUT_PATH):
        print(f"No curated file found at {OUTPUT_PATH} — run --curate first.")
        return

    with open(OUTPUT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    verified_pass = sum(1 for row in rows if row["verification_status"] == "verified_pass")
    wrong_tool = sum(1 for row in rows if not row["tool_selected_correct"])
    bad_schema = sum(1 for row in rows if not row["schema_valid"])
    exec_failed = sum(1 for row in rows if row["schema_valid"] and not row["execution_succeeded"])

    print(f"Total curated rows: {len(rows)} of {len(TASK_POOL)} task(s) in the pool.")
    print(f"Verified pass: {verified_pass}/{len(rows)}")
    print(f"Wrong tool selected: {wrong_tool}")
    print(f"Schema invalid (bad arguments): {bad_schema}")
    print(f"Right tool, valid args, execution still failed: {exec_failed}\n")

    if rows:
        print("Sample row(s) (task, tool_called, status):")
        for row in rows[:3]:
            print(f"  {row['task'][:60]}  |  {row['tool_called']}  |  {row['verification_status']}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nova Training Pipeline Phase 2 — tool-calling, minimal execution-grounded task pool (86bbcfpbg)"
    )
    parser.add_argument("--curate", action="store_true", help="Generate + real-execute + grade tool calls")
    parser.add_argument("--report", action="store_true", help="Print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    args = parser.parse_args()

    if args.all or args.curate:
        curate()
    if args.all or args.report:
        report()
