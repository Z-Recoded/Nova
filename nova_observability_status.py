# nova_observability_status.py
# Cross-machine guard/gate log data for Observability Initiative Phase 3's
# /observability/per-model and /observability/failure-frequency.
#
# Real bug found live 2026-08-06: both routes were returning near-empty
# results whenever served from the Omen -- guard_events_log.jsonl and
# ground_truth_gate_log.jsonl don't exist there at all, and the Omen's own
# agent_log.jsonl has only 137 lines vs. the Aero's 4,765. Almost all real
# coding-agent activity happens on the Aero (interactive lane + local
# nova_coding_eval.py runs); the Omen's headless dispatch is comparatively
# rare. nova_observability_dashboard.py's per_model_comparison()/
# failure_frequency_over_time() only ever read local files, so a route
# served from the Omen was silently showing a near-empty picture.
#
# Same cross-machine pattern nova_agent_log_status.py already established
# for /qwen-swap-status -- read_local/read_omen/read_aero, platform-aware
# combine, a "view" field ("combined"/"omen_only"/"aero_only") that's never
# silently presented as complete when it isn't. Deliberately a separate
# small module, not folded into nova_observability_dashboard.py itself --
# matches this repo's existing one-file-per-cross-machine-concern precedent
# (nova_agent_log_status.py, nova_worktree_status.py,
# nova_training_data_status.py).
#
# The agent_log.jsonl side of the model-join reuses
# nova_agent_log_status.read_omen_agent_log()/read_aero_agent_log() directly
# rather than re-implementing that fetch -- already built, already tested,
# same SSH key (agentlog) already covers it.

import json
import os
import subprocess
import sys

from nova_agent_log_status import read_aero_agent_log, read_omen_agent_log
from nova_guard_stats import GROUND_TRUTH_GATE_LOG_PATH, GUARD_EVENTS_LOG_PATH, LOGS_DIR, _parse_jsonl

# Same three constants as nova_agent_log_status.py/nova_omen_sync.py --
# duplicated rather than imported, matching this repo's existing convention
# of each cross-machine script staying self-contained.
OMEN_HOST = "100.114.197.117"
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

AERO_HOST = "100.122.229.23"
AERO_USER = "marvi"
# New key, 2026-08-06 -- see scripts/setup_omen_to_aero_ssh.ps1 step 5 and
# scripts/ssh_read_observability_logs.ps1. Bundles both files into one JSON
# response (one SSH round-trip), unlike the single-file agentlog key.
AERO_OBSERVABILITY_LOGS_KEY = os.path.expanduser("~/.ssh/aero_keys/id_ed25519_aero_observabilitylogs")

SSH_TIMEOUT_SECONDS = 15


def read_local_observability_logs() -> dict:
    """This machine's own two log files. Empty lists for whichever doesn't exist yet."""
    return {
        "guard_events": _parse_jsonl(GUARD_EVENTS_LOG_PATH),
        "ground_truth_gate": _parse_jsonl(GROUND_TRUTH_GATE_LOG_PATH),
    }


def read_omen_observability_logs() -> dict:
    """
    Fetch the Omen's two log files over SSH — same host/user/path
    nova_agent_log_status.read_omen_agent_log() already uses, the Aero's
    full-access login key (no restriction needed, same reasoning as that
    function's own docstring). Returns {"guard_events": [...],
    "ground_truth_gate": [...], "error": None} on success, or empty lists
    + a real error string if the Omen is unreachable. Never raises.
    """
    try:
        guard_result = subprocess.run(
            [
                "ssh",
                f"{OMEN_USER}@{OMEN_HOST}",
                f"cat {OMEN_REPO_PATH}/logs/guard_events_log.jsonl 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        gate_result = subprocess.run(
            [
                "ssh",
                f"{OMEN_USER}@{OMEN_HOST}",
                f"cat {OMEN_REPO_PATH}/logs/ground_truth_gate_log.jsonl 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if guard_result.returncode != 0 or gate_result.returncode != 0:
            return {
                "guard_events": [],
                "ground_truth_gate": [],
                "error": f"ssh exited non-zero: {guard_result.stderr.strip() or gate_result.stderr.strip()}",
            }
        return {
            "guard_events": _parse_jsonl_text(guard_result.stdout),
            "ground_truth_gate": _parse_jsonl_text(gate_result.stdout),
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"guard_events": [], "ground_truth_gate": [], "error": "SSH to the Omen timed out"}


def read_aero_observability_logs() -> dict:
    """
    Fetch the Aero's two log files over SSH, using the dedicated
    command-restricted key (see scripts/setup_omen_to_aero_ssh.ps1) — only
    ever attempted when running on the Omen itself. The forced script
    (scripts/ssh_read_observability_logs.ps1) bundles both files into one
    JSON response regardless of what's actually requested here, same shape
    as nova_agent_log_status.read_aero_agent_log(). Never raises,
    {"guard_events": [], "ground_truth_gate": [], "error": "..."} if the
    Aero is unreachable (e.g. asleep) or the key isn't set up yet.
    """
    try:
        result = subprocess.run(
            [
                "ssh",
                "-i",
                AERO_OBSERVABILITY_LOGS_KEY,
                "-o",
                "ConnectTimeout=10",
                f"{AERO_USER}@{AERO_HOST}",
                "ignored",
            ],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {
                "guard_events": [],
                "ground_truth_gate": [],
                "error": f"ssh exited {result.returncode}: {result.stderr.strip()}",
            }
        payload = json.loads(result.stdout)
        return {
            "guard_events": _parse_jsonl_text(payload.get("guard_events", "")),
            "ground_truth_gate": _parse_jsonl_text(payload.get("ground_truth_gate", "")),
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"guard_events": [], "ground_truth_gate": [], "error": "SSH to the Aero timed out"}
    except json.JSONDecodeError as e:
        return {"guard_events": [], "ground_truth_gate": [], "error": f"malformed response from the Aero: {e}"}


def _parse_jsonl_text(raw_text: str) -> list[dict]:
    """Parse a JSONL blob into a list of dicts, silently skipping blank/malformed lines."""
    entries = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def get_combined_observability_data() -> dict:
    """
    Merge this machine's local guard/gate/agent_log data with the other
    machine's fetched-over-SSH copy. Platform-aware (sys.platform ==
    "win32" means "I'm the Aero"), same "view" convention as
    nova_agent_log_status.get_combined_status(): "combined" (both sides
    real), "omen_only"/"aero_only" (the other machine couldn't be reached
    right now). A given branch's guard/gate entries are only ever written
    by the one machine that actually ran that task, so concatenating local
    + remote is a safe merge -- no real duplicate-branch conflict to
    resolve, unlike a value that could genuinely differ per machine.

    Returns {"view": ..., "guard_entries": [...], "gate_entries": [...],
    "agent_log_entries": [...]}.
    """
    local = read_local_observability_logs()
    on_aero = sys.platform == "win32"

    remote = read_omen_observability_logs() if on_aero else read_aero_observability_logs()

    guard_entries = local["guard_events"] + remote["guard_events"]
    gate_entries = local["ground_truth_gate"] + remote["ground_truth_gate"]

    # Reuse the already-built, already-tested agent_log fetch -- same SSH
    # key (agentlog) already covers this, no need to duplicate it here.
    agent_log_local = _parse_jsonl(os.path.join(LOGS_DIR, "agent_log.jsonl"))
    agent_log_remote_result = read_omen_agent_log() if on_aero else read_aero_agent_log()
    agent_log_entries = agent_log_local + agent_log_remote_result["entries"]

    view = "combined" if remote["error"] is None else ("aero_only" if on_aero else "omen_only")

    return {
        "view": view,
        "guard_entries": guard_entries,
        "gate_entries": gate_entries,
        "agent_log_entries": agent_log_entries,
    }
