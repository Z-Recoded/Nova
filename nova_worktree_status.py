# nova_worktree_status.py
# Read-only git worktree inventory across both machines, for the Nova
# Controller worktree browser (86bb3ceyc).
#
# Real motivating find (straight from the ClickUp task text): two stale
# sandbox-verify-test* worktrees from the original Docker-sandboxing PR
# verification were discovered sitting unmerged on the Omen on 2026-07-25,
# unnoticed until manually checked. This exists so stale worktrees don't
# silently pile up again. Ties to the existing harness-pruning backlog item
# (86baxc3xy).
#
# nova_omen_dispatch.py already has _snapshot_omen_worktrees() (SSH
# `git worktree list --porcelain`, used to diff for one new path after a
# dispatch) -- good reference for the SSH mechanics, but it only returns a
# bare path set, not branch/age/merged detail, and it's scoped to
# "diff before/after," not "list everything now." This is a separate,
# purpose-built module rather than stretching that one.
#
# Platform-aware for the same reason as nova_agent_log_status.py's
# get_combined_status(): nova_api.py runs on both the Aero (dev) and the
# Omen (production, what the phone Controller actually hits). As of the
# 2026-07-25 SSH follow-up, both directions are real and live -- see that
# module's docstring for the full reasoning, reused here rather than
# re-derived. The Aero still sleeps sometimes, so a real, live-but-partial
# view remains possible either direction -- see get_worktree_status()'s
# own docstring for the "view" field this now reports.

import os
import subprocess
import sys
from datetime import datetime

# Same three constants as nova_omen_dispatch.py/nova_omen_sync.py/
# nova_agent_log_status.py -- duplicated rather than imported, matching
# this repo's existing convention of each script staying self-contained.
OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

# This file lives at the repo root, so its own directory IS the local repo root.
AERO_REPO_PATH = os.path.dirname(os.path.abspath(__file__))

# Reverse direction (86bb3cey2/86bb3ceyc SSH follow-up, 2026-07-25) — real,
# live, and verified: a dedicated ed25519 key restricted via authorized_keys
# `command=` to exactly one forced script on the Aero (see
# scripts/setup_omen_to_aero_ssh.ps1). Private key half lives only on the
# Omen (~/.ssh/aero_keys/) — only resolves to something real when
# list_aero_worktrees() runs there. AERO_REPO_PATH_REMOTE is a separate,
# hardcoded string (not AERO_REPO_PATH's own __file__-relative resolution,
# which only makes sense for whichever machine this code is actually
# running on) -- it's what git on the Aero itself reports as its worktree
# path, confirmed live via a real SSH round-trip.
AERO_HOST = "100.122.229.23"  # Tailscale IP
AERO_USER = "marvi"
AERO_WORKTREES_KEY = os.path.expanduser("~/.ssh/aero_keys/id_ed25519_aero_worktrees")
AERO_REPO_PATH_REMOTE = "C:/Nova"

SSH_TIMEOUT_SECONDS = 20
LOCAL_TIMEOUT_SECONDS = 10

MERGED_CMD = ["git", "branch", "--merged", "master", "--format=%(refname:short)"]
DATES_CMD = ["git", "for-each-ref", "--format=%(refname:short)|%(committerdate:iso-strict)", "refs/heads"]
WORKTREE_CMD = ["git", "worktree", "list", "--porcelain"]


def _parse_worktree_porcelain(text: str) -> list[dict]:
    """
    Parse `git worktree list --porcelain` output into a list of
    {"path", "branch" (None if detached), "locked", "prunable"} dicts.
    """
    entries: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current = {"path": line[len("worktree ") :], "branch": None, "locked": False, "prunable": False}
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            current["branch"] = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    if current:
        entries.append(current)
    return entries


def _is_main_worktree(path: str, repo_root: str) -> bool:
    """
    True if `path` (a worktree entry's own path, in whatever slash/case
    convention that machine's git reported it in) is the main worktree --
    i.e. the repo root itself, not a disposable one. Normalizes slashes and
    case rather than assuming the main worktree is always index 0 of the
    porcelain listing (true in practice, but this is a cheap, more robust
    check against a known path instead of a positional guess).
    """
    normalize = lambda p: p.replace("\\", "/").rstrip("/").lower()  # noqa: E731
    return normalize(path) == normalize(repo_root)


def _parse_kv_lines(text: str) -> dict:
    """Parse `name|value` lines (as produced by DATES_CMD) into a dict, skipping malformed lines."""
    result = {}
    for line in text.splitlines():
        if "|" in line:
            name, _, value = line.partition("|")
            result[name.strip()] = value.strip()
    return result


def _build_entries(worktrees: list[dict], repo_root: str, merged_branches: set, commit_dates: dict) -> list[dict]:
    """
    Turn parsed porcelain entries (minus the main worktree) into the
    browser's real payload: age in days since the branch's tip commit, and
    whether that branch is already merged into local master.
    """
    entries = []
    for wt in worktrees:
        if _is_main_worktree(wt["path"], repo_root):
            continue

        branch = wt["branch"]
        commit_date = commit_dates.get(branch) if branch else None
        age_days = None
        if commit_date:
            try:
                commit_dt = datetime.fromisoformat(commit_date)
                age_days = round((datetime.now(commit_dt.tzinfo) - commit_dt).total_seconds() / 86400, 1)
            except ValueError:
                pass

        entries.append(
            {
                "path": wt["path"],
                "branch": branch,
                "locked": wt["locked"],
                "prunable": wt["prunable"],
                "last_commit_at": commit_date,
                "age_days": age_days,
                "merged": (branch in merged_branches) if branch else None,
            }
        )
    return entries


def list_local_worktrees() -> dict:
    """
    Enumerate worktrees on whichever machine this code is actually running
    on (the Aero when called from Aero-hosted nova_api.py, the Omen when
    called from Omen-hosted nova_api.py) via three local `git` calls in
    AERO_REPO_PATH. Never raises -- returns {"entries": [], "error": "..."}
    on any failure.
    """
    try:
        porcelain = subprocess.run(
            WORKTREE_CMD, cwd=AERO_REPO_PATH, capture_output=True, text=True, timeout=LOCAL_TIMEOUT_SECONDS
        )
        merged = subprocess.run(
            MERGED_CMD, cwd=AERO_REPO_PATH, capture_output=True, text=True, timeout=LOCAL_TIMEOUT_SECONDS
        )
        dates = subprocess.run(
            DATES_CMD, cwd=AERO_REPO_PATH, capture_output=True, text=True, timeout=LOCAL_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "local git commands timed out"}

    if porcelain.returncode != 0:
        return {"entries": [], "error": f"git worktree list exited {porcelain.returncode}: {porcelain.stderr.strip()}"}

    worktrees = _parse_worktree_porcelain(porcelain.stdout)
    merged_branches = {line.strip() for line in merged.stdout.splitlines() if line.strip()}
    commit_dates = _parse_kv_lines(dates.stdout)

    return {"entries": _build_entries(worktrees, AERO_REPO_PATH, merged_branches, commit_dates), "error": None}


def list_omen_worktrees() -> dict:
    """
    Fetch the Omen's worktree inventory over SSH — one round-trip carrying
    all three git commands (`&&`-chained with plain-text markers between
    sections), not three separate SSH calls. Same host/user/path
    nova_omen_dispatch.py/nova_agent_log_status.py already use. Never
    raises: {"entries": [], "error": "..."} if the Omen is unreachable.
    """
    remote_cmd = (
        f"cd {OMEN_REPO_PATH} && git worktree list --porcelain "
        f"&& echo '===MERGED===' && git branch --merged master --format='%(refname:short)' "
        f"&& echo '===DATES===' && git for-each-ref --format='%(refname:short)|%(committerdate:iso-strict)' refs/heads"  # noqa: E501
    )
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_cmd],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "SSH to the Omen timed out"}

    if result.returncode != 0:
        return {"entries": [], "error": f"ssh exited {result.returncode}: {result.stderr.strip()}"}

    porcelain_part, _, rest = result.stdout.partition("===MERGED===\n")
    merged_part, _, dates_part = rest.partition("===DATES===\n")

    worktrees = _parse_worktree_porcelain(porcelain_part)
    merged_branches = {line.strip() for line in merged_part.splitlines() if line.strip()}
    commit_dates = _parse_kv_lines(dates_part)

    return {"entries": _build_entries(worktrees, OMEN_REPO_PATH, merged_branches, commit_dates), "error": None}


def list_aero_worktrees() -> dict:
    """
    Fetch the Aero's worktree inventory over SSH, using the dedicated
    command-restricted key. Unlike list_omen_worktrees(), no `&&`-chained
    remote command is needed here -- the forced command itself
    (scripts/ssh_read_worktrees.ps1 on the Aero) already does that
    chaining server-side and emits the identical "===MERGED==="/"===DATES==="
    markers, so the same parsing functions apply unchanged. The command
    string passed to ssh here is irrelevant -- the forced command always
    runs regardless of what's actually requested.
    """
    try:
        result = subprocess.run(
            ["ssh", "-i", AERO_WORKTREES_KEY, "-o", "ConnectTimeout=10", f"{AERO_USER}@{AERO_HOST}", "ignored"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "SSH to the Aero timed out"}

    if result.returncode != 0:
        return {"entries": [], "error": f"ssh exited {result.returncode}: {result.stderr.strip()}"}

    porcelain_part, _, rest = result.stdout.partition("===MERGED===\n")
    merged_part, _, dates_part = rest.partition("===DATES===\n")

    worktrees = _parse_worktree_porcelain(porcelain_part)
    merged_branches = {line.strip() for line in merged_part.splitlines() if line.strip()}
    commit_dates = _parse_kv_lines(dates_part)

    return {"entries": _build_entries(worktrees, AERO_REPO_PATH_REMOTE, merged_branches, commit_dates), "error": None}


def get_worktree_status() -> dict:
    """
    Symmetric combined view, backing the Controller's worktree browser
    (GET /worktree-status). Directly modeled on
    nova_agent_log_status.get_combined_status() -- see that module's own
    docstring for the full "local + real live SSH to the other machine"
    reasoning. Whichever machine this runs on, "local" is this machine's
    own worktrees and the other machine's are fetched over a real,
    command-restricted SSH key (both directions now exist and are
    verified live). `view` tells a caller what it actually got: "combined"
    (both sides real), "omen_only" (served from the Omen, the Aero
    couldn't be reached right now -- e.g. asleep), or "aero_only" (served
    from the Aero, the Omen couldn't be reached).
    """
    local = list_local_worktrees()
    on_aero = sys.platform == "win32"

    remote = list_omen_worktrees() if on_aero else list_aero_worktrees()
    aero = local if on_aero else remote
    omen = remote if on_aero else local

    view = "combined" if remote["error"] is None else ("aero_only" if on_aero else "omen_only")
    return {"view": view, "aero": aero, "omen": omen}


if __name__ == "__main__":
    import json

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(get_worktree_status(), indent=2))
