# ACI Failure Mechanism Analysis — What "Familiarity" Actually Predicts

Written 2026-08-15, following up on `docs/aci-task-familiarity-finding.md`. That doc
established *that* task familiarity predicts success better than size or difficulty; this one
digs into *why*, using the existing `logs/aci_harness_log.jsonl` (122 runs) plus 3 fresh
live re-runs against Qwen2.5-Coder-7B with `verbose=True` transcripts captured (the original
122-run corpus log only keeps summary fields — `slug`/`turns_used`/`final_status`/
`test_passed`/`parse_method_counts`/`parse_failures` — never the actual model output, so a
mechanistic answer required watching new runs directly, not just re-reading old data).

## The corpus-wide breakdown (104 failed runs across the 25 never-pass exercises)

Bucketed every failed run by `final_status` and `turns_used`:

| Bucket | Count | % of failures |
|---|---|---|
| Quick give-up — called `done` within 4 turns | 32 | 31% |
| Mid-length completed (5-11 turns) but wrong | 28 | 27% |
| Late completed (12-15 turns) but wrong | 1 | 1% |
| Max-turns reached — never called `done` at all | 43 | 41% |

Two dominant, roughly equal-sized failure modes account for ~72% of all failures — and
neither one is "the model reasoned about the puzzle and got the logic wrong." Both are
process failures that happen *before or instead of* real problem-solving.

## Three live transcripts, three distinct mechanisms

### 1. `bob` (a "winner-adjacent" exercise, textbook-familiar) — stuck-loop exhaustion

Re-run live, `max_turns_reached`, 15/15 turns burned, `test_passed: False`. The model's
**logic was entirely correct** on turn 2 — right conditions (`isupper()`, `endswith("?")`,
`strip()`), right response strings ("Sure.", "Whoa, chill out!", "Fine. Be that way!",
"Whatever."). But the submitted code had one stray `}` character embedded mid-body (JSON/dict
brace syntax leaking into the Python code value), which `aci.edit()` correctly rejected:
`{"accepted": false, "syntax_error": "unmatched '}' (<unknown>, line 10)"}`.

Turns 3 through 15 then resent the **byte-for-byte identical malformed payload** thirteen
times in a row. The model never adjusted a single character in response to the same error
message repeated thirteen times. This is not a reasoning failure — the underlying solution
was right — it's a **failure to use repeated identical negative feedback as a signal to
change approach**.

### 2. `zebra-puzzle` (genuinely unfamiliar, puzzle-shaped) — premature abandonment, no attempt

Re-run live, `completed`, only 3 turns, `test_passed: False`. Full sequence:
1. `find_file("zebra_puzzle")` → found the file.
2. `search_dir("Zebra Puzzle")` → empty (searching for the puzzle's own name in file
   contents, not a meaningful query).
3. `done` — no `view` call, no `edit` call, zero code ever written.

The model never even looked at the file it had already located, let alone attempted the
puzzle. This isn't "tried and failed at constraint-satisfaction logic" — it's declining to
engage with the task at all once early exploration didn't turn up something immediately
useful.

### 3. `affine-cipher` (unfamiliar, modular-arithmetic) — self-inflicted false negative

Re-run live, `completed`, only 2 turns. Verified directly against `aci.find_file()`:

```
initial_files (from find_file(".py")): ['affine_cipher.py', 'affine_cipher_test.py']
find_file("affine cipher")  -> []   # the model's own turn-1 query (space, no underscore)
find_file("affine_cipher")  -> ['affine_cipher.py', 'affine_cipher_test.py']
```

The correct filename was **already sitting in the model's own first-turn prompt** (the
harness always seeds "Files in your working directory: [...]" up front — see
`nova_aci_harness.py`'s `run_exercise()` comment on the `bob` filename-guessing gotcha this
was originally built to prevent). The model ignored that and ran a redundant, malformed
search using a natural-language phrase with a space instead of the exact filename it had
already been given. That search returned empty (correctly — the query itself was wrong), and
the model treated an empty *search* result as proof the *file* didn't exist, then quit via
`done` without ever calling `view`.

## Reframing the familiarity finding

The original finding (`docs/aci-task-familiarity-finding.md`) is still correct as a
correlation, but "familiarity predicts success" is not really about the model's capacity to
*reason through* an unfamiliar problem — `bob`'s transcript shows genuinely correct reasoning
producing a genuinely wrong outcome for purely mechanical reasons. What familiarity actually
seems to modulate is **process robustness**:

- On a well-worn task, the model commits to a real attempt and its first substantive answer
  tends to be logically right — but a single mechanical slip (a stray character, a malformed
  tool argument) can still derail the whole run, because the model does not treat repeated
  identical error feedback as a cue to change its approach.
- On a less-worn or oddly-named task, the model's *tool-use* becomes less confident before it
  ever reaches the underlying logic — vaguer search queries, more willingness to accept an
  empty/negative result as decisive, and a much lower bar for declaring `done` without having
  produced anything. Unfamiliarity shows up as reduced persistence and self-trust in the
  *process*, not as a demonstrated inability to solve the puzzle once actually attempted.

Task novelty is best read as `task shape` correlating with `how likely the model is to fully
engage the tool loop`, not `task shape` correlating with `raw problem-solving capability`.
Both `zebra-puzzle` and `affine-cipher` quit before ever writing a line of code — there is no
capability signal to read from either transcript, because the model never tried.

## Two concrete, buildable gaps this points at

Nova already has precedent for both of these guards in the RunPod/Devstral coding-agent
backends (`nova_orchestrator_runpod.py`, `86bb4gy0y` punch list) — the ACI harness currently
has neither:

1. **Repeat-failed-call guard.** `nova_orchestrator_runpod.py` already refuses "an exact
   repeat of an already-failed call" for its own turn loop. `nova_aci_harness.py`'s
   `run_exercise()` loop has no equivalent — `_execute_tool()` just re-runs whatever it's
   given, including the identical rejected edit `bob` resent 13 times. A cheap dedup check
   (compare each new tool call against the immediately-preceding failed one) would very likely
   have ended `bob`'s run in 3 turns instead of 15, either by forcing the model to produce a
   different payload or by surfacing a sharper, more specific correction message than a bare
   repeated syntax error.
2. **No-edit-before-done guard.** Nothing currently stops `done` from being accepted on a run
   that never called `edit` even once. This is the same shape of problem
   `nova_completion_gate.py`/`self_verify_nudge` was built to catch in the interactive
   coding-agent lane (CLAUDE.md's "Strengthened `self_verify_nudge`" entry, 2026-08-06) —
   accepting a stop without checking that real work was actually done. A minimal version here:
   refuse `done` if zero successful `edit` calls occurred, with a corrective nudge back to the
   model rather than accepting the exit.

Neither is built yet — this doc is diagnosis, not a shipped fix. Both are small, scoped
changes to `nova_aci_harness.py`'s existing turn loop, not a redesign.

## What this doesn't establish

Three live transcripts is enough to identify and name distinct failure mechanisms with
confidence (each is a directly observed, reproducible behavior, not inferred) — it is not
enough to say what *fraction* of the 43 max-turns and 32 quick-give-up runs each mechanism
specifically accounts for. The corpus-wide turn-count/status buckets are exact counts from all
104 real failed runs; the *mechanisms* behind each bucket are established from 3
representative examples, one per bucket shape, not all 104 re-run individually.

## Guarded re-run results (2026-08-17)

Built both guards named above directly into `nova_aci_harness.py` (`GUARD_REPEAT_FAILED_CALL`,
`GUARD_DONE_WITHOUT_EDIT`, capped at `MAX_DONE_WITHOUT_EDIT_NUDGES = 2` before a genuine stop
is accepted under a distinct `abandoned_after_nudge` status), verified correct against a
deterministic scripted test (a fake model client resending an identical broken edit, then
calling `done` repeatedly with no successful edit — the guard refused the repeat without
re-executing it, escalated its message on the second refusal, nudged the premature `done`
twice, then accepted the third stop under `abandoned_after_nudge` rather than conflating it
with a genuine completion), then re-ran the full 30-exercise corpus at `--repeat 4` (120 runs).
The original 122-run baseline is preserved at
`logs/aci_harness_log_baseline_2026-08-15.jsonl`; the 2 manual/deterministic sanity-check runs
that preceded the batch are separated into `logs/aci_harness_log_guard_sanity_checks.jsonl` so
the corpus comparison stays apples-to-apples.

| | Baseline (no guards) | Guarded |
|---|---|---|
| Runs | 122 | 120 |
| Passed | 9 (7.4%) | 7 (5.8%) |
| Avg turns used | 8.57 | 9.07 |
| `max_turns_reached` rate | 36.9% | 38.3% |

**Pass rate did not improve — if anything it's flat-to-slightly-worse, well within noise at
this sample size.** This was the expected, stated outcome going in: the guards were designed
to make failure legible, not to force success. They deliver on that: `repeat_failed_call`
fired 53 times and `done_without_edit` fired 10 times across 120 real runs — recurrence data
that simply did not exist before (the pre-guard harness could not distinguish "resent an
identical broken call" from any other turn). Of the 46 runs that still hit `max_turns_reached`,
22 (48%) had at least one guard fire along the way — meaning the guard genuinely intervened in
close to half of the stuck-loop cases, but blocking the *exact* repeat did not stop the model
from generating a *new, differently wrong* call and continuing to spin. `abandoned_after_nudge`
never fired organically in the 120-run corpus (only in the deterministic test designed to
force it) — every real run that got nudged for calling `done` too early either went on to make
a real edit attempt or ran out of turns some other way, never a third bare `done` with nothing
attempted.

**Honest reframe of the guards' value:** they don't fix the underlying process-robustness gap
`docs/aci-failure-mechanism-analysis.md`'s three transcripts identified — a model that keeps
producing *varied* wrong attempts is just as stuck as one repeating an identical wrong attempt,
and no single-call-level guard can tell the difference between genuine exploration and
directionless flailing. What they do is turn an invisible failure mode into a counted one.
That counted signal is exactly the raw material Eval Harness Initiative 7 (accumulated-failure
tracking) and Initiative 8 (correlation-based failure prediction) are designed to consume —
this corpus run is effectively a first live data source for both, ahead of either initiative
being built.

## Third guard: same-path repeated failure (2026-08-17, `86bbfwm3a`)

Re-examining the guarded run above surfaced the actual mechanism behind that 48% figure: the
model wasn't resending identical calls (that's already refused) — it was generating a *new,
differently wrong* edit against the same spot each time. `GUARD_REPEAT_FAILED_CALL` structurally
cannot see this, since it keys on exact call identity. Built `GUARD_SAME_PATH_REPEATED_FAILURE`:
tracks failed `edit` calls per path regardless of content, appends a corrective note (not a
refusal) once a path crosses 3 failures — mirrors `nova_orchestrator_runpod.py`'s own
`file_replace` fallback nudge, built for the identical real loop shape (`86bb728nj`). Also
ported SWE-agent's explicit empty-result feedback into `find_file`/`search_file`/`search_dir`
the same day (`_format_list_result()`), motivated by the `affine-cipher` transcript.

Re-ran the full 120-run corpus a third time:

| | Baseline | 2-guard | 3-guard |
|---|---|---|---|
| Runs | 122 | 120 | 120 |
| Passed | 9 (7.2%) | 7 (5.8%) | 9 (7.5%) |
| Avg turns used | 8.57 | 9.07 | **7.76** |
| `max_turns_reached` rate | 36.0% | 38.3% | **25.8%** |

**This time the effect is real, not just better telemetry.** Pass rate recovered to roughly
baseline (7.5% vs. 7.2%, still noise-level at this sample size) — but `max_turns_reached` dropped
from 38.3% to 25.8%, a genuine ~12-point reduction in runs that burn the entire budget without
resolving, and average turns per run dropped from 9.07 to 7.76. `same_path_repeated_failure`
fired 34 times across 120 runs — confirming the pattern is common, not a one-off. One
`abandoned_after_nudge` fired organically this run (the first time that status has occurred
outside a deliberately scripted test) — a real case of the model calling `done` with nothing
attempted, getting nudged twice, and genuinely having nothing more to offer on the third try,
correctly distinguished from every other outcome bucket instead of hiding inside `completed`.

**Honest read:** the model still doesn't solve meaningfully more exercises — the ceiling on raw
capability hasn't moved. What moved is process efficiency: fewer runs get stuck spinning through
the full 15-turn budget, and runs resolve (whether pass or fail) faster on average. That's a real
result, distinct from the flat null result the first two guards produced alone, and it's exactly
the kind of signal Eval Harness Initiative 7/8 are built to consume at scale.

> **⚠️ Correction (2026-08-31, Eval Harness Initiative 2 — `docs/aci-guard-cluster-ablation.md`):**
> The "efficiency improved" claim in this section does **not** survive individual ablation. The
> 2026-08-17 "3rd guard" batch shipped *two* changes at once — `same_path_repeated_failure` and
> the `_format_list_result()` empty-result search feedback — and its `max_turns 38.3 → 25.8`
> figure was a comparison between *separate batches*. Ablating each change on its own at
> repeat=6 (n=180/condition) now shows **removing either one *improves* efficiency** by
> ~0.6 turns / ~7 pts `max_turns` — the opposite direction. `max_turns_reached %` measured
> 30.6 / 36.7 / 37.8 across three clean baseline batches this session, so the original 38 → 26
> move is within noise. The likely truth: this was batch-to-batch variance read as signal.
> `same_path_repeated_failure` has since been demoted to opt-in (`--same-path-guard`);
> `_format_list_result()` is under review. `repeat_failed_call` / `done_without_edit` are
> confirmed keepers by that same ablation.
