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
#   request:  {"input": {"messages": [...], "max_tokens": N, "temperature": T}}
#   response: {"status": "COMPLETED", "output": [{"choices": [{"tokens": [...]}],
#              "usage": {"input": N, "output": M}}], ...}
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
MODEL_NAME = "Qwen/Qwen2.5-Coder-32B-Instruct-AWQ"

RUNPOD_ENDPOINT_ID = "2ldulpirwqz1vp"
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
# wait. Confirmed live: real cold-start delay was ~30s on a quiet endpoint --
# 180s leaves real headroom above that, not a tight guess.
POLL_INTERVAL_SECONDS = 2.0
POLL_MAX_TOTAL_SECONDS = 180

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


# ── Helpers ────────────────────────────────────────────────────


def _extract_answer(response_json: dict) -> dict | None:
    """
    Parse a COMPLETED RunPod job's response into the same shape
    ollama_client.chat() returns: {"message": {"content": str},
    "prompt_eval_count": int|None, "eval_count": int|None}.

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
        return {
            "message": {"content": answer_text},
            "prompt_eval_count": usage.get("input"),
            "eval_count": usage.get("output"),
        }
    except (KeyError, IndexError, TypeError) as e:
        print(
            f"[nova_remote_inference] UNEXPECTED RESPONSE SHAPE -- schema "
            f"assumption may be wrong: {e} -- raw response: {response_json}"
        )
        return None


# ── Core ───────────────────────────────────────────────────────


def chat(messages: list[dict], num_ctx: int) -> dict | None:
    """
    Send a chat completion request to Nova's RunPod-hosted model
    (Qwen2.5-Coder-32B-Instruct-AWQ) and return an ollama.chat()-shaped
    response dict, or None on any failure so the caller can fall back to
    local Ollama.

    num_ctx is accepted for interface parity with ollama_client.chat() but
    is not forwarded -- see MAX_OUTPUT_TOKENS's comment above.
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
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": SAMPLING_TEMPERATURE,
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
