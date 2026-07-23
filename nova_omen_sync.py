# nova_omen_sync.py
# One-command sync for the Omen's MAIN checkout (not the disposable
# `claude --worktree` dispatch path, which already self-syncs — see
# nova_omen_dispatch.py's docstring).
#
# Collapses the sequence that caused the 15-commit stale-clone incident
# (CLAUDE.md Section 2, "HP Omen Headless Server") into one command:
#   git pull  -->  restart nova-api + nova-chroma  -->  confirm both are
#   listening again
# Each step was previously a separate manual action, easy to do out of
# order or skip — this script always does all three, in order, every run.
#
# ONE-TIME SETUP THIS SCRIPT DEPENDS ON: restarting the systemd services
# needs sudo, and sudo over a non-interactive SSH session can't prompt for
# a password — it just fails (or hangs, without -n). This script passes
# `sudo -n` so a missing sudoers rule fails loudly and immediately instead
# of hanging. Add this line on the Omen with `sudo visudo` before this
# script's restart step will work (scoped to exactly these two restarts,
# nothing broader):
#
#   marvinroyal5 ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart nova-api, /usr/bin/systemctl restart nova-chroma
#
# Confirm the real path to systemctl on the Omen first (`which systemctl`)
# — sudoers NOPASSWD rules match on exact command path, a wrong path
# silently fails to match and sudo falls back to asking for a password.
#
# Deliberately manual-trigger only (not wired to a git post-push hook) —
# Marvin's explicit choice: a human decides when new code goes live on the
# Omen, matching the review-before-merge posture the rest of this project
# already uses for nova_orchestrator.py worktrees.

import argparse
import socket
import subprocess
import sys
import time

OMEN_HOST = "100.114.197.117"  # Tailscale IP — same choice as nova_omen_dispatch.py, works whether or not the Aero is on the same LAN  # noqa: E501
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

SSH_TIMEOUT_SECONDS = 30
RESTART_TIMEOUT_SECONDS = 30
POST_RESTART_SETTLE_SECONDS = 3  # brief pause so the probe doesn't catch the just-stopped old process's socket
STARTUP_TIMEOUT_SECONDS = 45  # nova-api's import chain loads the embedding model (~6-17s), so poll until up rather than probing once  # noqa: E501
TCP_PROBE_TIMEOUT_SECONDS = 5

NOVA_API_PORT = 8001  # CLAUDE.md Section 2 — 8001 on the Omen specifically, not 8000 (port conflict with Chroma)
NOVA_CHROMA_PORT = 8000


def _run_ssh(remote_command: str, timeout: int) -> subprocess.CompletedProcess:
    """Run one command on the Omen over SSH and return the completed process. Never raises on a non-zero exit — callers check returncode themselves."""  # noqa: E501
    return subprocess.run(
        ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tcp_reachable(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT_SECONDS) -> bool:
    """Raw socket check — same technique as nova_chroma_omen_check.py's reachability probe."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pull_latest() -> dict:
    """
    Run `git pull origin master` on the Omen's main checkout. Reports the
    commit SHA before and after so the caller can see exactly what moved —
    "Already up to date" is a legitimate, expected outcome, not a failure.
    """
    before_result = _run_ssh(f"cd {OMEN_REPO_PATH} && git rev-parse HEAD", SSH_TIMEOUT_SECONDS)
    before_sha = before_result.stdout.strip()

    pull_result = _run_ssh(f"cd {OMEN_REPO_PATH} && git pull origin master", SSH_TIMEOUT_SECONDS)
    if pull_result.returncode != 0:
        return {
            "success": False,
            "error": f"git pull exited {pull_result.returncode}: {pull_result.stderr.strip()}",
            "before_sha": before_sha,
        }

    after_result = _run_ssh(f"cd {OMEN_REPO_PATH} && git rev-parse HEAD", SSH_TIMEOUT_SECONDS)
    after_sha = after_result.stdout.strip()

    return {
        "success": True,
        "before_sha": before_sha,
        "after_sha": after_sha,
        "changed": before_sha != after_sha,
        "pull_output": pull_result.stdout.strip(),
    }


def restart_services() -> dict:
    """
    Restart nova-api and nova-chroma via `sudo -n systemctl restart`, one
    unit per sudo call. The sudoers NOPASSWD grant in this script's module
    docstring authorizes two separate exact commands (`restart nova-api` and
    `restart nova-chroma` individually) — a single combined invocation like
    `systemctl restart nova-api nova-chroma` does NOT match either of those
    exact-match rules, so this issues two separate sudo calls instead of one.
    The `-n` flag makes a missing/non-matching sudoers rule fail immediately
    with a clear "a password is required" error instead of hanging the SSH
    session waiting for a prompt that can never arrive non-interactively.
    """
    results = {}
    for unit in ("nova-api", "nova-chroma"):
        result = _run_ssh(f"sudo -n /usr/bin/systemctl restart {unit}", RESTART_TIMEOUT_SECONDS)
        results[unit] = {
            "success": result.returncode == 0,
            "error": result.stderr.strip() if result.returncode != 0 else None,
        }

    all_succeeded = all(r["success"] for r in results.values())
    if not all_succeeded:
        failed_units = [unit for unit, r in results.items() if not r["success"]]
        return {
            "success": False,
            "error": f"restart failed for: {', '.join(failed_units)}",
            "per_unit": results,
            "likely_cause": (
                "Missing or non-matching NOPASSWD sudoers rule — see this script's "
                "module docstring for the exact `visudo` line, and confirm with "
                "`sudo -l` on the Omen that it actually shows up."
            ),
        }
    return {"success": True, "per_unit": results}


def _wait_until_listening(host: str, port: int, timeout: int) -> bool:
    """Poll a port until it accepts a connection or the timeout elapses. Returns True as soon as it's up, so a fast restart isn't penalized and a slow one isn't falsely failed."""  # noqa: E501
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _tcp_reachable(host, port):
            return True
        time.sleep(1)
    return False


def verify_services_listening() -> dict:
    """
    Confirm both services are accepting TCP connections again after the
    restart. Polls up to STARTUP_TIMEOUT_SECONDS rather than probing once,
    because nova-api's startup loads the embedding model (~6-17s) and a
    single fixed-delay probe races it into a false "down" report. This
    confirms "restarted and listening," not "returns correct RAG answers" —
    that deeper functional check is nova_chroma_omen_check.py's job, not this
    script's (see CLAUDE.md's "reachable != functionally correct" lesson).
    """
    # Brief settle so we don't catch the just-stopped old process's socket.
    time.sleep(POST_RESTART_SETTLE_SECONDS)
    return {
        "nova_api_listening": _wait_until_listening(OMEN_HOST, NOVA_API_PORT, STARTUP_TIMEOUT_SECONDS),
        "nova_chroma_listening": _wait_until_listening(OMEN_HOST, NOVA_CHROMA_PORT, STARTUP_TIMEOUT_SECONDS),
    }


def sync_omen(skip_restart: bool = False, force_restart: bool = False) -> dict:
    """
    Full sync sequence: pull, then (unless skipped) restart both services
    and confirm they're back up. Stops early and reports exactly which step
    failed rather than plowing ahead — a failed restart with no verification
    is worse than knowing it failed. force_restart bypasses the "nothing
    changed" skip — used to verify the restart+verify path itself works,
    independent of whether there's a real commit to pull.
    """
    pull_result = pull_latest()
    if not pull_result["success"]:
        return {"step": "pull", **pull_result}

    if skip_restart:
        return {"step": "pull", **pull_result, "restart_skipped": True}

    if not pull_result["changed"] and not force_restart:
        return {"step": "pull", **pull_result, "restart_skipped": True, "restart_skip_reason": "no new commits"}

    restart_result = restart_services()
    if not restart_result["success"]:
        return {"step": "restart", "pull": pull_result, **restart_result}

    verify_result = verify_services_listening()
    return {
        "step": "verify",
        "success": verify_result["nova_api_listening"] and verify_result["nova_chroma_listening"],
        "pull": pull_result,
        "restart": restart_result,
        "verify": verify_result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-command sync for the Omen's main checkout.")
    parser.add_argument(
        "--skip-restart", action="store_true", help="Pull only, don't restart services (e.g. for a doc-only commit)"
    )  # noqa: E501
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help="Restart services even if the pull found nothing new (for verifying the restart path itself)",
    )  # noqa: E501
    args = parser.parse_args()

    outcome = sync_omen(skip_restart=args.skip_restart, force_restart=args.force_restart)
    print(
        f"Pull: {outcome.get('pull', outcome).get('before_sha', '?')[:8]} -> {outcome.get('pull', outcome).get('after_sha', '?')[:8]}"  # noqa: E501
    )  # noqa: E501

    if not outcome.get("pull", outcome).get("success", outcome.get("success")):
        print(f"FAILED at pull: {outcome.get('error')}")
        sys.exit(1)

    if outcome.get("restart_skipped"):
        reason = outcome.get("restart_skip_reason", "--skip-restart passed")
        print(f"Restart skipped ({reason}).")
        sys.exit(0)

    restart = outcome.get("restart", {})
    if not restart.get("success"):
        print(f"FAILED at restart: {restart.get('error')}")
        if restart.get("likely_cause"):
            print(f"Likely cause: {restart['likely_cause']}")
        sys.exit(1)

    verify = outcome.get("verify", {})
    print(f"nova-api listening ({OMEN_HOST}:{NOVA_API_PORT}): {verify.get('nova_api_listening')}")
    print(f"nova-chroma listening ({OMEN_HOST}:{NOVA_CHROMA_PORT}): {verify.get('nova_chroma_listening')}")

    if not outcome["success"]:
        print("FAILED at verify: one or both services aren't listening after restart.")
        sys.exit(1)

    print("Sync complete - Omen is current and both services are back up.")
