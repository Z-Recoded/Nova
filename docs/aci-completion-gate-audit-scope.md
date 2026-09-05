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

## Results — dev-set / historical-corpus pass, 2026-08-31 ($0, data-only)

`scratchpad/gate_audit.py` over `ground_truth_gate_log.jsonl` (96 rows) + `coding_review_log.jsonl`
(109 rows).

**Hard limitation up front:** the 96 gate rows are only **10 distinct tasks**; the 109 review
rows are **6 distinct tasks**. Same low-diversity problem the 2026-08-06 `nova_coding_corrector`
run hit. Every number below is *directional* — "how did this check behave across ~10 tasks run
many times", not a population estimate.

### Hard-fail checks — fired / was-the-sole-reason

| check | fired | sole (removing it flips a real F→T) | note |
|---|---|---|---|
| `lint_clean` | 26 | 18 | dominant — carries most of the gate's "not done" verdicts |
| `nonzero_diff` | 15 | **15** | perfectly decisive — an empty diff is unambiguously not-done |
| `module_level_name_order` | 12 | 10 | mostly on genuinely-bad attempts (8/12 on all-rejected tasks) |
| `required_files_touched` | 8 | 5 | catches incomplete attempts |
| `narrow_scope_not_exceeded` | 3 | 3 | small n, directionally fine |
| `cross_module_missing_export` | 2 | **2** | small n, perfectly decisive — imports a non-existent name → real ImportError |
| `cross_module_circular_import` | 3 | **0** | `86bb77vk6`'s check — never *independently* decisive here (always co-fired) |
| `syntax_valid` | **0** | — | never fired in 96 rows |
| `powershell_syntax_valid` | **0** | — | never fired (only 1–2 tasks had .ps1) |
| `forbidden_paths_untouched` | **0** | — | never fired (model never tried a forbidden path) |

### Warnings

| check | fired |
|---|---|
| `deliverables_present` | 40 |
| `unused_new_import` | 20 |
| `unexpected_deletion` | **0** |

### Read

- **Keepers, clearly earning their place:** `nonzero_diff` and `cross_module_missing_export`
  are 100% decisive whenever they fire (empty diff / broken import — both unambiguous).
  `module_level_name_order` and `required_files_touched` fire mostly on genuine incompleteness.
- **Never-fired checks stay** — `syntax_valid`, `powershell_syntax_valid`,
  `forbidden_paths_untouched`, `unexpected_deletion` are the cheapest possible checks and each
  catches a catastrophic/safety case. A low fire rate is the healthy state (same logic as the
  guard cluster's `done_without_edit` / `forbidden_paths`), *not* evidence they're useless.
- **`cross_module_circular_import` added no independent decisiveness** on this corpus (0 sole).
  n=3 — keep it (circular imports are a real bug class, and `86bb77vk6` filed it from a live
  miss), but note it's unproven.
- **`lint_clean` is the dominant check and carried a plausible precision risk, now closed
  structurally (2026-09-04).** It used to assume `C:/Nova` is always ruff-clean, so any
  violation on a touched file = diff-caused — but it lints the *whole current file* (not
  just added lines) in a worktree branched from master, so transient lint drift on master at
  gate time (`nova_api.py`, the most-churned file, accounted for 15 of the 26 dev-set fires)
  could in principle get misattributed. The 2026-09-02 held-out pass gave 3 clean OOD data
  points but couldn't *prove* the gap was closed, just that it hadn't fired yet. Fixed by
  instrumenting `_check_lint_clean(diff, root, base_ref)` to also lint each touched file's
  `base_ref` version (via `git show base_ref:path` piped to `ruff check
  --stdin-filename=... -`, no second worktree needed) and subtract the base version's
  violation multiset — matched by `(code, message)`, deliberately ignoring line number since
  a diff shifts everything below an edit — from the current version's. Only violations that
  survive that subtraction (i.e. genuinely new) get reported; a brand-new file (no base_ref
  version to subtract) still reports everything, preserving the original fail-safe behavior
  for that case. Verified live against 3 real throwaway git repos: a file with pre-existing
  `F401`s that gets a new `import json` correctly flags only the new `F401`/`I001` pair; a
  file whose only violation is pre-existing (edit touches an unrelated line) now correctly
  returns *no* reasons — the exact false-positive shape this check used to be structurally
  capable of; and a brand-new file's violations are still fully flagged. Re-run against the
  real repo's own last 3 commits: clean, as expected.

### Recommendation

**One completion-gate change, shipped 2026-09-04.** The gate is broadly doing real work; the
decisive checks are decisive on genuine problems; the quiet checks are cheap safety nets. The
one open question (`lint_clean` pre-existing-debt) is now closed via the base-ref diffing
above, rather than left open pending more held-out evidence.

## What still needs the held-out set (Initiative 1 follow-up)

Whether a check *generalizes* — i.e. does it keep its precision on tasks it wasn't built
from. That needs `logs/held_out_pool.jsonl` populated with hand-authored tasks (currently
empty). The dev-set / historical-corpus analysis above stands on its own as the first pass.

**This is now the critical path for the rest of Initiative 2.** Both gate groups audited so
far (ACI turn-loop guards, production completion gate) hit the same wall: the eval corpus is
6–10 distinct tasks. Directional conclusions are all this data supports. A real per-gate
verdict — especially "does check X hold its precision on tasks it wasn't tuned against" —
requires Initiative 1's held-out pool to actually exist.

## The A1-G2 turn-loop guards (separate, deferred)

The registry guards in `nova_orchestrator_runpod.py` / `_devstral.py` (repeat-call,
context-overflow, dead-code, self-verify nudge, goal re-anchor, file-allowlist, …) DO need
agentic runs to ablate, against the RunPod/Devstral backends, which cost real GPU money. Lower
priority given the 2026-08-12 pivot away from the dense-model lane — revisit only if that lane
comes back.
