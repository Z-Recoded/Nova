# nova_tools.py
# Path-scoped file and command primitives for Nova's coding agent.
#
# Every function takes an explicit `root` directory (a task's git worktree,
# never the live C:/Nova tree) and refuses to touch anything outside it.
# The Second Brain vault is hard-denied as well, as a defense-in-depth check,
# even though a worktree root should never resolve there in practice.

import os
import subprocess
from pathlib import Path

from nova_sources import SOURCES

# Command execution timeout, in seconds. Named so it's never a bare magic
# number at the call site.
NOVA_AGENT_CMD_TIMEOUT_SECONDS = 60


def _find_second_brain_path() -> Path:
    """Look up the Second Brain vault path from nova_sources.SOURCES."""
    for src in SOURCES:
        if src["project"] == "Second Brain":
            return Path(src["path"]).resolve()
    raise RuntimeError("Second Brain source not found in nova_sources.SOURCES")


SECOND_BRAIN_PATH = _find_second_brain_path()


def _resolve_within_root(path: str, root: str) -> Path:
    """
    Resolve `path` (relative or absolute) against `root` and verify the
    result stays inside `root` and outside the Second Brain vault.
    Raises ValueError if either check fails.
    """
    root_path = Path(root).resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root_path / candidate).resolve()

    if not resolved.is_relative_to(root_path):
        raise ValueError(f"Path '{path}' resolves outside the task root '{root_path}'.")
    if resolved.is_relative_to(SECOND_BRAIN_PATH):
        raise ValueError(f"Path '{path}' resolves inside the Second Brain vault — never allowed.")

    return resolved


def read_file(path: str, root: str) -> str:
    """Read a text file's contents, scoped to `root`."""
    resolved = _resolve_within_root(path, root)
    return resolved.read_text(encoding="utf-8")


def write_file(path: str, content: str, root: str) -> None:
    """Write (create or overwrite) a text file, scoped to `root`."""
    resolved = _resolve_within_root(path, root)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")


def file_replace(path: str, old_str: str, new_str: str, root: str) -> None:
    """
    Replace the single unique occurrence of old_str with new_str in an
    existing file, scoped to root. Raises ValueError if old_str appears
    zero or more than once, so the caller can react (pick a more specific
    old_str, or fall back to write_file) instead of applying an ambiguous
    or no-op edit.
    """
    resolved = _resolve_within_root(path, root)
    content = resolved.read_text(encoding="utf-8")
    occurrences = content.count(old_str)
    if occurrences != 1:
        raise ValueError(
            f"old_str appears {occurrences} times in '{path}' — must appear exactly once."
        )
    resolved.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")


def list_files(path: str, root: str) -> list[str]:
    """List file paths (relative to `root`) under `path`, scoped to `root`."""
    resolved = _resolve_within_root(path, root)
    root_path = Path(root).resolve()
    if resolved.is_file():
        return [str(resolved.relative_to(root_path))]
    return sorted(
        str(p.relative_to(root_path))
        for p in resolved.rglob("*")
        if p.is_file()
    )


# Explicit Git Bash path, not Python's shell=True default. On Windows that
# default is cmd.exe, which doesn't understand the Unix-style commands (ls,
# grep, which, heredocs) a model will naturally reach for — and Git's own
# tools are partially on PATH anyway, so failures look inconsistent rather
# than clearly wrong. Using bash explicitly matches the rest of this
# environment (see the project's own Bash tool) and removes the ambiguity.
GIT_BASH_PATH = r"C:\Program Files\Git\usr\bin\bash.exe"

# Worktrees don't have their own virtualenv (nova-env/ isn't git-tracked), so
# there's no local Python interpreter to find. Prepending the live venv's
# Scripts dir to PATH lets plain `python`/`pip` resolve without the agent
# needing to `cd` out of its worktree to find them.
NOVA_ENV_SCRIPTS_PATH = r"C:\Nova\nova-env\Scripts"

# NOTE ON SANDBOXING: run_command's isolation is NOT equivalent to
# read_file/write_file/list_files above. Those hard-validate every path
# against `root`. This does not — a command can `cd` anywhere the OS user
# can reach, including the live C:/Nova tree, bypassing the worktree
# boundary entirely. This denylist is a best-effort speed bump against
# obviously destructive commands, not real sandboxing. Real containment is
# deferred to the Phase 3.5 Docker/OpenHands hardening pass — see CLAUDE.md.
DANGEROUS_COMMAND_PATTERNS = [
    "rm -rf",
    "git push",
    "git reset --hard",
    "git clean -f",
    "format ",
    "del /f",
    "remove-item -recurse -force",
    "shutdown",
    "mkfs",
]


def _is_dangerous_command(cmd: str) -> bool:
    """Best-effort check for obviously destructive command patterns."""
    lowered = cmd.lower()
    return any(pattern in lowered for pattern in DANGEROUS_COMMAND_PATTERNS)


def run_command(cmd: str, root: str, timeout: int = NOVA_AGENT_CMD_TIMEOUT_SECONDS) -> dict:
    """
    Run a shell command via Git Bash, with cwd pinned to `root`. Returns
    stdout, stderr, and the return code. A timeout kills the process and
    reports it as such rather than hanging the agent loop indefinitely.
    Refuses to run commands matching an obvious-destructive-pattern
    denylist (see NOTE ON SANDBOXING above — this is best-effort, not a
    real sandbox boundary).
    """
    if _is_dangerous_command(cmd):
        return {
            "stdout": "",
            "stderr": (
                "Refused: command matches a denylisted destructive pattern. "
                "If this command is actually needed, ask a human to run it directly."
            ),
            "returncode": None,
            "timed_out": False,
        }

    root_path = Path(root).resolve()
    env = os.environ.copy()
    env["PATH"] = NOVA_ENV_SCRIPTS_PATH + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(
            [GIT_BASH_PATH, "-c", cmd],
            cwd=root_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "returncode": None,
            "timed_out": True,
        }
