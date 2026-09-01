# Completion-Gate Audit — Scope (Eval Harness Initiative 2, part 2)

Written 2026-08-31. The ACI-harness turn-loop guard cluster is done
(`docs/aci-guard-cluster-ablation.md`). This scopes the *other* gate group Initiative 2
names: the production coding-agent completion gate, `check_ground_truth_completion()` in
`nova_completion_gate.py`.

## The gate

`check_ground_truth_completion(diff, task, root, base_ref, requirements)` runs **13 checks**
on a finished diff and returns `{passed, hard_fails, warnings}`. It's called once at the end
of a coding task by `run_coding_task()` (and by `nova_coding_eval.py` for each dev-set task).
It **never blocks the commit** — Marvin reviews every diff by hand — its job is to stop a
false "completed" from going unnoticed.

| Kind | Checks |
|---|---|
| hard-fail (10) | `nonzero_diff`, `syntax_valid`, `powershell_syntax_valid`, `lint_clean`, `module_level_name_order`, `cross_module_circular_import`, `cross_module_missing_export`, `required_files_touched`, `forbidden_paths_untouched`, `narrow_scope_not_exceeded` |
| warning (3) | `deliverables_present`, `unused_new_import`, `unexpected_deletion` |

Every check is a standalone `_check_*` function taking `(diff, root[, requirements])` — pure,
deterministic, **no API cost**. The only paid step is `extract_task_requirements()` (one
Claude call per task), which is already SHA-cached (2026-08-14) and shared with the RunPod
file-allowlist guard.

## Why this half is cheap and unblocked

Unlike the A1-G2 turn-loop guards, ablating a completion-gate *check* does **not** need an
agentic run — the gate operates on a diff that already exists. And the historical corpus is
already on disk:

- **`logs/ground_truth_gate_log.jsonl` — 96 rows.** Each is a real
  `check_ground_truth_completion()` result: `passed`, `hard_fails` (tagged by check name),
  `warnings`, `task`, `branch`. The per-check firing record already exists — no re-run needed
  for checks that existed when these were logged.
- **`logs/coding_review_log.jsonl` — 109 rows.** Each has the real `diff`, `task`, an
  `approved` bool (Claude's review verdict), `issues`, and `chosen_diff` (a corrected
  version). This is the ground truth for "was this diff actually good?"

## Proposed method ($0, data-only, no held-out set required)

1. **Decisiveness per check** — from `ground_truth_gate_log.jsonl`, count how often each check
   was the *sole* hard-fail (removing it flips `passed` False → True). A check that is never
   the sole reason a diff fails carries little independent weight.
2. **Precision cost** — join the flipped verdicts to `coding_review_log.jsonl`'s `approved`.
   When a check hard-failed a diff, was that diff actually good (`approved: true` → the check
   hurt precision) or bad (`approved: false` → the check earned its place)?
3. **Backfill newer checks** — some checks (`cross_module_circular_import`, `86bb77vk6`;
   `cross_module_missing_export`) postdate parts of the log. Re-run
   `check_ground_truth_completion()` on the stored `coding_review_log.jsonl` diffs to get a
   complete matrix — still $0 beyond the cached requirements call.
4. **Report** — per check: fire count, sole-fail count, false-positive rate (good diffs it
   blocked), false-negative contribution (bad diffs only it caught). Same "some carry the
   lift, some hurt precision" framing as the guard-cluster audit.

Optional permanent infra (only if step 1–4 justify a change): a `disabled_checks: frozenset`
param on `check_ground_truth_completion()`, mirroring `run_exercise(disabled_guards=...)`.

## What still needs the held-out set (Initiative 1 follow-up)

Whether a check *generalizes* — i.e. does it keep its precision on tasks it wasn't built
from. That needs `logs/held_out_pool.jsonl` populated with hand-authored tasks (currently
empty). The dev-set / historical-corpus analysis above stands on its own as the first pass.

## The A1-G2 turn-loop guards (separate, deferred)

The registry guards in `nova_orchestrator_runpod.py` / `_devstral.py` (repeat-call,
context-overflow, dead-code, self-verify nudge, goal re-anchor, file-allowlist, …) DO need
agentic runs to ablate, against the RunPod/Devstral backends, which cost real GPU money. Lower
priority given the 2026-08-12 pivot away from the dense-model lane — revisit only if that lane
comes back.
