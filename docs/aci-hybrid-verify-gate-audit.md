# ACI Hybrid-Verify Gate Audit — Splitting "Gamed" from "Idiom"

Written 2026-08-29 for Eval Harness Initiative 2 (`86bbcfv9d`, "audit existing gates
individually"), applied to `nova_aci_harness.py`'s hybrid-verify gate. Feeds Nova Training
Pipeline Phase 5 (`86bbcfpd1`, hybrid verification at inference time). Companion to
`docs/aci-failure-mechanism-analysis.md`.

## What the gate does today

`--hybrid-verify` (default off) inserts a two-stage check between a `done` call and its
acceptance, in `run_exercise()`'s turn loop. `has_successful_edit` must already be True — there
is nothing to verify before the model has changed anything.

### Stage 1 — execution (`_run_real_tests`, cheap, deterministic)

Runs the exercise's real `*_test.py` via stdlib `unittest` inside the working copy. This is the
same objective check the final scoring metric uses. A real test failure returns a nudge with
the real test output truncated to 1500 chars and **never reaches Stage 2** — no reason to pay
for a style opinion on code that doesn't work.

### Stage 2 — generative (`_generative_style_verifier`, one Claude call, only once tests pass)

A single `claude-sonnet-5` call, `max_tokens=200`, no `tools` argument (a judge, never a
writer — `feedback_reviewer_model_no_tools_guarantee`). Billed to `ANTHROPIC_API_KEY` /
Anthropic Console, not the subscription.

**Before this change** it checked two unrelated things and collapsed them into one verdict:

1. A *gamed / hardcoded* solution — output values copied from the visible test cases rather
   than a genuine implementation of the described logic.
2. *Clearly unidiomatic* Python a competent developer would not write.

Either one produced `CONCERNS: <reason>`, which hard-blocked `done` and nudged: "Address this
before calling done again."

### The `done`-handler control flow (unchanged in shape)

- Separate rejection budgets, `test_fail_nudges` and `style_concern_nudges`, each capped at
  `MAX_HYBRID_VERIFY_NUDGES` (2). This split is a real 2026-08-20 fix — a single shared budget
  let test failures alone exhaust it before the generative verifier ever ran.
- `--early-abandon` and `--regression-guard` both read the same `pass_fraction` signal
  (`_parse_test_pass_fraction`) that Stage 1 produces on every check.
- On a blocked `done`, `GUARD_HYBRID_VERIFY_REJECTED` fires and the nudge goes back as a
  user-role message; the loop continues.

## The problem: one verdict, two very different stakes

The two concerns the generative half checks do not belong together:

| Concern | What it is | Stakes | Verifier reliability on tiny exercises |
|---|---|---|---|
| Gamed / hardcoded | The solution passes tests without implementing the logic — a **correctness / cheating** issue | High — the test suite has been defeated, the pass is fake | Decent (a `if name == 'Alice'` ladder is visually obvious) but not perfect — a genuinely simple correct solution can look "too simple" |
| Unidiomatic | Real implementation, just ugly | Low — subjective, and the solution is *already correct by the objective measure this whole harness is built on* | Noisy — "unidiomatic" is a judgment call that varies run to run |

Blocking `done` on the second one asks the model to keep editing a solution that already
passes every real test, to satisfy a subjective opinion. That is where the harm comes from.

### The `octal` failure (motivating trace, from `project_early_abandon_ab_and_snapshot_finding`)

Live verbose re-run, 2026-08-25:

1. The model wrote `return int(digits, 8)` — correct, all tests pass, `pass_fraction` 1.0.
2. The style verifier flagged it as unidiomatic and blocked `done`.
3. Chasing the nudge, the model rewrote the function as a **self-shadowing nested `def
   parse_octal`** — the outer function now just defines an inner function and implicitly
   returns `None`. A real regression.
4. It never recovered. `--early-abandon` correctly caught the stall; final `test_passed=False`
   despite a correct answer having existed a few turns earlier.

The gate destroyed a working solution over a style opinion.

### Why `--regression-guard` is a net, not a fix

`--regression-guard` (shipped `86bbmj2hw`, default-on since 2026-08-27) restores a 100%-passing
snapshot at run end if the model regressed and never recovered. It works — 5 genuine causal
restores across 210 runs, zero false positives — but:

- It only fires when the exact "100% pass → style reject → regress → never recover" sequence
  recurs, ~2.4% of runs in this corpus.
- It fixes the outcome by **discarding the style feedback entirely** — the "concern" is simply
  ignored in the final score. If ignoring the concern is the right answer at run end, it was
  the right answer at the gate.
- The turns spent thrashing after the idiom nudge are still wasted.

The root cause is upstream: the gate treats a subjective idiom opinion with the same blocking
force as a genuine cheating flag.

## The change: split the verdict

`_generative_style_verifier` now returns one of three verdicts (system prompt updated to ask
for exactly `ACCEPT`, `GAMED: <reason>`, or `IDIOM: <reason>`; an unrecognized reply falls back
to `ACCEPT` — fail open, a judge that can't answer clearly should not block a passing
solution):

- **`ACCEPT`** — no concern. `done` accepted. Unchanged.
- **`GAMED`** — hard-block, unconditionally, in both flag states. Nudge names the gaming
  concern specifically ("a likely gamed or hardcoded solution … Replace it with a genuine
  implementation").
- **`IDIOM`** — behaviour depends on the new `--advisory-idiom` flag (default **off**):
  - flag **off** → blocks `done`, exactly as `CONCERNS` did before (pre-split behaviour
    preserved for a clean A/B baseline).
  - flag **on** → logged (`style_idiom_note` on the result) and **accepted**. No nudge, no
    re-loop.

The categorization itself runs on every hybrid-verify pass regardless of the flag, so every
run logs which concern fired. New per-run result fields: `advisory_idiom_enabled`,
`style_idiom_note`, `style_gamed_rejections`. Batch summary and single-run output print the
`GAMED` / `IDIOM` split.

### Interaction with `--regression-guard`

None, by construction. When `--advisory-idiom` accepts an `IDIOM` solution, the run ends
`completed` with tests passing, so the run-end snapshot restore (`if regression_guard and not
test_passed …`) simply never triggers. The two flags are orthogonal — the guard remains the
safety net for the `GAMED`-block path and for genuine test-failure thrash.

### Flag, not default

Opt-in `--advisory-idiom`, matching the `--diff-format` / `--early-abandon` / original
`--regression-guard` pattern. Promote to default-on (with a `--no-advisory-idiom` opt-out) only
after a real full-corpus A/B batch (`scripts/run_advisory_idiom_ab_test.py`) shows a pass-rate
edge **without** raising `GAMED` rejections — the split must not weaken the one high-value
check. ~$1–2 Console per repeat=2 batch.

## Verified (2026-08-29, direct gate calls against a real `two-fer` working copy)

| Solution | Verdict | `advisory_idiom=False` | `advisory_idiom=True` |
|---|---|---|---|
| Correct + idiomatic | `ACCEPT` | `gate_passed=True` | `gate_passed=True` |
| Correct + egregiously unidiomatic (char-by-char `while` loops) | `IDIOM` | `gate_passed=False` (blocks) | `gate_passed=True` (accepts) |
| Hardcoded `if name == 'Alice'` ladder | `GAMED` | `gate_passed=False` | `gate_passed=False` (still blocks) |

Plus a full harness run (`two-fer --hybrid-verify --advisory-idiom`, real Ollama + one real
style call) completing clean with the new tuple unpacking and result fields.

## Audited, not changed — these are deliberate and stay

- **Execution before generation.** A failing solution never reaches the paid style call.
- **Separate `test_fail_nudges` / `style_concern_nudges` budgets.** Real 2026-08-20 fix; a
  shared budget starved the verifier.
- **Fail-open default.** An empty or unparseable verifier reply is `ACCEPT`, not a block.
- **`_run_real_tests` timeout handling.** `subprocess.TimeoutExpired` → normal `(False, msg)`
  result (real 2026-08-22 fix — an infinite loop once crashed a whole batch).
- **The test file is never editable.** `nova_coding_aci.edit()` refuses any `*_test.py` path
  (`86bbmn9vp`, `project_aci_test_file_tampering_finding`).

## Open question for Phase 5 (do not solve here)

Is `GAMED` detection itself reliable? On tiny exercises a genuinely simple correct solution
(`return int(digits, 8)`) can superficially resemble a gamed one, and a hardcoded ladder that
happens to cover an incomplete test suite can pass Stage 1. This is exactly the "generative
verifiers on novel out-of-domain failures" territory Eval Harness Initiative 3 (`86bbcfva4`)
is for. Flag it there; the split above is a strict improvement regardless of how that lands.
