# nova_worktree_pr.py
# Diff-preview-and-merge for dispatched tasks (86bb3ceyf) -- scoped in a
# dedicated conversation before building (recorded as a ClickUp comment on
# the task): Omen-hosted headless-dispatch worktrees only, no custom diff
# viewer -- this pushes a real GitHub PR and the Controller just deep-links
# to it, so Marvin reviews/merges using GitHub's own already-good,
# already-mobile-friendly UI. A separate discard action deletes the
# worktree+branch outright when the dispatched work isn't worth keeping.
#
# The real constraint that shapes this whole module: the Omen genuinely
# cannot push to GitHub (its deploy key is read-only, confirmed live) and
# has no `gh` CLI installed at all -- so the actual git fetch/push/gh-pr-
# create work has to happen on the Aero. But ALSO: today's abort/kill-
# switch build (86bb3ceyj) found that SSH public-key sessions on Windows
# use a "network logon" token that Windows refuses to use for
# CreateProcess AT ALL -- confirmed for python.exe, and the failure was a
# generic Win32 "Access is denied" at the CreateProcess level, not
# anything specific to that one binary, so it generalizes to git.exe and
# gh.exe too. That means the Omen->Aero forced SSH script for this
# feature CANNOT itself run git/gh -- it can only relay the request (via
# Invoke-RestMethod, a native .NET HTTP call, not a spawned process) to
# the Aero's OWN already-running nova_api.py instance, which -- being a
# normal locally-launched process, not an SSH session -- has no such
# restriction and can run git/gh directly.
#
# Real, honest consequence: the Aero->Omen leg of this feature only works
# when nova_api.py is actually running on the Aero (not just "the machine
# is on"). Given Task Scheduler's "Nova Auto-Start" already launches it at
# login (Phase 4), this holds most of the time Marvin's logged in, but
# it's a real, additional precondition beyond the existing aero_only/
# omen_only reachability pattern used elsewhere -- surfaced honestly in
# every error path below, not hidden.

import json
import os
import re
import subprocess
import sys

OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

AERO_HOST = "100.122.229.23"  # Tailscale IP
AERO_USER = "marvi"
# This file lives at the repo root, so its own directory IS the local repo root.
AERO_REPO_PATH = os.path.dirname(os.path.abspath(__file__))

# Fifth Omen->Aero command-restricted key (2026-07-26 follow-up to the
# 2026-07-25 bridge) -- relay-only, never runs git/gh itself (see module
# docstring for why it can't). Private key half lives only on the Omen.
AERO_WORKTREE_PR_KEY = os.path.expanduser("~/.ssh/aero_keys/id_ed25519_aero_worktree_pr")

SSH_TIMEOUT_SECONDS = 45

# Matches dispatch_headless_task()'s own naming convention exactly
# (nova_omen_dispatch.py: f"nova-dispatch-{uuid.uuid4().hex[:8]}") -- never
# accept an arbitrary branch name, on either the fetch/push leg or the
# discard leg. This is the one real security boundary standing between "a
# Controller button" and "push/delete arbitrary refs."
DISPATCH_BRANCH_PATTERN = re.compile(r"^nova-dispatch-[0-9a-f]{8}$")


def _validate_branch(branch: str) -> str | None:
    """Returns an error string if `branch` doesn't match the expected dispatch-branch shape, else None."""
    if not DISPATCH_BRANCH_PATTERN.match(branch or ""):
        return f"'{branch}' doesn't look like a real dispatch branch (expected nova-dispatch-<8 hex chars>)"
    return None


def _create_pr_locally(branch: str) -> dict:
    """
    The real work: fetch the branch's git objects directly out of the
    Omen's local object store (no GitHub round-trip needed for this step
    -- same command this project's own CLAUDE.md already documents for
    the manual "Working Directly on the Omen via SSH" workflow), push to
    origin, and open a draft PR. Only ever called on the Aero -- see
    create_worktree_pr() for the platform check. Never raises; returns
    {"success": False, "error": ...} on any step's failure.
    """
    error = _validate_branch(branch)
    if error:
        return {"success": False, "error": error}

    try:
        fetch_result = subprocess.run(
            ["git", "fetch", f"ssh://{OMEN_USER}@{OMEN_HOST}{OMEN_REPO_PATH}", f"{branch}:{branch}"],
            cwd=AERO_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "git fetch from the Omen timed out"}
    if fetch_result.returncode != 0:
        return {"success": False, "error": f"git fetch failed: {fetch_result.stderr.strip()}"}

    try:
        push_result = subprocess.run(
            ["git", "push", "origin", f"{branch}:{branch}"],
            cwd=AERO_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "git push to origin timed out"}
    if push_result.returncode != 0:
        return {"success": False, "error": f"git push failed: {push_result.stderr.strip()}"}

    try:
        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--head",
                branch,
                "--title",
                branch,
                "--body",
                "Automated headless-dispatch review request — created via Nova Controller (86bb3ceyf). "
                "Review the diff here, then merge or close from this PR.",
            ],
            cwd=AERO_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "gh pr create timed out"}
    if pr_result.returncode != 0:
        return {"success": False, "error": f"gh pr create failed: {pr_result.stderr.strip()}"}

    # `gh pr create`'s own stdout is just the PR URL on success (its last
    # non-empty line, matching _run_claude_over_ssh()'s own "scan, don't
    # assume pure output" discipline elsewhere in this project).
    lines = [line.strip() for line in pr_result.stdout.splitlines() if line.strip()]
    pr_url = lines[-1] if lines else None
    return {"success": True, "pr_url": pr_url, "branch": branch}


def _dispatch_create_pr_to_aero(branch: str) -> dict:
    """
    Relay a create-PR request to the Aero over the command-restricted SSH
    key -- only ever called when running on the Omen. The forced script
    on the Aero (scripts/ssh_relay_worktree_pr.ps1) can't run git/gh
    itself (see module docstring); it relays this over a local HTTP call
    to the Aero's own already-running nova_api.py instance, which does
    the real work and returns its result, which the forced script passes
    back over SSH stdout unchanged.

    Never raises. Returns {"success": False, "error": ...} for any
    transport failure (SSH unreachable/timeout, malformed response) --
    distinct from a real business-logic failure the Aero's own
    _create_pr_locally() reports, which passes through unchanged.
    """
    payload = json.dumps({"branch": branch}).encode("utf-8")
    try:
        result = subprocess.run(
            ["ssh", "-i", AERO_WORKTREE_PR_KEY, "-o", "ConnectTimeout=10", f"{AERO_USER}@{AERO_HOST}", "ignored"],
            input=payload,
            capture_output=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SSH to the Aero timed out"}

    if result.returncode != 0:
        return {
            "success": False,
            "error": f"ssh exited {result.returncode}: {result.stderr.decode(errors='replace').strip()}",
        }  # noqa: E501

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": f"Malformed response from the Aero: {result.stdout.decode(errors='replace')[:300]}",
        }


def create_worktree_pr(branch: str) -> dict:
    """
    Push `branch` to origin and open a draft PR for review, backing
    POST /worktree-pr. Platform-aware: on the Aero, does the real work
    directly; on the Omen, relays to the Aero over the SSH bridge (the
    Omen itself can never do this -- see module docstring). Never raises.
    """
    if sys.platform == "win32":
        return _create_pr_locally(branch)
    return _dispatch_create_pr_to_aero(branch)


def _discard_worktree_locally(branch: str, worktree_path: str) -> dict:
    """
    Delete a dispatch worktree and its branch outright -- no push/PR
    involved, so this is a pure local operation on whichever machine
    actually hosts the worktree (always the Omen, per this task's settled
    scope). Never raises.
    """
    error = _validate_branch(branch)
    if error:
        return {"success": False, "error": error}

    try:
        remove_result = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=OMEN_REPO_PATH if sys.platform != "win32" else AERO_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "git worktree remove timed out"}
    if remove_result.returncode != 0:
        return {"success": False, "error": f"git worktree remove failed: {remove_result.stderr.strip()}"}

    branch_result = subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=OMEN_REPO_PATH if sys.platform != "win32" else AERO_REPO_PATH,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if branch_result.returncode != 0:
        return {
            "success": True,
            "warning": f"Worktree removed, but branch deletion failed: {branch_result.stderr.strip()}",
        }

    return {"success": True, "branch": branch}


def discard_worktree(branch: str, worktree_path: str) -> dict:
    """
    Delete a dispatch worktree+branch, backing POST /worktree-discard.
    The worktree always lives on the Omen (this task's settled scope) --
    when this runs ON the Omen, it's a direct local delete; when it runs
    on the Aero (dev-only case), it reaches the Omen over the Aero's
    pre-existing, fully-privileged SSH access (same as nova_omen_sync.py)
    -- no new key needed for this direction, unlike create_worktree_pr().
    Never raises.
    """
    if sys.platform != "win32":
        return _discard_worktree_locally(branch, worktree_path)

    error = _validate_branch(branch)
    if error:
        return {"success": False, "error": error}

    remote_cmd = f"cd {OMEN_REPO_PATH} && git worktree remove --force {worktree_path} && git branch -D {branch}"
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_cmd],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SSH to the Omen timed out"}
    if result.returncode != 0:
        return {"success": False, "error": f"Discard on the Omen failed: {result.stderr.strip()}"}
    return {"success": True, "branch": branch}
