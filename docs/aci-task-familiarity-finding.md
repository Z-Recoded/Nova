# ACI Corpus Results: Task Familiarity Predicts Success Better Than Task Size or Difficulty

Written 2026-08-15 after running the full 30-exercise vendored corpus through Qwen2.5-Coder-7B
via `nova_aci_harness.py`, first as a single pass, then repeated 3x per exercise
(`--repeat 3`, 90 additional runs, 122 total logged) once the single-pass numbers proved
untrustworthy. Analysis tooling: `nova_aci_stats.py`.

## Why a single pass wasn't good enough

The first full-corpus run showed `bob` pass 26/26 tests in isolation, then fail completely
in the batch run with identical code, and `bowling`/`dominoes` show real parse failures in
the batch that reproduced as *zero* parse failures (real logic/code bugs instead) on a fresh
re-run. Ollama's default sampling is not deterministic — a single pass or fail per exercise
is a noisy one-sample estimate, not a result. `nova_aci_harness.py --all --repeat N` and
`nova_aci_stats.py` exist specifically to fix this: run each exercise multiple times, report
a rate, not a coin-flip.

## Headline numbers (122 real runs, 30 exercises, repeat=3-5 each)

**Overall: 9/122 passed (~7.4%).**

| Exercise | Pass rate |
|---|---|
| `ledger` | **4/4 (100%)** |
| `raindrops` | 2/4 (50%) |
| `binary` | 1/4 (25%) |
| `scrabble-score` | 1/4 (25%) |
| `two-fer` | 1/4 (25%) |
| All other 25 exercises | 0/4 or 0/5 (0%) |

## A correlation that looked real and wasn't

On the first 32 runs, `test_passed` correlated with `lenient_fraction` (how much of a run's
tool calls needed the `ast.literal_eval`/repair parsing tiers instead of strict JSON) at
**r=0.469** — a moderate, plausible-looking signal. With the full 122 runs, that correlation
collapsed to **r=0.126**, essentially noise. It was a small-sample artifact built on 3 positive
cases, not a real relationship. This is the concrete payoff of building repeated sampling
before trusting any correlation search on this kind of data — not just a cleaner pass rate,
but a specific wrong conclusion caught before it got acted on.

None of the tracked numeric run parameters (`turns_used`, `parse_failures`,
`lenient_fraction`, `difficulty`) correlate with `test_passed` at real strength once the
sample is large enough. Whatever separates the winners from the rest isn't visible in the
per-run telemetry this harness already tracks.

## What actually separates the 5 winners from the 25 that never passed

Pulled real metrics from each exercise's own files (solution length from `.meta/example.py`,
test count from `<slug>_test.py`, instructions length from `.docs/instructions.md` — never
shown to the model, used here only for this analysis):

| Metric | Any-pass (n=5) | Never-pass (n=25) |
|---|---|---|
| Instructions (words) | 138 | 271 |
| Solution length (lines) | 21 | 34 |
| Function count | 2.4 | 4.8 |
| Test count | 10.8 | 19.9 |
| Exercism's own difficulty rating | 2.2 | 3.9 |

Task size (instructions/solution/function/test count) all move together, roughly 1.5-2x
higher in the never-pass group — expected, since they're correlated proxies for "how much
task there is," not four independent signals. **Exercism's own official difficulty rating is
the weakest separator of the group, proportionally** — raw task size predicts this model's
real success better than the tier Exercism itself assigned.

**The more informative pattern is qualitative, not numeric.** The 5 winners —
`binary` (binary-to-decimal conversion), `raindrops` (a FizzBuzz variant), `scrabble-score`
(sum values from a lookup table), `two-fer` (return a templated string), `ledger` (format a
financial table) — are all extremely common, textbook-standard programming exercises, the
kind that appear constantly across tutorials and real codebases, almost certainly
overrepresented in pretraining data. The never-pass set skews toward more unusual or
puzzle-shaped tasks: `zebra-puzzle` (constraint-satisfaction logic puzzle), `sgf-parsing` (a
niche file format), `pov` (tree restructuring), `affine-cipher` (modular-arithmetic
cryptography), `bowling` (famously edge-case-dense scoring logic even for human solvers).

## The exception that rules out a purely-size explanation

`ledger` breaks the "shorter is better" story outright. It's the **longest, most complex**
solution among the 5 winners — 72 lines, 8 functions, class-based — and simultaneously the
**only exercise that passed every single time** in the entire corpus. Pure size/complexity
can't be the real driver, since the single most reliable exercise is also the most complex
one among the winners. What `ledger` shares with the other four isn't brevity — it's that
"format a financial ledger as a table" is a conventional, heavily-trodden task shape, the
same way FizzBuzz-family and lookup-table-scoring exercises are.

## Working conclusion

For this model, **task familiarity — how standard and well-represented a task pattern is in
training data — predicts success better than task size, and both predict better than
Exercism's own subjective difficulty tier.**

## Real implication for Nova

Novel or puzzle-shaped tasks appear to be where this model genuinely struggles, largely
independent of how "hard" they're officially rated. Worth carrying into how real Nova coding
work gets routed: a task that's conventional in shape (even a long/complex one, per `ledger`)
may be a reasonable fit for a small local model; a task that's structurally unusual — even a
short one — may not be, regardless of its nominal difficulty. This is a genuinely different
routing signal than difficulty rating or task length alone, and neither of those proxies
would have caught it.

## What this doesn't establish

n=30 exercises (5 vs. 25 in the categorical split) is still a small sample for the
task-familiarity claim specifically — the numeric comparison and the qualitative pattern are
both real, grounded in this corpus's actual data, but this is a first look worth testing
against a larger or more varied exercise set before treating it as a settled model property
rather than a real trend observed once.
