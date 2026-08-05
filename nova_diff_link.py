# nova_diff_link.py
# Trace-to-diff linking for Observability Initiative Phase 4 (86bb7pb6t) --
# given a branch name (as already carried by each point in the
# /observability/uncertainty payload), resolves what's actually viewable:
# a real GitHub compare link if the branch was pushed, a local diff if its
# ref exists on this machine, a diff fetched from the OTHER machine if it
# only exists there, or an honest "not available" result.
#
# Most historical coding-agent branches are local-only and get pruned once
# reviewed (nova_orchestrator.py never runs `git push` on its own worktrees
# -- confirmed by grep), so "diff no longer available" is an expected,
# first-class result here, not an error case.
#
# Cross-machine leg (86bb7pb6t follow-up, 2026-08-05): originally shipped
# same-machine-only, deferring cross-machine coverage because the mechanism
# it would need (a forced Omen-to-Aero SSH command running git.exe) was
# unverified -- nova_worktree_pr.py's docstring claimed that class of
# session can't run git.exe/python.exe at all. Confirmed live that this
# overgeneralized from what it actually found for python.exe:
# ssh_read_worktrees.ps1 (a forced command using the pre-existing
# AERO_WORKTREES_KEY) runs git.exe directly and works. This module's
# cross-machine functions reuse that same proof -- a NEW dedicated key
# (AERO_DIFF_KEY, matching this bridge's one-key-per-capability
# convention) running git directly via a sibling forced script
# (scripts/ssh_read_aero_diff.ps1), not a relay through nova_api.py.
#
# Reuses nova_worktree_pr.DISPATCH_BRANCH_PATTERN rather than redefining an
# already-correct regex for that lane.

import json
import os
import re
import subprocess
import sys

from nova_worktree_pr import DISPATCH_BRANCH_PATTERN

# This file lives at the repo root, so its own directory IS the local repo root.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

GITHUB_REPO_URL = "https://github.com/Z-Recoded/Nova"

# Same three constants as nova_worktree_status.py/nova_worktree_pr.py --
# duplicated rather than imported, matching this repo's existing
# convention of each script staying self-contained.
OMEN_HOST = "100.114.197.117"  # Tailscale IP
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

AERO_HOST = "100.122.229.23"  # Tailscale IP
AERO_USER = "marvi"
AERO_DIFF_KEY = os.path.expanduser("~/.ssh/aero_keys/id_ed25519_aero_diff")

SSH_TIMEOUT_SECONDS = 20

# Deliberately NOT anchored to nova_orchestrator._slugify()'s exact output
# shape (lowercase slug + "-YYYYMMDD-HHMMSS") -- a real branch found live
# while testing this feature, "nova-agent/phase2-verify-guard-gate" (created
# by a direct verification call bypassing the normal _slugify() entry
# point), doesn't have that timestamp suffix and would be wrongly rejected
# by a stricter pattern. The real guarantee this needs is structural, not
# an exact-shape match: stay inside the "nova-agent/" namespace, don't let
# the first character after the slash be "-" (avoids any ambiguity with a
# git flag once this reaches `git diff master...<branch>`), and don't allow
# another "/" (blocks path-traversal-shaped input like ".."). subprocess.run
# is always called in list form in this module (never shell=True), so
# traditional shell injection isn't the threat model here -- this regex is
# about keeping branch values inside the one real namespace this app
# creates, not escaping shell metacharacters.
NOVA_AGENT_BRANCH_PATTERN = re.compile(r"^nova-agent/[a-z0-9][a-z0-9-]*$")

GIT_TIMEOUT_SECONDS = 15


def _validate_branch(branch: str) -> str | None:
    """
    Error string if `branch` matches neither known real branch-naming
    pattern, else None. MUST be called before any subprocess call in this
    module -- branch names flow into `git diff master...<branch>`, a real
    shell-adjacent injection surface, and this is the one gate standing
    between "a dashboard click" and "an arbitrary git revision argument."
    """
    if NOVA_AGENT_BRANCH_PATTERN.match(branch or ""):
        return None
    if DISPATCH_BRANCH_PATTERN.match(branch or ""):
        return None
    return f"'{branch}' doesn't look like a real Nova coding-agent branch"


def _origin_link(branch: str) -> str | None:
    """
    `git ls-remote` is local and read-only -- works from either machine
    even though the Omen's deploy key can't push, and needs no new
    dependency (unlike `gh`, which per nova_worktree_pr.py's own docstring
    is only installed on the Aero). Returns a GitHub compare URL if the ref
    exists on origin, else None. A compare link works whether or not a PR
    was ever formally opened, so this doesn't need to know that separately.
    Never raises.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None
    return f"{GITHUB_REPO_URL}/compare/master...{branch}"


def _local_diff(branch: str) -> dict:
    """
    Real diff text for `branch` against master, from THIS machine's own
    repo -- only meaningful if the branch's ref still exists here (its
    worktree may already be gone; a branch ref survives `git worktree
    remove`, only `git branch -D` actually deletes it). Never raises.
    """
    try:
        verify_result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"exists": False, "diff_text": None, "error": str(e)}

    if verify_result.returncode != 0:
        return {"exists": False, "diff_text": None, "error": None}

    try:
        diff_result = subprocess.run(
            ["git", "diff", f"master...{branch}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"exists": True, "diff_text": None, "error": str(e)}

    if diff_result.returncode != 0:
        return {"exists": True, "diff_text": None, "error": diff_result.stderr.strip()}

    return {"exists": True, "diff_text": diff_result.stdout, "error": None}


def _remote_diff_from_omen(branch: str) -> dict:
    """
    Aero-only: the Aero already has full, unrestricted SSH access to the
    Omen (same as nova_omen_sync.py/nova_worktree_status.list_omen_worktrees())
    -- no forced-command restriction on that side, so this runs
    rev-parse+diff directly in one chained remote command. Same
    {"exists", "diff_text", "error"} shape as _local_diff(). Never raises.
    """
    remote_cmd = (
        f"cd {OMEN_REPO_PATH} && git rev-parse --verify refs/heads/{branch} "
        f"&& echo ===DIFF=== && git diff master...{branch}"
    )
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_cmd],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"exists": False, "diff_text": None, "error": str(e)}

    if result.returncode != 0:
        # rev-parse failing (branch doesn't exist on the Omen either) looks
        # identical to a real transport error here -- both mean "nothing to
        # show," and get_diff_link() only needs to know exists=False either way.
        return {"exists": False, "diff_text": None, "error": None}

    _, _, diff_text = result.stdout.partition("===DIFF===\n")
    return {"exists": True, "diff_text": diff_text, "error": None}


def _remote_diff_from_aero(branch: str) -> dict:
    """
    Omen-only: forced SSH via the dedicated AERO_DIFF_KEY ->
    scripts/ssh_read_aero_diff.ps1, which runs git directly on the Aero
    (confirmed live this works, unlike nova_worktree_pr.py's relay
    workaround for a different capability). Same stdin-JSON-request/
    JSON-response shape as nova_worktree_pr._dispatch_create_pr_to_aero().
    Never raises.
    """
    payload = json.dumps({"branch": branch}).encode("utf-8")
    try:
        result = subprocess.run(
            ["ssh", "-i", AERO_DIFF_KEY, "-o", "ConnectTimeout=10", f"{AERO_USER}@{AERO_HOST}", "ignored"],
            input=payload,
            capture_output=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"exists": False, "diff_text": None, "error": str(e)}

    if result.returncode != 0:
        return {
            "exists": False,
            "diff_text": None,
            "error": f"ssh exited {result.returncode}: {result.stderr.decode(errors='replace').strip()}",
        }

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {
            "exists": False,
            "diff_text": None,
            "error": f"Malformed response from the Aero: {result.stdout.decode(errors='replace')[:300]}",
        }


def get_diff_link(branch: str) -> dict:
    """
    The single entry point backing GET /observability/diff. Priority order:
    (a) pushed to origin -> real GitHub compare link; (b) local ref on this
    machine -> real diff text; (c) ref exists on the OTHER machine -> diff
    text fetched from there; (d) neither -> an honest "unavailable" result.
    Never raises -- every branch below is a plain return, same fail-open
    contract as nova_observability_dashboard.uncertainty_vs_outcome().
    """
    invalid_reason = _validate_branch(branch)
    if invalid_reason:
        return {"status": "invalid", "branch": branch, "reason": invalid_reason}

    github_url = _origin_link(branch)
    if github_url:
        return {"status": "github", "branch": branch, "url": github_url}

    local = _local_diff(branch)
    if local["exists"]:
        if local["diff_text"] is not None:
            return {"status": "local_diff", "branch": branch, "diff_text": local["diff_text"]}
        return {
            "status": "unavailable",
            "branch": branch,
            "reason": local["error"] or "Branch exists locally but its diff could not be read.",
        }

    on_aero = sys.platform == "win32"
    remote = _remote_diff_from_omen(branch) if on_aero else _remote_diff_from_aero(branch)
    remote_machine = "omen" if on_aero else "aero"
    if remote["exists"] and remote["diff_text"] is not None:
        return {"status": "remote_diff", "branch": branch, "machine": remote_machine, "diff_text": remote["diff_text"]}

    return {
        "status": "unavailable",
        "branch": branch,
        "reason": "Diff no longer available — branch not found on this machine, the other "
        f"machine ({remote_machine}), or on GitHub (it may have been pruned everywhere).",
    }
