# nova_training_data_status.py
# Cross-machine DPO pair count for /training-data-status (86bax4akx), fixed
# for the same "Omen can't see Aero-only data" gap already found and fixed
# in nova_agent_log_status.py (Qwen swap-trigger widget) and
# nova_worktree_status.py (worktree browser) on 2026-07-25.
#
# training_flags.jsonl is written by nova_logger.py's log_blend(), which
# only ever fires from an interactive nova_query.ask() call -- today that
# means the Aero (Open WebUI / nova_chat.py), never the Omen's headless
# dispatch path. Confirmed live: the Omen has no training_flags.jsonl file
# at all, not even a misplaced one. So /training-data-status, when served
# from the Omen (the machine the phone Controller actually hits), was
# reading a local file that can never have real data -- reporting 0/100
# while the Aero's real count was 33/100. Not a hardcoded-path bug (the
# path already resolves relative to __file__) -- a missing cross-machine
# fetch, same root cause as the other two widgets.
#
# Platform-aware exactly like nova_agent_log_status.get_combined_status():
# "local" is whichever machine this runs on, the other machine's data is
# fetched over SSH -- Aero-to-Omen already existed, Omen-to-Aero is the
# 2026-07-25 command-restricted bridge (scripts/setup_omen_to_aero_ssh.ps1).
# `view` is "combined" (both sides real), "omen_only", or "aero_only" --
# never silently presented as complete when it isn't.

import json
import os
import subprocess
import sys

# Same three constants as nova_omen_dispatch.py/nova_agent_log_status.py/
# nova_worktree_status.py -- duplicated rather than imported, matching this
# repo's existing convention of each script staying self-contained.
OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

# Third command-restricted Omen->Aero key (2026-07-26 follow-up to the
# 2026-07-25 SSH bridge) -- a dedicated key per forced script, same
# discipline as the agentlog/worktrees keys, not a reused one. Private key
# half lives only on the Omen (~/.ssh/aero_keys/) -- only resolves to
# something real when read_aero_training_flags() runs there.
AERO_HOST = "100.122.229.23"  # Tailscale IP
AERO_USER = "marvi"
AERO_TRAININGDATA_KEY = os.path.expanduser("~/.ssh/aero_keys/id_ed25519_aero_trainingdata")

# Resolved relative to this file's own location -- same GRAPH_PATH-class
# fix already applied everywhere else in this project.
LOCAL_TRAINING_FLAGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "training_flags.jsonl")
SSH_TIMEOUT_SECONDS = 15

# Duplicated from nova_api.py's own MIN_REAL_PAIRS_FOR_FINETUNE (itself a
# deliberate duplicate of nova_finetune_phi4.MIN_REAL_PAIRS, so nova_api.py
# never depends on the training stack being installed) -- kept in sync by
# hand, flagged in both places.
MIN_REAL_PAIRS_FOR_FINETUNE = 100


def _parse_jsonl(raw_text: str) -> list[dict]:
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


def read_local_training_flags() -> list[dict]:
    """Read this machine's own logs/training_flags.jsonl. Empty list if it doesn't exist yet."""
    try:
        with open(LOCAL_TRAINING_FLAGS_PATH, encoding="utf-8") as f:
            return _parse_jsonl(f.read())
    except FileNotFoundError:
        return []


def read_omen_training_flags() -> dict:
    """
    Fetch the Omen's logs/training_flags.jsonl over the pre-existing
    Aero-to-Omen SSH path (same host/user/path every other cross-machine
    script here uses). Returns {"entries": [...], "error": None} on
    success -- an empty list with no error is the expected, honest result
    today, since the Omen genuinely has no real training data yet. Never
    raises: a status check shouldn't fail outright just because the Omen
    happens to be off.
    """
    remote_path = f"{OMEN_REPO_PATH}/logs/training_flags.jsonl"
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", f"cat {remote_path} 2>/dev/null || true"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {"entries": [], "error": f"ssh exited {result.returncode}: {result.stderr.strip()}"}
        return {"entries": _parse_jsonl(result.stdout), "error": None}
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "SSH to the Omen timed out"}


def read_aero_training_flags() -> dict:
    """
    Fetch the Aero's logs/training_flags.jsonl over SSH, using the
    dedicated command-restricted key (see scripts/setup_omen_to_aero_ssh.ps1)
    -- only ever attempted when running on the Omen itself (see
    get_combined_training_status()). The command string passed here is
    irrelevant: the key's forced command
    (scripts/ssh_read_training_flags.ps1 on the Aero) always runs
    regardless of what's actually requested. Same shape as
    read_omen_training_flags(): never raises, {"entries": [], "error": "..."}
    if the Aero is unreachable (e.g. asleep) or the key isn't set up yet.
    """
    try:
        result = subprocess.run(
            ["ssh", "-i", AERO_TRAININGDATA_KEY, "-o", "ConnectTimeout=10", f"{AERO_USER}@{AERO_HOST}", "ignored"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {"entries": [], "error": f"ssh exited {result.returncode}: {result.stderr.strip()}"}
        return {"entries": _parse_jsonl(result.stdout), "error": None}
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "SSH to the Aero timed out"}


def _summarize(entries: list[dict]) -> dict:
    """
    Compute the same stats /training-data-status has always reported --
    moved here unchanged from nova_api.py so both the combined route and
    a future CLI use identical logic.
    """
    corrected = [e for e in entries if e.get("correction")]
    total_corrected = len(corrected)

    by_category: dict = {}
    for e in corrected:
        category = e.get("category", "uncategorized")
        by_category[category] = by_category.get(category, 0) + 1

    verified_good = sum(1 for e in corrected if e.get("verification_status") == "confirmed_good")
    needs_rework = sum(1 for e in corrected if e.get("verification_status") == "needs_rework")

    return {
        "total_flagged": len(entries),
        "total_corrected": total_corrected,
        "by_category": by_category,
        "verified_good": verified_good,
        "needs_rework": needs_rework,
        "unverified": total_corrected - verified_good - needs_rework,
    }


def get_combined_training_status() -> dict:
    """
    Merge the Aero's local training_flags.jsonl with the Omen's copy
    (fetched over SSH in whichever direction this machine needs), and
    report real progress toward MIN_REAL_PAIRS_FOR_FINETUNE. Backs
    GET /training-data-status.

    Platform-aware exactly like nova_agent_log_status.get_combined_status()
    and nova_worktree_status.get_worktree_status() -- see those modules'
    docstrings for the full "local + real live SSH to the other machine"
    reasoning. `view` is "combined" (both sides real, regardless of which
    machine served the request), "omen_only" (served from the Omen, the
    Aero couldn't be reached right now -- e.g. asleep), or "aero_only"
    (served from the Aero, the Omen couldn't be reached). In practice the
    Omen side of "combined" is usually an honest zero today -- it has no
    real training data yet, only the Aero does -- but that's a fact about
    the data, not a reason to hide the view field.
    """
    local_entries = read_local_training_flags()
    on_aero = sys.platform == "win32"

    remote_result = read_omen_training_flags() if on_aero else read_aero_training_flags()
    remote_entries = remote_result["entries"]

    aero_entries = local_entries if on_aero else remote_entries
    omen_entries = remote_entries if on_aero else local_entries

    if remote_result["error"] is None:
        view = "combined"
    else:
        view = "aero_only" if on_aero else "omen_only"

    combined_entries = aero_entries + omen_entries
    summary = _summarize(combined_entries)

    total_corrected = summary["total_corrected"]
    summary.update(
        {
            "view": view,
            "min_pairs_for_finetune": MIN_REAL_PAIRS_FOR_FINETUNE,
            "pairs_remaining": max(0, MIN_REAL_PAIRS_FOR_FINETUNE - total_corrected),
            "progress_pct": round(100 * total_corrected / MIN_REAL_PAIRS_FOR_FINETUNE, 1),
            "threshold_met": total_corrected >= MIN_REAL_PAIRS_FOR_FINETUNE,
        }
    )
    return summary


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(get_combined_training_status(), indent=2))
