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

## Results — 2026-08-29 ($0, Ollama-only)

Two batches: a repeat=2 sweep of all four guards (300 runs) + a focused repeat=6 re-run of
`same_path_repeated_failure` (360 runs) after that guard showed a surprising reversal.

### repeat=2 sweep, all four guards

`scripts/run_guard_ablation.py --repeat 2`, Qwen2.5-Coder-7B, 30 exercises × 2 per condition.

| Condition | Pass | Avg turns | `max_turns` % | would-have-fired |
|---|---|---|---|---|
| baseline (all guards on) | 4/60 | 8.78 | 36.7 | — |
| −`done_without_edit` | 3/60 | 8.62 | 33.3 | 4 |
| −`multiple_calls_ignored` | 7/60 | 8.93 | 38.3 | **0** |
| −`repeat_failed_call` | 3/60 | 9.13 | 40.0 | **76** |
| −`same_path_repeated_failure` | 3/60 | **8.03** | **28.3** | 22 |

### Pass rate: noise, as predicted

3–7/60 across every condition. At n=60 and a ~7% base rate the sampling spread alone is
±~6 points — the +3 (`multiple_calls_ignored`) and −1s are all inside it. No guard moves the
capability ceiling individually, consistent with every prior ACI finding
(`docs/aci-failure-mechanism-analysis.md`, `project_aci_task_familiarity_finding`). The
+3 on `−multiple_calls_ignored` is definitely noise — that guard **fired zero times** in the
batch, so removing it changed nothing causally.

### Per-guard read

- **`repeat_failed_call` — the workhorse. Keep.** 76 would-have-fired, ~5× any other guard.
  Removing it is the only ablation that pushed *both* efficiency metrics the wrong way
  (avg turns +0.35, `max_turns` +3.3). Its origin case (`bob` resending one broken edit 13×)
  is a live, common pattern.

- **`multiple_calls_ignored` — dormant for this model. Keep, but document.** 0 fires in 300
  ablation runs (and 0 in the 120 advisory-idiom runs; ~2 in ~420 total). It was built from a
  `--progress-framing` transcript artifact — a real bug class, but one Qwen2.5-Coder-7B does
  not hit without that flag. Near-zero cost, still worth keeping for other models / with
  progress-framing on, but it contributes nothing measurable here. This is exactly the
  runbook's "some gates contribute little" case.

- **`done_without_edit` — rare, low-impact, keep as a safety net.** Only 4 fires; efficiency
  deltas (−0.17 turns, −3.3 `max_turns` %) are within noise. But it's a *correctness* guard
  (don't accept a `done` with zero work done), not an efficiency one — a low fire count is the
  expected, healthy state, not evidence it's useless.

- **`same_path_repeated_failure` — net-negative on efficiency. Reproduced. Recommend flipping
  it off by default.** Removing it *improved* turn-efficiency in both batches:

  | | repeat=2 (n=60) | repeat=6 (n=180) | pooled (n=240) |
  |---|---|---|---|
  | Δ avg turns | −0.75 | −0.56 | ~−0.6 |
  | Δ `max_turns` % | −8.3 | −6.1 | ~−6.8 |
  | Δ pass | −1 | +3 | +2 (noise) |
  | would-have-fired | 22 | 72 | — fires on ~40% of runs |

  Same direction, similar magnitude, at 4× the combined sample — this is a real finding, not
  noise. The guard is heavily exercised (~40% of runs) and removing it makes the harness
  *faster* with no pass-rate cost.

  **This contradicts `docs/aci-failure-mechanism-analysis.md`** (2026-08-17), where adding it
  as the "3rd guard" appeared to drop `max_turns` 38.3 → 25.8. Most likely a **misattribution**:
  that same 2026-08-17 change also "ported SWE-agent's explicit empty-result feedback into
  `find_file`/`search_file`/`search_dir` … `_format_list_result()`" — a change that directly
  targets the `affine-cipher`/`zebra-puzzle` "quit after an empty search" failures the doc
  itself describes. Two efficiency changes shipped in one batch; the credit went to the wrong
  one. `same_path`'s own nudge ("Stop guessing at small variations. Use `view` to re-read the
  file's real current content **in full**…") pushes the model into an extra `view` turn — and
  `repeat_failed_call` already covers the exact-repeat case this was meant to complement.

  **APPLIED 2026-08-30 (Marvin's call): `same_path_repeated_failure` is now opt-in, off by
  default.** New `run_exercise(same_path_guard=False)` param + `--same-path-guard` flag,
  mirroring how `--diff-format` was kept as an off-by-default tested reference after its own
  negative A/B. It was removed from `ABLATABLE_GUARDS` (now 3 always-on guards). The guard's
  code, threshold, and nudge text are unchanged — only its default. Verified deterministically
  (fake Ollama client): default → `same_path_guard_enabled=False`, 0 fires on 5 different
  broken edits to one path; `--same-path-guard` → fires 3×.

### advisory-idiom A/B (same batch, `--hybrid-verify` on)

| Condition | Pass | Avg turns | Style calls | IDIOM verdicts | GAMED rejections |
|---|---|---|---|---|---|
| baseline (IDIOM blocks) | 7/60 | 13.50 | 8 | **0** | 4 |
| `--advisory-idiom` | 10/60 | 13.30 | 9 | **0** | 3 |

**Inconclusive — the IDIOM path never fired.** Across 120 runs the style verifier returned
`GAMED` or `ACCEPT` only, never `IDIOM`. The `octal` scenario the flag targets (100%-passing
solution → verifier calls it unidiomatic → model breaks it) needs both a fully-passing
solution *and* an "unidiomatic" verdict on it, and that pair did not occur once. GAMED
rejections did **not** rise (4 → 3), so the split didn't weaken the cheat catch. Style cost
was ~$0.10, not the estimated $1–2 — most runs never reach a passing solution, so the paid
call rarely runs. **Keep `--advisory-idiom` opt-in; cannot promote on no evidence.** A
meaningful re-test needs a model/corpus that produces more passing-but-unidiomatic solutions,
or the production coding lane.

## Recommendation

1. **`repeat_failed_call`, `done_without_edit` — confirmed keepers, no change.**
2. **`multiple_calls_ignored` — dormant for this model, keep (near-zero cost), document.**
3. **`same_path_repeated_failure` — flipped to opt-in (off by default), DONE 2026-08-30.** Two
   reproduced batches (n=240/condition combined) showed it costs ~0.6 turns/run and ~7 points
   of `max_turns_reached` with no pass-rate benefit; the 2026-08-17 "it helped" result was
   probably crediting the wrong same-day change (empty-result search feedback). Now behind
   `--same-path-guard`, code otherwise untouched.
4. `--advisory-idiom` — stays opt-in, flagged for a future re-test (0 IDIOM verdicts in 120 runs).
