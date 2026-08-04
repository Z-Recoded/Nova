# nova_remote_inference_native_tools.py
# RunPod serverless adapter for Devstral-Small-2507, using REAL native
# tool-calling (OpenAI-style `tools`/`tool_choice` request params,
# `tool_calls` in the response) instead of nova_remote_inference.py's
# prompted-format approach for Qwen2.5-Coder (that model has no reliable
# vLLM tool-call parser -- see nova_orchestrator_runpod.py's own header).
#
# Deliberately a SEPARATE module, not a modification of
# nova_remote_inference.py: that module's chat() has a strict Ollama-shaped
# return contract nova_query.py's production RAG path depends on, and its
# request schema (RunPod's raw vLLM-worker `/runsync` envelope with a flat
# "messages"/"sampling_params" input) has no room for a real "tools" array.
#
# Uses RunPod worker-vllm's `openai_route`/`openai_input` request-level
# passthrough (proven live on the `guided-decoding-spike` branch,
# nova_runpod_guided_decoding_spike.py, for a different vLLM feature --
# guided decoding) to reach the worker's real OpenAI-compatible
# /v1/chat/completions route instead of its default flat schema. Confirmed
# live 2026-08-03 against this exact endpoint: a real `tools`-bearing
# request returns a correctly-populated `tool_calls` array
# (finish_reason="tool_calls", one call with the right function name and
# JSON-string arguments) -- not just an HTTP 200.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_remote_inference_native_tools.py

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────
MODEL_NAME = "mistralai/Devstral-Small-2507"

RUNPOD_ENDPOINT_ID = "sh0he5uubl6hw3"
RUNPOD_BASE_URL = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
RUNPOD_RUNSYNC_URL = f"{RUNPOD_BASE_URL}/runsync"
RUNPOD_STATUS_URL_TEMPLATE = f"{RUNPOD_BASE_URL}/status/{{job_id}}"

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")

SAMPLING_TEMPERATURE = 0.0  # deterministic, matching this project's other generation calls

RUNSYNC_TIMEOUT_SECONDS = 90
HTTP_REQUEST_TIMEOUT_SECONDS = 100

# Real cold start observed live on this exact endpoint (2026-08-03
# sanity check): 384s. Set well above that with real margin, not copied
# from nova_remote_inference.py's 300s -- that ceiling was ALREADY raised
# once after two real held-out-eval timeouts on a different (smaller,
# already-warm-more-often) endpoint, and this one's checkpoint format
# (Mistral-native, not a plain HF safetensors load) adds real, observed
# extra cold-start time.
POLL_INTERVAL_SECONDS = 5.0
POLL_MAX_TOTAL_SECONDS = 600

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}

RUNPOD_GPU_HOURLY_RATE_USD = 2.99  # same H100 80GB tier as the Qwen eval endpoints


def _calculate_cost_usd(execution_time_ms: int | None) -> float | None:
    """Real dollar cost for one job, from its real executionTime -- same formula as nova_remote_inference.py."""
    if execution_time_ms is None:
        return None
    hours = execution_time_ms / 1000 / 3600
    return hours * RUNPOD_GPU_HOURLY_RATE_USD


# ── Tool schema ────────────────────────────────────────────────


def build_tool_schema() -> list[dict]:
    """
    Converts Nova's existing tool catalog (nova_orchestrator.TOOL_DEFINITIONS,
    already in Anthropic's input_schema shape) into OpenAI's function-calling
    shape. Anthropic's input_schema and OpenAI's function.parameters are both
    plain JSON Schema objects -- no shape translation needed beyond renaming
    the wrapper keys, so this reads TOOL_DEFINITIONS directly rather than
    maintaining a second, duplicate tool catalog that could drift out of sync.

    Deferred import of nova_orchestrator -- nova_orchestrator.py only ever
    imports a coding-agent backend module (this one included) lazily, inside
    run_coding_task(), specifically to avoid a real import-time cycle (same
    discipline nova_orchestrator_runpod.py already follows for its own
    nova_orchestrator._execute_tool reference). Importing nova_orchestrator
    back at this module's top level would recreate exactly that cycle.
    """
    from nova_orchestrator import TOOL_DEFINITIONS

    return [
        {
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["input_schema"],
            },
        }
        for tool_def in TOOL_DEFINITIONS
    ]


# ── Helpers ────────────────────────────────────────────────────


def _extract_native_response(response_json: dict) -> dict | None:
    """
    Parse a COMPLETED RunPod job's response (real OpenAI chat-completions
    shape, reached via the openai_route passthrough) into:
    {"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}],
    "finish_reason": str | None, "prompt_eval_count": int | None,
    "eval_count": int | None, "execution_time_ms": int | None,
    "delay_time_ms": int | None, "cost_usd": float | None,
    "logprobs": list[dict] | None}.

    Each tool call's "arguments" field arrives as a JSON-encoded string per
    the OpenAI function-calling spec (confirmed live: '{"path": "notes.txt"}')
    -- parsed here into a real dict so callers never have to re-parse it.
    Logs loudly and returns None on any shape mismatch, same discipline as
    nova_remote_inference._extract_answer().

    "logprobs" (added for Observability Initiative Phase 1, 86bb7pawp) is a
    real per-token list of {"token": str, "logprob": float} pairs pulled from
    choice.logprobs.content[], or None if the caller didn't request logprobs
    -- same standard OpenAI-compatible shape confirmed live against this
    endpoint's own tool-calling responses.
    """
    try:
        result = response_json["output"][0]
        choice = result["choices"][0]
        message = choice["message"]
        raw_tool_calls = message.get("tool_calls") or []
        tool_calls = [
            {
                "id": call["id"],
                "name": call["function"]["name"],
                "arguments": json.loads(call["function"]["arguments"]),
            }
            for call in raw_tool_calls
        ]
        usage = result.get("usage", {})
        execution_time_ms = response_json.get("executionTime")

        logprobs_content = (choice.get("logprobs") or {}).get("content")
        logprobs = None
        if logprobs_content:
            logprobs = [{"token": entry["token"], "logprob": entry["logprob"]} for entry in logprobs_content]

        return {
            "content": message.get("content"),
            "tool_calls": tool_calls,
            "finish_reason": choice.get("finish_reason"),
            "prompt_eval_count": usage.get("prompt_tokens"),
            "eval_count": usage.get("completion_tokens"),
            "execution_time_ms": execution_time_ms,
            "delay_time_ms": response_json.get("delayTime"),
            "cost_usd": _calculate_cost_usd(execution_time_ms),
            "logprobs": logprobs,
        }
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(
            f"[nova_remote_inference_native_tools] UNEXPECTED RESPONSE SHAPE -- schema "
            f"assumption may be wrong: {e} -- raw response: {response_json}"
        )
        return None


# ── Core ───────────────────────────────────────────────────────


def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    tool_choice: str = "auto",
    logprobs: bool = False,
    top_logprobs: int | None = None,
) -> dict | None:
    """
    Send a chat-completion request with real native tool-calling to the
    Devstral endpoint, via RunPod worker-vllm's openai_route/openai_input
    passthrough. Returns None on any failure (missing API key, network
    error, non-2xx response, poll timeout, or an unexpected response
    shape) -- never raises, matching nova_remote_inference.chat()'s
    contract, though this is a structurally distinct function with a
    structurally distinct return shape (real tool_calls, not a single
    content string), not a drop-in replacement for it.

    logprobs/top_logprobs (added for Observability Initiative Phase 1,
    86bb7pawp): standard OpenAI-compatible request params, default off so
    every existing caller's behavior/payload is unchanged. top_logprobs is
    only added to the payload when logprobs=True and a value is given --
    vLLM's OpenAI-compatible route rejects top_logprobs without logprobs
    set, so this avoids ever sending an invalid combination.
    """
    if not RUNPOD_API_KEY:
        print("[nova_remote_inference_native_tools] RUNPOD_API_KEY not set -- skipping remote call")
        return None

    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }
    openai_input = {
        "model": MODEL_NAME,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": max_tokens,
        "temperature": SAMPLING_TEMPERATURE,
    }
    if logprobs:
        openai_input["logprobs"] = True
        if top_logprobs is not None:
            openai_input["top_logprobs"] = top_logprobs

    payload = {
        "input": {
            "openai_route": "/v1/chat/completions",
            "openai_input": openai_input,
        }
    }

    try:
        response = requests.post(
            RUNPOD_RUNSYNC_URL,
            headers=headers,
            json=payload,
            timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        print(f"[nova_remote_inference_native_tools] request to RunPod failed: {e}")
        return None

    if response.status_code != 200:
        print(f"[nova_remote_inference_native_tools] RunPod returned {response.status_code}: {response.text[:500]}")
        return None

    data = response.json()
    status = data.get("status")

    if status == "COMPLETED":
        return _extract_native_response(data)

    if status in ("IN_QUEUE", "IN_PROGRESS"):
        return _poll_until_terminal(data["id"], headers)

    print(f"[nova_remote_inference_native_tools] unexpected initial status {status!r}: {data}")
    return None


def _poll_until_terminal(job_id: str, headers: dict) -> dict | None:
    """Poll RunPod's /status/{id} endpoint until a terminal status or POLL_MAX_TOTAL_SECONDS elapses."""
    status_url = RUNPOD_STATUS_URL_TEMPLATE.format(job_id=job_id)
    start_time = time.monotonic()

    while time.monotonic() - start_time < POLL_MAX_TOTAL_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            response = requests.get(status_url, headers=headers, timeout=HTTP_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            print(f"[nova_remote_inference_native_tools] poll request failed: {e}")
            return None

        if response.status_code != 200:
            print(f"[nova_remote_inference_native_tools] poll returned {response.status_code}: {response.text[:500]}")
            return None

        data = response.json()
        status = data.get("status")

        if status == "COMPLETED":
            return _extract_native_response(data)
        if status in TERMINAL_STATUSES:
            print(f"[nova_remote_inference_native_tools] job ended with non-success status {status!r}: {data}")
            return None
        # else: still IN_QUEUE / IN_PROGRESS, keep polling

    print(f"[nova_remote_inference_native_tools] poll timed out after {POLL_MAX_TOTAL_SECONDS}s for job {job_id}")
    return None


# ── Quick test ─────────────────────────────────────────────────

if __name__ == "__main__":
    tool_schema = build_tool_schema()
    test_messages = [{"role": "user", "content": "Read the file named notes.txt to see what it says."}]
    result = chat_with_tools(test_messages, tool_schema, max_tokens=200)
    print(result)
