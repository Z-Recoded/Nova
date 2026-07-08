# Nova Skill: Coding

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked for a coding sub-agent task to Nova's codebase conventions, safety constraints, and workflow before it sees the task description.

## Conventions

- All Nova code lives under C:/Nova/. Never write outside this tree unless the task explicitly names an external path.
- Python files use snake_case; one module = one responsibility (e.g. nova_headroom.py only does resource/headroom checks, never orchestration logic).
- Every new function that touches nova_state.db must use the confidence/source_adapter/staleness conventions from the domain schema reference — never invent a parallel convention.
- Git commits are made per atomic change, not batched at the end of session. Commit message format: `[module] short imperative description` e.g. `[token_budget] add conservative mode downgrade logic`.
- Work happens in git worktrees, one per concurrent task, to avoid cross-task file contention. Never work directly on main without a worktree during autonomous sub-agent runs.
- Tests run before a task is marked complete. If no test exists for the touched module, write a minimal one rather than skipping verification.
- Config values (thresholds, ceilings, feature flags) belong in nova_config.json, never hardcoded in module files.

## Constraints

- Never touch files outside the workspace path scoped to the current task by nova_tools.py's permission layer.
- Never call external APIs that aren't already an approved integration (Anthropic, ClickUp, Drive, Tailscale, Ollama) without flagging the new dependency to Marvin first.
- Never delete or rewrite an entire file when a targeted patch/diff would do — see patch-style edits convention (ClickUp 86barhqpp).
- Never mark a ClickUp task complete if tests fail or the change is partial. Leave status accurate — a lie on the board is worse than an incomplete task.
- Never invoke a frontier model call inside a loop without a budget/headroom check gating it first.

## Output format

A completed coding task returns: (1) list of files changed, (2) one-line summary of what changed and why, (3) test result (pass/fail + which tests ran), (4) any new dependency or config key introduced, (5) ClickUp task ID updated with accurate status.

## Examples

Good: "Modified token_budget.py: added conservative-mode model downgrade logic (see lines 40-58). Ran test_token_budget.py — 6/6 pass. No new dependencies. Updated 86barhqt9 to 'in progress', added implementation note."

Bad: "Rewrote token_budget.py from scratch, added a bunch of stuff, should work, marked the ClickUp task as done." (No specifics on what changed, no test evidence, a full rewrite where a patch was possible, and a completion claim with no verification — this is exactly the failure mode the constraints above exist to prevent.)
