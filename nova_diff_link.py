# nova_diff_link.py
# Trace-to-diff linking for Observability Initiative Phase 4 (86bb7pb6t) --
# given a branch name (as already carried by each point in the
# /observability/uncertainty payload), resolves what's actually viewable:
# a real GitHub compare link if the branch was pushed, a local diff if its
# ref still exists on this machine, or an honest "not available" result.
#
# Same-machine-only v1 (scope decision, confirmed with Marvin): most
# historical coding-agent branches are local-only and get pruned once
# reviewed (nova_orchestrator.py never runs `git push` on its own worktrees
# -- confirmed by grep), so "diff no longer available" is an expected,
# first-class result here, not an error case. A branch that's local-only on
# the OTHER machine (Aero vs. Omen) also reports "unavailable" in this v1 --
# reaching across machines would need a new SSH bridge key + forced script
# (nova_worktree_pr.py's relay pattern), deliberately deferred because a
# live test of that leg was inconclusive (the Aero wasn't reachable over
# Tailscale when checked) and the real cost of new SSH infrastructure isn't
# worth taking on for an unverified path.
#
# Reuses nova_worktree_pr.DISPATCH_BRANCH_PATTERN rather than redefining an
# already-correct regex for that lane.

import os
import re
import subprocess

from nova_worktree_pr import DISPATCH_BRANCH_PATTERN

# This file lives at the repo root, so its own directory IS the local repo root.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

GITHUB_REPO_URL = "https://github.com/Z-Recoded/Nova"

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


def get_diff_link(branch: str) -> dict:
    """
    The single entry point backing GET /observability/diff. Priority order:
    (a) pushed to origin -> real GitHub compare link; (b) local ref on this
    machine -> real diff text; (c) neither -> an honest "unavailable" result
    (covers both "genuinely pruned everywhere" and "local-only on the other
    machine," which this same-machine-only v1 can't distinguish). Never
    raises -- every branch below is a plain return, same fail-open contract
    as nova_observability_dashboard.uncertainty_vs_outcome().
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

    return {
        "status": "unavailable",
        "branch": branch,
        "reason": "Diff no longer available — branch not found on this machine or on GitHub "
        "(it may have been pruned locally, or exist only on the other machine).",
    }
