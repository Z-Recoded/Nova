# nova_langfuse_client.py
# Langfuse Cloud connectivity for Nova's Observability Initiative (86bb7pamh),
# Phase 0 (86bb7par3).
#
# Deliberately connectivity-verification ONLY -- reads credentials, builds a
# client, and proves a real trace reaches the dashboard. The real turn-loop
# instrumentation (reasoning/tool-calls/token-uncertainty wired into BOTH
# nova_orchestrator.run_coding_task() AND nova_coding_eval.py's eval path,
# per the G2 lesson -- a mechanism that only fires from one call site isn't
# really built) is Phase 1's job, deliberately not started here.
#
# Cloud, not self-hosted: Langfuse v3's official self-hosted stack (Postgres/
# ClickHouse/Redis/MinIO/web/worker, 6 containers) recommends 4 vCPU/16GB RAM
# for VM deployments -- the Omen has 7.64GB RAM total and already runs real
# production services (nova-api, nova-chroma). Langfuse v2 (lighter, no
# ClickHouse) was checked and ruled out: unmaintained since end of Q1 2025,
# a bad tradeoff for RAM savings on an internet-adjacent box. Decided with
# Marvin (2026-08-03): Langfuse Cloud free tier instead -- a deliberate,
# acknowledged exception to Nova's usual local-first default (trace content
# leaves the local network), made knowingly, not by accident.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_langfuse_client.py

import os
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
# LANGFUSE_BASE_URL, not LANGFUSE_HOST -- confirmed directly against the
# installed SDK's own Langfuse.__init__() source: base_url/LANGFUSE_BASE_URL
# take precedence over host/LANGFUSE_HOST in its real env-var resolution
# order, and LANGFUSE_BASE_URL is what a real Langfuse Cloud project's own
# "get API keys" page hands you to copy-paste.
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL")


def get_client() -> Langfuse | None:
    """
    Build a Langfuse client from this repo's own explicit env vars (same
    discipline as every other credential in this codebase -- read once via
    os.environ.get(), not implicit env-var auto-detection). Returns None if
    any of the three required values is missing, so a caller can fail
    toward "tracing is off" rather than crash -- Phase 1's real
    instrumentation will need this same fail-open discipline once it's
    wired into the production turn loop.
    """
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL):
        return None
    return Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY, base_url=LANGFUSE_BASE_URL)


def verify_connectivity() -> str | None:
    """
    Real, live proof the configured Langfuse Cloud project is reachable --
    not just "no exception was raised." auth_check() confirms the key pair
    is valid; a real observation is then created, flushed, and its trace
    URL returned, so this can be opened in a browser and visually confirmed
    rather than trusted from a boolean alone (same "verify the payload, not
    just the status code" discipline as CLAUDE.md's Omen deployment lesson).
    Returns None if credentials are missing.
    """
    client = get_client()
    if client is None:
        print("[nova_langfuse_client] LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL not fully set -- skipping")
        return None

    client.auth_check()
    print("[nova_langfuse_client] auth_check() passed -- credentials are valid")

    with client.start_as_current_observation(
        name="nova-phase-0-connectivity-check",
        as_type="span",
        input="Phase 0 sanity check (86bb7par3)",
        output="ok",
    ):
        trace_url = client.get_trace_url()

    client.flush()
    return trace_url


if __name__ == "__main__":
    url = verify_connectivity()
    if url:
        print(f"[nova_langfuse_client] Real trace sent -- view it here: {url}")
    else:
        print("[nova_langfuse_client] Connectivity check did not complete -- see messages above.")
