# nova_remote_inference.py
# RunPod serverless GPU adapter for Nova's own model weights (ClickUp 86baw3010).
#
# Lets nova_query.py's ask() optionally route its one generation call to a
# RunPod-hosted model (Qwen2.5-Coder-32B-Instruct-AWQ) instead of local
# Ollama, for when a task needs a bigger model than the Aero's 8GB card can
# fit. One public entry point, chat(), matching ollama_client.chat()'s
# return shape exactly so nothing downstream in ask() has to change.
#
# Contract: chat() NEVER raises. Any failure (missing API key, network
# error, non-2xx response, poll timeout, or an unexpected response shape)
# is logged and returns None, so the caller falls back to local Ollama
# rather than the whole query failing.
#
# Request/response schema below was confirmed live against the real
# endpoint (not assumed) -- this is RunPod's raw vLLM-worker format from
# its "Deploy LLM from Hugging Face" quick-deploy flow, NOT an
# OpenAI-compatible chat schema:
#   request:  {"input": {"messages": [...], "sampling_params":
#              {"max_tokens": N, "temperature": T}}}
#   response: {"status": "COMPLETED", "output": [{"choices": [{"tokens": [...]}],
#              "usage": {"input": N, "output": M}}], ...}
#
# Real bug found and fixed 2026-07-27: max_tokens/temperature were previously
# sent as flat keys directly under "input" instead of nested under
# "sampling_params" (RunPod worker-vllm's actual documented schema) -- the
# worker silently ignored both and fell back to its own defaults, which
# capped every real response at exactly 100 output tokens regardless of what
# MAX_OUTPUT_TOKENS was set to (confirmed by testing max_tokens=50/300/2000
# against the same prompt -- eval_count came back as exactly 100 every time),
# and meant SAMPLING_TEMPERATURE=0.0 was likely never actually applied
# either. Discovered while spiking a coding-agent tool-use loop against this
# endpoint (nova_runpod_toolcall_spike.py) -- real responses were getting
# truncated mid-JSON, which first looked like a model failure.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_remote_inference.py

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────
# Exposed so callers (nova_query.py) can log which model actually generated
# a response -- the endpoint has exactly one model deployed, there's no
# per-request model selection.
#
# 2026-08-02: tested the unquantized bf16 merged fine-tune
# (zrecoded/nova-qwen-coder-32b-dpo-merged, endpoint mlelod0lpc3rxg) against
# the same 6-task held-out eval, to isolate whether AWQ quantization itself
# was the coding-agent quality bottleneck. Real result: 3/6 clean passes
# (vs. AWQ's typical 1/6) but also one severe, catastrophic scope-violating
# rewrite (task 6) worse than anything the AWQ model produced -- deleted
# live functions, fabricated fake config, introduced an undefined
# cross-module import. Net: does not clearly clear a pass bar against
# Claude's baseline, so reverted to the AWQ production endpoint per the
# standing "only keep it if it clearly clears the bar" rule.
MODEL_NAME = "zrecoded/nova-qwen-coder-32b-awq"

RUNPOD_ENDPOINT_ID = "gwhpxqmae68fgr"
RUNPOD_BASE_URL = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
RUNPOD_RUNSYNC_URL = f"{RUNPOD_BASE_URL}/runsync"
RUNPOD_STATUS_URL_TEMPLATE = f"{RUNPOD_BASE_URL}/status/{{job_id}}"

# Read once at import time -- same convention as nova_query.py's OLLAMA_HOST.
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")

# Cap on generated response length. The RunPod vLLM worker's request schema
# has no equivalent of Ollama's num_ctx (context-window size is a property
# of the deployed model, not a per-request param) -- num_ctx is still
# accepted by chat() for interface parity with ollama_client.chat(), but is
# not forwarded in the request. max_tokens caps OUTPUT length only.
MAX_OUTPUT_TOKENS = 1024
SAMPLING_TEMPERATURE = 0.0  # deterministic, matching this project's other generation calls

# RunPod's own documented ceiling for /runsync before it needs polling.
RUNSYNC_TIMEOUT_SECONDS = 90
# requests' client-side timeout, set just above RUNSYNC_TIMEOUT_SECONDS so
# RunPod's own server-side timeout fires first, not requests'.
HTTP_REQUEST_TIMEOUT_SECONDS = 100

# Poll loop bounds, for jobs that don't finish inline within /runsync's own
# wait. Originally 180s, based on one observed cold-start of ~30s on a quiet
# endpoint. Real gap found 2026-08-02: this endpoint is configured
# workersMin=0/workersMax=1 (confirmed via `nova_runpod_endpoint_deploy.py
# status gwhpxqmae68fgr`) -- it scales to zero whenever idle, so EVERY
# request after an idle gap pays a full cold start (reloading the 32B AWQ
# model onto a fresh GPU instance), not just the first one, and that one
# ~30s sample was never a real distribution. Two real held-out-eval tasks
# hit the old 180s ceiling the same night; raised to 300s to tolerate a
# slower cold start without paying for a continuously-warm worker
# (workersMin=1 would eliminate this entirely but costs real GPU-idle
# dollars -- a deliberate choice not made here).
POLL_INTERVAL_SECONDS = 2.0
POLL_MAX_TOTAL_SECONDS = 300

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}

# This endpoint's real configured GPU tier and RunPod's serverless rate for
# it -- confirmed 2026-07-29 by checking the RunPod dashboard directly (H100
# SXM, $2.99/hr), not assumed or looked up from a generic pricing page.
# RunPod bills on executionTime (real GPU compute time), never delayTime
# (queue/cold-start wait) -- both are captured in a COMPLETED job's response
# but only executionTime feeds the cost calculation below. Needs manual
# updating here if the endpoint's GPU tier is ever changed.
RUNPOD_GPU_HOURLY_RATE_USD = 2.99


def _calculate_cost_usd(execution_time_ms: int | None) -> float | None:
    """
    Real dollar cost for one job, from its real executionTime -- RunPod
    bills per GPU-second, not per token, so this is the only accurate way
    to price a call (see RUNPOD_GPU_HOURLY_RATE_USD's own comment). Returns
    None (not 0.0) when execution_time_ms itself is unknown, so callers can
    tell "unpriced" apart from "genuinely free."
    """
    if execution_time_ms is None:
        return None
    hours = execution_time_ms / 1000 / 3600
    return hours * RUNPOD_GPU_HOURLY_RATE_USD


# ── Helpers ────────────────────────────────────────────────────


def _extract_answer(response_json: dict) -> dict | None:
    """
    Parse a COMPLETED RunPod job's response into the same shape
    ollama_client.chat() returns, plus three extra keys this project's own
    callers use for real cost tracking: {"message": {"content": str},
    "prompt_eval_count": int|None, "eval_count": int|None,
    "execution_time_ms": int|None, "delay_time_ms": int|None,
    "cost_usd": float|None}. The three extra keys are additive -- confirmed
    safe against nova_query.py's own use of this return value, which only
    ever checks `is None` and never enumerates the dict's keys.

    Shared by both the inline-completed and polled-completed paths in
    chat(), since both hit this same output envelope. Logs loudly and
    distinctly on a shape mismatch rather than failing silently -- an
    unexpected shape here means the schema assumption baked into this
    function has gone stale (e.g. RunPod changed the worker), which is a
    real bug to notice, not an ordinary network/timeout failure.
    """
    try:
        result = response_json["output"][0]
        tokens = result["choices"][0]["tokens"]
        answer_text = "".join(tokens)
        usage = result.get("usage", {})
        execution_time_ms = response_json.get("executionTime")
        return {
            "message": {"content": answer_text},
            "prompt_eval_count": usage.get("input"),
            "eval_count": usage.get("output"),
            "execution_time_ms": execution_time_ms,
            "delay_time_ms": response_json.get("delayTime"),
            "cost_usd": _calculate_cost_usd(execution_time_ms),
        }
    except (KeyError, IndexError, TypeError) as e:
        print(
            f"[nova_remote_inference] UNEXPECTED RESPONSE SHAPE -- schema "
            f"assumption may be wrong: {e} -- raw response: {response_json}"
        )
        return None


# ── Core ───────────────────────────────────────────────────────


def chat(messages: list[dict], num_ctx: int, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict | None:
    """
    Send a chat completion request to Nova's RunPod-hosted model
    (Qwen2.5-Coder-32B-Instruct-AWQ) and return an ollama.chat()-shaped
    response dict, or None on any failure so the caller can fall back to
    local Ollama.

    num_ctx is accepted for interface parity with ollama_client.chat() but
    is not forwarded -- see MAX_OUTPUT_TOKENS's comment above. max_tokens
    defaults to MAX_OUTPUT_TOKENS (nova_query.py's existing RAG-path call
    keeps that behavior unchanged) but can be overridden by callers needing
    a larger cap -- e.g. the coding sub-agent's RunPod backend, where a
    single write_file tool call can run several thousand tokens.
    """
    if not RUNPOD_API_KEY:
        print("[nova_remote_inference] RUNPOD_API_KEY not set -- skipping remote call")
        return None

    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "messages": messages,
            "sampling_params": {
                "max_tokens": max_tokens,
                "temperature": SAMPLING_TEMPERATURE,
            },
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
        print(f"[nova_remote_inference] request to RunPod failed: {e}")
        return None

    if response.status_code != 200:
        print(f"[nova_remote_inference] RunPod returned {response.status_code}: {response.text[:500]}")
        return None

    data = response.json()
    status = data.get("status")

    if status == "COMPLETED":
        return _extract_answer(data)

    if status in ("IN_QUEUE", "IN_PROGRESS"):
        return _poll_until_terminal(data["id"], headers)

    print(f"[nova_remote_inference] unexpected initial status {status!r}: {data}")
    return None


def _poll_until_terminal(job_id: str, headers: dict) -> dict | None:
    """
    Poll RunPod's /status/{id} endpoint until the job reaches a terminal
    status or POLL_MAX_TOTAL_SECONDS elapses. Reached when /runsync itself
    doesn't finish inline (real cold-start jobs observed live taking ~30s,
    exceeding runsync's inline wait but well within this poll budget).
    """
    status_url = RUNPOD_STATUS_URL_TEMPLATE.format(job_id=job_id)
    start_time = time.monotonic()

    while time.monotonic() - start_time < POLL_MAX_TOTAL_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            response = requests.get(status_url, headers=headers, timeout=HTTP_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            print(f"[nova_remote_inference] poll request failed: {e}")
            return None

        if response.status_code != 200:
            print(f"[nova_remote_inference] poll returned {response.status_code}: {response.text[:500]}")
            return None

        data = response.json()
        status = data.get("status")

        if status == "COMPLETED":
            return _extract_answer(data)
        if status in TERMINAL_STATUSES:
            print(f"[nova_remote_inference] job ended with non-success status {status!r}: {data}")
            return None
        # else: still IN_QUEUE / IN_PROGRESS, keep polling

    print(f"[nova_remote_inference] poll timed out after {POLL_MAX_TOTAL_SECONDS}s for job {job_id}")
    return None


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    test_messages = [
        {"role": "system", "content": "You are a test."},
        {"role": "user", "content": "Say OK and nothing else."},
    ]
    result = chat(test_messages, num_ctx=2048)
    if result is None:
        print("FAIL: chat() returned None")
        sys.exit(1)
    print("OK:", result)
