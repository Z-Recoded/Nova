# Coding Specialist ACI: Sandbox/Container Decision

Scoping the one concrete blocker on `86bbch95y` (constrained action-space
interface design) that's genuinely still open — base image and sandbox
tooling for running the ACI's commands in isolation. No build in this pass;
this proposes a decision for review.

## What the container actually needs to support

Walking through the ACI ticket's own 4 core commands:

| Command | What it needs |
|---|---|
| `find_file` / `search_file` / `search_dir` | File-tree traversal and text search — Python stdlib (`os.walk`, `pathlib`, `re`) is enough. No extra tooling. |
| Windowed file view | Read a file, slice to a line range. Stdlib only. |
| Edit + structural gate | Write the file, then run a Python lint/syntax check before accepting the edit. **Needs `ruff`.** |
| History collapsing | Pure prompt/context bookkeeping — doesn't touch the container at all. |

So the real requirement is: isolated file read/write + `ruff`. Nothing else.

## Why the existing `nova-dispatch-sandbox` image isn't a fit

Checked `docker/nova-dispatch-sandbox/Dockerfile` (the only Docker precedent
in this repo) before proposing anything new. It's `node:20-bookworm-slim` +
git + python3/pip + the `claude` CLI — built for a completely different job:
running `claude -p` itself, unattended, with full worktree/git access, for
headless task dispatch. Two real mismatches for ACI's use:

- **The model doesn't run inside the ACI container.** In SWE-agent's own
  architecture (which this ACI is modeled on), the LM is a separate API call
  — the container is just the "computer" its structured commands operate
  against. `nova-dispatch-sandbox` bundles the `claude` CLI *because* the
  model runs inside it; ACI's container never needs that.
- **No git needed inside.** The existing sandbox needs git because
  `dispatch_headless_task_sandboxed()` does real worktree/commit operations
  inside the container. ACI never commits anything — it only edits files
  inside a working copy prepared before the container starts.

Reusing this image would carry real dead weight (Node.js/npm/claude-cli, ~none
of which this task needs) and the wrong mount philosophy (git-aware worktree
vs. a plain bind-mounted directory).

## Proposed decision

**Base image:** `python:3.13-slim` — matches this repo's own pinned Python
version exactly (`nova-env`'s interpreter is 3.13.14; `pyproject.toml`'s
`[tool.ruff]` targets `py313`). Not a guess — checked both directly before
proposing this.

**Sandbox tooling:** `ruff==0.15.22`, the exact version already pinned in
`requirements.txt` — same tool, same version, as
`nova_completion_gate.py`'s existing `_check_lint_clean()`/`_ruff_violations()`
already use (`sys.executable -m ruff check --output-format=json`). **This is
less net-new work than the ticket implies** — the structural gate ACI wants
("Python lint/syntax check runs before an edit is accepted") is functionally
the same check Nova already has proven code for; the ACI work is triggering
it per-edit instead of once at the end of a whole task, not writing it from
scratch.

**No git inside the container.** Repo/exercise content should be prepared on
the *host* (`git worktree add`, same as today) and bind-mounted into the
container read-write — mirroring `nova_omen_dispatch.py`'s existing
sandboxed-dispatch mount design rather than inventing a second pattern for
git access inside a container.

**Nothing else installed.** No Node.js, no `claude` CLI, no ML/torch stack —
the model is served separately (locally via Ollama once Qwen2.5-Coder-7B is
pulled, or a rented endpoint); this container's only job is running the
file operations the model's structured commands request.

## Sketch (not yet built)

```dockerfile
FROM python:3.13-slim

RUN pip install --no-cache-dir ruff==0.15.22

# No ENTRYPOINT/CMD — matches nova-dispatch-sandbox/Dockerfile's own
# convention of never assuming a fixed invocation; the real command is
# passed at `docker run` time by whatever harness drives the ACI.
```

## Explicitly still open (not decided here)

- Exact mount scope (whether the container gets read-write on the whole
  worktree or a narrower subtree) and per-task teardown lifecycle.
- Resource limits (memory/CPU caps) — not addressed, since none of Nova's
  existing sandbox precedent sets these either.
- Whether this lives at `docker/nova-aci-sandbox/Dockerfile` (its own
  directory, matching the existing `nova-dispatch-sandbox` pattern) — assumed
  but not created in this pass.
- This does **not** unblock `86bbch95y`'s *other* named blocker (a task set
  suited to tuning ACI's specific parameters like window size) — that's a
  separate, still-open question flagged in the earlier conversation.
