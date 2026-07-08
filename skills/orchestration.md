# Nova Skill: Orchestration

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked inside nova_orchestrator.py on DAG rules, the concurrency contract, failure-mode escalation, and ClickUp/MCP update conventions.

## Conventions

- LangGraph resolves the DAG (dependency order, state passing, parallel/sequential routing, retry). Nova owns everything outside that: Docker container spawning, the headroom gate before any spawn, memory read/write coordination, ClickUp updates via MCP, and failure escalation. Never let orchestration logic reach into Nova's identity/memory/state systems directly — always go through the owning module.
- Principle 1 (parallelism contract): concurrent sub-agents must not write to the same file or the same nova_state.db row simultaneously. Check the headroom calculator's task_slots (heavy/light) before spawning, and never exceed available slots.
- Every spawned task must pass through the token budget gate (see token_budget.py / system_state) before starting — this is the same gate as the resource headroom check, not a separate call.
- ClickUp task status updates happen at defined transition points only: when a task starts (to do → in progress), when it completes successfully (→ complete with accurate notes), and when it fails/halts (status reflects real state, never marked complete prematurely).

## Constraints

- Never spawn a new sub-agent task if headroom (VRAM/CPU/token budget) is in critical or halt mode — queue it instead.
- Never let a failed task retry silently more than the configured retry limit — escalate to Marvin via notification after exhausting retries, don't loop indefinitely.
- Never allow two concurrent tasks to hold conflicting locks on the same resource — if a conflict is detected, one task must wait, not both proceeding and corrupting state.
- Never update ClickUp with a status that doesn't match the actual on-disk/on-repo state of the work.

## Output format

An orchestration decision/log entry records: task ID, DAG position (dependencies satisfied/pending), headroom check result at spawn time, assigned execution mode (sequential/parallel), and final status with reason if not successful.

## Examples

Good: "Task 86barhqpp: dependencies satisfied (86barhqkw complete). Headroom check: normal mode, 1 heavy slot available. Spawned sequentially. Result: complete, 3 files changed, tests pass."

Bad: "Kicked off a few tasks at once since they seemed related, one of them failed a couple times so I just kept retrying it until it worked." (No headroom check recorded, no defined retry limit respected, no escalation on repeated failure, and a conflict-blind "kicked off a few at once" — exactly the ungated concurrency this skill file exists to prevent.)
