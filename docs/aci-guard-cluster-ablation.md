# ACI Turn-Loop Guard Cluster — Individual Ablation

Written 2026-08-29 for Eval Harness Initiative 2 (`86bbcfv9d`, "audit existing gates
individually"). Companion to `docs/aci-hybrid-verify-gate-audit.md` (which audited the
hybrid-verify gate) and `docs/aci-failure-mechanism-analysis.md` (which built three of these
guards and measured them as a block).

## The gap this closes

`docs/aci-failure-mechanism-analysis.md` measured `nova_aci_harness.py`'s turn-loop guards
**cumulatively** — baseline → 2-guard → 3-guard — and reported the aggregate:

| | Baseline | 2-guard | 3-guard |
|---|---|---|---|
| Passed | 9 (7.2%) | 7 (5.8%) | 9 (7.5%) |
| Avg turns | 8.57 | 9.07 | **7.76** |
| `max_turns_reached` | 36.0% | 38.3% | **25.8%** |

That tells us the *stack* helps turn-efficiency (the ~12-point `max_turns_reached` drop is
real) but not which guard did the work, or whether any guard is net-negative on its own.
Initiative 2's whole point is that this is exactly the wrong granularity — "some gates carry
most of the lift, others contribute little or actively hurt precision" (runbook). You can't
see that from a cumulative curve.

## The four ablatable guards

All always-on in `run_exercise()`'s turn loop, no prior toggle:

| Guard | Fires when | Effect |
|---|---|---|
| `repeat_failed_call` | model resends a byte-identical already-failed tool call | refuses, does not re-execute (motivated by `bob` resending one broken edit 13×) |
| `done_without_edit` | `done` called before any successful `edit` | nudges up to `MAX_DONE_WITHOUT_EDIT_NUDGES` (2), then accepts under `abandoned_after_nudge` |
| `same_path_repeated_failure` | ≥ `SAME_PATH_FAILURE_THRESHOLD` (3) failed edits on one path, not necessarily identical | appends a corrective note to the tool result (not a refusal) |
| `multiple_calls_ignored` | a turn's raw text carries a second tool call the parser dropped | appends `MULTIPLE_CALLS_NUDGE` |

`--hybrid-verify` / `--early-abandon` / `--regression-guard` are separate axes with their own
flags and their own A/B scripts — not part of this cluster.

## Ablation infra (shipped 2026-08-29)

- `nova_aci_harness.ABLATABLE_GUARDS` — the frozenset of the four names above.
- `run_exercise(disabled_guards=frozenset({...}))` / `run_all_exercises(...)` — for each name
  in the set, that guard's **effect** is skipped (the loop reverts to pre-guard behaviour at
  that point: re-execute the repeat; accept the no-edit `done` and stop; drop the same-path
  note; drop the multi-call note). A would-have-fired count is still recorded per run in
  `guards_suppressed` (logged on the result), so a flat ablation result can be read correctly
  — "guard doesn't matter" vs. "guard barely got exercised in this corpus".
- `--disable-guard NAME` CLI flag (repeatable, `choices`-validated).
- `scripts/run_guard_ablation.py` — baseline (all on) + one condition per guard (that guard
  off), full corpus, `repeat=N`. `--hybrid-verify` stays off, so the batch spends **$0**
  (Ollama only). Reports pass rate, avg turns, `max_turns_reached` %, and the delta vs.
  baseline per guard.

**Verified** via a deterministic scripted test (fake Ollama client, no API): with
`repeat_failed_call` active a repeated broken edit fires the guard twice and is never
re-executed; disabled, it fires zero times, `guards_suppressed["repeat_failed_call"] == 2`,
and the call is re-executed. With `done_without_edit` active an immediate `done` ends
`abandoned_after_nudge` after 2 nudges; disabled, it ends `completed` on turn 1 with
`guards_suppressed["done_without_edit"] == 1`.

## Planned batch (not yet run)

`nova-env/Scripts/python.exe scripts/run_guard_ablation.py --repeat 2` — 5 conditions × 31
exercises × 2 ≈ 310 runs, a few hours on this hardware, run backgrounded. Deferred to a
separate go-ahead.

**Prediction:** pass rate flat across all conditions (these guards don't lift the capability
ceiling — established repeatedly). Real signal in `avg_turns` / `max_turns_reached` %:
`same_path_repeated_failure` most likely carries the efficiency lift the 3-guard cumulative
run showed; `multiple_calls_ignored` and `repeat_failed_call` plausibly contribute little
individually; `done_without_edit` may not matter much either way (it fires rarely and
`abandoned_after_nudge` almost never triggers organically).

## Results

_(pending the batch run)_
