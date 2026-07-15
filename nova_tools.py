# nova_tools.py
# Path-scoped file and command primitives for Nova's coding agent.
#
# Every function takes an explicit `root` directory (a task's git worktree,
# never the live C:/Nova tree) and refuses to touch anything outside it.
# The Second Brain vault is hard-denied as well, as a defense-in-depth check,
# even though a worktree root should never resolve there in practice.

import os
import re
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
# against `root`. As of 86baxbrmj's interim hardening, run_command also
# rejects any `cd` outside `root` and restricts PATH/env to explicit
# allowlists (see _cd_targets_outside_root/_build_restricted_path/
# _build_restricted_env below) — but an allowed binary can still take an
# absolute-path argument (e.g. `git -C /c/Nova status`), so the live
# C:/Nova tree is still reachable through that vector. This denylist plus
# the cd/PATH/env restrictions are a best-effort interim stopgap, not real
# sandboxing. Real containment is deferred to the Phase 3.5 Docker/OpenHands
# hardening pass — see CLAUDE.md.
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


# Interim run_exec hardening (86baxbrmj) — closes the specific failure already
# observed (a run falling back to the shared live venv when it cd'd looking for
# a worktree-local Python) without waiting on the Phase 3.5 Docker/OpenHands
# hardening pass. Three pieces: reject cd targets outside root, restrict PATH
# to a small named allowlist instead of the full inherited PATH, and restrict
# the subprocess environment to non-secret Windows/process plumbing only.
# Still not real sandboxing — an allowed binary can still take an absolute-path
# argument (e.g. `git -C /c/Nova status`). That gap stays deferred to Docker.

_CD_PATTERN = re.compile(r'(?:^|[;&|\n]|&&|\|\|)\s*cd\s+([^\s;&|]+)', re.IGNORECASE)


def _resolve_cd_target(target: str, root_path: Path) -> Path | None:
    """
    Best-effort static resolution of a `cd` argument. Returns None when it
    can't be resolved without running a shell (variable/command substitution)
    — those cases are let through, same accepted-gap philosophy as
    _is_dangerous_command's substring denylist.
    """
    if "$" in target or "`" in target:
        return None
    posix_drive = re.match(r'^/([A-Za-z])(/.*)?$', target)  # Git-Bash /c/... -> C:/...
    if posix_drive:
        target = f"{posix_drive.group(1).upper()}:{posix_drive.group(2) or '/'}"
    if target == "~":
        return root_path
    if target.startswith("~/"):
        candidate = root_path / target[2:]
    else:
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = root_path / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _cd_targets_outside_root(cmd: str, root_path: Path) -> str | None:
    """
    Best-effort regex scan for `cd` targets resolving outside root_path. Not
    a shell parser — can be fooled by `cd $VAR`, command substitution, or a
    `cd` inside an unrelated quoted string. Accepted gap, same trade-off as
    the existing denylist.
    """
    for match in _CD_PATTERN.finditer(cmd):
        target = match.group(1).strip("'\"")
        if target.startswith("-"):
            continue
        resolved = _resolve_cd_target(target, root_path)
        if resolved is not None and not resolved.is_relative_to(root_path):
            return f"`cd {target}` resolves outside the worktree root ({resolved})"
    return None


# .../Git/usr/bin/bash.exe -> .../Git — derived from the already-hardcoded
# GIT_BASH_PATH rather than a fresh shutil.which() call, which would depend
# on the very system PATH this restriction is removing.
GIT_INSTALL_ROOT = Path(GIT_BASH_PATH).resolve().parent.parent.parent


def _build_restricted_path(root_path: Path) -> str:
    """
    Small, explicit allowlist PATH: the documented venv exception (so plain
    python/pip keep resolving to the live project's venv), Git Bash's own
    bin dirs (git, ls, grep, cat, ...), and the worktree root itself.
    Everything else from the previously-full inherited system PATH is
    dropped.
    """
    candidates = [
        NOVA_ENV_SCRIPTS_PATH,
        str(GIT_INSTALL_ROOT / "cmd"),
        str(GIT_INSTALL_ROOT / "bin"),
        str(GIT_INSTALL_ROOT / "usr" / "bin"),
        str(root_path),
    ]
    return os.pathsep.join(p for p in candidates if Path(p).is_dir())


# Non-secret Windows/process-plumbing variables a subprocess needs to
# function normally. Deliberately excludes ANTHROPIC_API_KEY, CLICKUP_API_KEY,
# RUNPOD_API_KEY, and everything else the parent Nova process has loaded from
# .env — nothing in this codebase reads any of those from inside a run_command
# child process.
ENV_ALLOWLIST = [
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
    "NUMBER_OF_PROCESSORS", "OS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
]


def _build_restricted_env(root_path: Path) -> dict:
    """Explicit non-secret environment, replacing a full os.environ.copy()."""
    env = {var: os.environ[var] for var in ENV_ALLOWLIST if var in os.environ}
    env["PATH"] = _build_restricted_path(root_path)
    # HOME pinned to the worktree so a bare `cd` with no argument — which the
    # regex scan above can't statically catch, since there's nothing to
    # match — still can't land outside root.
    env["HOME"] = str(root_path)
    return env


def run_command(cmd: str, root: str, timeout: int = NOVA_AGENT_CMD_TIMEOUT_SECONDS) -> dict:
    """
    Run a shell command via Git Bash, with cwd pinned to `root`. Returns
    stdout, stderr, and the return code. A timeout kills the process and
    reports it as such rather than hanging the agent loop indefinitely.
    Refuses to run commands matching an obvious-destructive-pattern
    denylist, or any `cd` that resolves outside `root` (see NOTE ON
    SANDBOXING above — this is best-effort, not a real sandbox boundary).
    PATH and the subprocess environment are both restricted to explicit
    allowlists rather than the full inherited system PATH/environment.
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

    cd_violation = _cd_targets_outside_root(cmd, root_path)
    if cd_violation:
        return {
            "stdout": "",
            "stderr": f"Refused: {cd_violation}. run_command is scoped to this worktree.",
            "returncode": None,
            "timed_out": False,
        }

    env = _build_restricted_env(root_path)
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
