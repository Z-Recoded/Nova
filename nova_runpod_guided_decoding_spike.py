# nova_runpod_guided_decoding_spike.py
# Throwaway spike (86bb72wfy, corrected scope) -- tests whether vLLM's
# guided decoding (guided_regex/guided_choice) is reachable against Nova's
# RunPod-hosted Qwen2.5-Coder-32B-Instruct-AWQ endpoint, and whether it can
# usefully constrain a turn's output shape without breaking the model's
# ability to ever emit a genuine completion summary.
#
# Real finding before writing this: 86bb72wfy's literal ask (vLLM's
# tool_choice="required") has no real path to work here -- confirmed via
# nova_runpod_endpoint_deploy.py's create_template(), the deployed template
# sets no ENABLE_AUTO_TOOL_CHOICE/TOOL_CALL_PARSER env var at all, matching
# the existing 86bb71x02 audit (no native tool-calling parser configured on
# either endpoint). The ticket's own supporting evidence (F1 over-explaining,
# a think=True finding) is also from a DIFFERENT backend entirely --
# qwen3:8b running locally via Ollama (nova_ollama_toolcall_spike.py), not
# this vLLM-served RunPod backend, which has never actually shown
# over-explaining across any real held-out eval run.
#
# Real research finding instead (RunPod/vLLM docs, not guessed): guided
# decoding is a genuine, separate vLLM feature from tool-calling, reachable
# through worker-vllm's openai_route/openai_input request passthrough --
# structurally different from nova_remote_inference.chat()'s existing raw
# {"input": {"messages": ..., "sampling_params": ...}} schema. This script
# builds and sends that alternate request shape directly (does NOT modify
# nova_remote_inference.py -- this is a one-off test of a schema its shared
# callers don't need today), reusing only its URL/key constants.
#
# Not wired into any production import graph; delete/archive once its go/no-go
# checkpoint is done, same as nova_runpod_toolcall_spike.py/
# nova_ollama_toolcall_spike.py.
#
# Usage:
#   nova-env\\Scripts\\python nova_runpod_guided_decoding_spike.py

import json
import re
import sys

import requests

import nova_remote_inference

sys.stdout.reconfigure(encoding="utf-8")

# Real, tested convention (see nova_orchestrator_runpod.py's own
# TOOLS_FORMAT_PROMPT) -- kept identical here so a <tools> block produced
# under a guided-decoding constraint is directly comparable to production
# behavior, not a different prompt convention.
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

# Deliberately NOT "must start with <tools>" unconditionally -- that would
# make the model structurally unable to ever emit a genuine completion
# summary, trading over-explaining for a worse failure (can never finish).
# Allows EITHER a real <tools>...</tools> block OR a short plain-text
# response (<= ~300 chars) -- narrow enough to block a multi-paragraph
# unsolicited breakdown, loose enough for a legitimate short summary.
GUIDED_REGEX_TOOLS_OR_SHORT_SUMMARY = r"(<tools>[\s\S]{0,4000}</tools>[\s\S]{0,4000})|([^\n]{0,300})"

# Same regex/parsing logic as nova_runpod_toolcall_spike.py -- duplicated
# rather than imported, matching that file's own "each spike stays
# self-contained" precedent.
_TOOLS_BLOCK_RE = re.compile(r"<tools>(.*?)(?:</tools>|\Z)", re.DOTALL)


def _parse_tool_json(raw: str):
    raw = raw.strip()
    for strict in (True, False):
        try:
            return json.loads(raw, strict=strict)
        except json.JSONDecodeError:
            continue
    return None


def _parse_tool_calls(content: str) -> list[dict]:
    calls: list[dict] = []
    for raw_block in _TOOLS_BLOCK_RE.findall(content):
        parsed = _parse_tool_json(raw_block)
        if isinstance(parsed, dict):
            calls.append(parsed)
        elif isinstance(parsed, list):
            calls.extend(item for item in parsed if isinstance(item, dict))
    return calls


def call_raw_schema(messages: list[dict], max_tokens: int = 512) -> dict | None:
    """Baseline: Nova's existing production request schema, via chat() directly -- no guided decoding."""
    return nova_remote_inference.chat(messages, num_ctx=8192, max_tokens=max_tokens)


def call_openai_route_guided(messages: list[dict], guided_regex: str, max_tokens: int = 512) -> dict:
    """
    Sends the alternate openai_route/openai_input request shape RunPod's
    worker-vllm docs describe, with guided_regex as an extra top-level field
    (vLLM's OpenAI-compatible server accepts vLLM-specific extensions as
    plain extra JSON fields -- extra_body is a client-SDK construct for
    injecting exactly this when using the official openai Python client;
    since this script sends raw JSON directly, the field just goes straight
    in). Returns the FULL raw response dict regardless of success/failure --
    this spike's whole point is observing real behavior, not asserting a
    predicted shape.
    """
    if not nova_remote_inference.RUNPOD_API_KEY:
        return {"error": "RUNPOD_API_KEY not set"}

    headers = {
        "Authorization": f"Bearer {nova_remote_inference.RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "openai_route": "/v1/chat/completions",
            "openai_input": {
                "model": nova_remote_inference.MODEL_NAME,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "guided_regex": guided_regex,
            },
        }
    }

    try:
        response = requests.post(
            nova_remote_inference.RUNPOD_RUNSYNC_URL,
            headers=headers,
            json=payload,
            timeout=nova_remote_inference.HTTP_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return {"error": f"request failed: {e}"}

    try:
        return {"status_code": response.status_code, "body": response.json()}
    except ValueError:
        return {"status_code": response.status_code, "body_text": response.text[:2000]}


if __name__ == "__main__":
    # A real prompt shaped to plausibly invite an unsolicited breakdown --
    # asks for an edit but doesn't forbid explaining first, unlike
    # nova_orchestrator_runpod.py's production READ_BEFORE_WRITE_GUARD_PROMPT.
    task_description = (
        "Add a one-line comment above the SOURCES list in nova_sources.py explaining "
        "it configures which directories ingest.py scans."
    )
    messages = [
        {"role": "system", "content": TOOLS_FORMAT_PROMPT},
        {"role": "user", "content": task_description},
    ]

    print("=== 1. Baseline: existing raw schema, no guided decoding ===")
    baseline = call_raw_schema(messages)
    print(f"result: {baseline}\n")

    print("=== 2. openai_route + guided_regex (tools-or-short-summary) ===")
    guided = call_openai_route_guided(messages, GUIDED_REGEX_TOOLS_OR_SHORT_SUMMARY)
    print(f"raw response: {json.dumps(guided, indent=2)[:3000]}\n")

    if "body" in guided and isinstance(guided["body"], dict):
        try:
            content = guided["body"]["output"][0]["choices"][0]["message"]["content"]
            print(f"extracted content: {content!r}")
            print(f"parsed tool_calls: {_parse_tool_calls(content)}")
        except (KeyError, IndexError, TypeError) as e:
            print(f"could not extract content from guided response using the OpenAI chat-completions shape: {e}")
