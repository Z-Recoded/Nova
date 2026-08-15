# Coding Specialist: Shared Exercise Corpus Plan

Scoped plan for pulling the small, real exercise set both `86bbch988` (edit-
format test plan) and `86bbch95y` (ACI parameter tuning) need. Not yet
executed — `nova_pull_exercism_corpus.py` exists and is verified via
`--dry-run`, but no real fetch has run and nothing has been vendored into the
repo yet.

## Why one corpus serves two tickets

Both `86bbch988` and `86bbch95y`'s remaining blocker need the same real
shape of data: many small, cheap, repeatable Python coding exercises with an
objective pass/fail check, run against Qwen2.5-Coder-7B. The only difference
is what gets varied and measured:

- **`86bbch988`** sweeps edit format (whole-file / search-replace / unified
  diff) and measures task correctness + format compliance.
- **`86bbch95y`** sweeps ACI parameters (view-window size, gate timing) and
  measures how those choices affect edit success on the same exercises.

Authoring two separate task sets for this would be real, avoidable
duplication — same reasoning already applied earlier this session when
scoping `86bbch988` itself (small Exercism-style exercises over Nova's own
6-task production dev set, since ablation-style tuning needs many cheap
trials, not a handful of expensive real tasks).

## Source: `exercism/python`, not the polyglot-benchmark repo

Checked both real candidates before choosing:

- **`Aider-AI/polyglot-benchmark`** — Aider's own "hardest 225 exercises
  across 6 languages" curation; its Python subset is only 34 exercises,
  already skewed toward difficulty (a *curated-hard* set, not a
  representative spread).
- **`exercism/python`** (chosen) — the canonical Exercism Python track
  itself, which is what Aider's own benchmark docs cite as the real source
  for its original 133-exercise benchmark ("the benchmark uses 133 practice
  exercises from the Exercism python repository"). 140 real exercises as of
  this check (the repo has grown slightly since Aider's docs cited 133 — an
  honest discrepancy, not a discarded number). **MIT-licensed**, confirmed
  directly via the GitHub API (`license.spdx_id: "MIT"`), safe to vendor a
  subset with attribution.

Chosen for full breadth and clean single-source provenance — a 30-exercise
stratified sample drawn from 140 gives real difficulty coverage; the same
sample size drawn from the polyglot repo's 34 would barely be a sample at
all.

## Selection methodology: difficulty-stratified, not arbitrary

`exercism/python`'s own track-level `config.json` publishes a real
1-9 difficulty rating per exercise (confirmed live — this is Exercism's own
published metadata, not inferred). Real distribution across all 140:

| Difficulty | Count | Sampled |
|---|---|---|
| 1 | 34 | 6 |
| 2 | 27 | 5 |
| 3 | 31 | 5 |
| 4 | 24 | 5 |
| 5 | 10 | 3 |
| 6 | 7 | 2 |
| 7 | 5 | 2 |
| 8 | 1 | 1 (all) |
| 9 | 1 | 1 (all) |

30 exercises total (`seed=2026`, reproducible — rerunning the sample
generator with the same seed against the same source data yields the
identical list). Weighted toward the common easy/medium tiers, while still
including *every* exercise at the two rarest tiers (8 and 9 have exactly one
real exercise each) rather than letting proportional sampling round them to
zero and silently losing the hardest real cases.

The real selected list (also embedded in `nova_pull_exercism_corpus.py`'s
`SELECTED_EXERCISES`):

```
1: bob, list-ops, raindrops, secret-handshake, space-age, two-fer
2: luhn, nth-prime, proverb, scrabble-score, yacht
3: binary, crypto-square, error-handling, octal, poker
4: all-your-base, complex-numbers, ledger, meetup, rail-fence-cipher
5: binary-search-tree, bowling, zebra-puzzle
6: affine-cipher, two-bucket
7: dominoes, sgf-parsing
8: rest-api
9: pov
```

## Exercise structure (confirmed live — corrected after the real fetch)

Each exercise directory contains, and this corpus vendors:

- `.docs/instructions.md` (+ `introduction.md`/`instructions.append.md` on
  some exercises) — the natural-language task description (what the model is
  given)
- `.meta/example.py` — the real reference solution — **never shown to the
  model**, used only for our own sanity-checking that a task is genuinely
  solvable
- `.meta/config.json` — exercise metadata (authors, file roles)
- `<slug>.py` — the stub file the model edits
- `<slug>_test.py` — the real objective check (unit tests define success,
  same "objective, runnable check" pattern `86bbch9ak`'s own task template
  wants to standardize on)

**Real correction found on the first actual fetch (2026-08-15):** newer
Exercism exercises also carry `.approaches/` (multiple worked *alternate*
solutions with full explanations) and `.articles/` (deep-dive content,
including a complete working benchmark solution) — neither anticipated when
this plan was first written. `raindrops` alone pulled 29 files instead of
the ~5-6 expected; 98 of 311 total files across the first real fetch (~31%)
were this unplanned content. This is a real leakage risk, not just bloat —
`.approaches/*/snippet.txt` and `.articles/*/code/*.py` contain working
solution code well beyond the single `.meta/example.py` this plan already
flagged as never-shown-to-the-model. Fixed in
`nova_pull_exercism_corpus.py` (`EXCLUDED_SUBDIR_PREFIXES`) before
committing the vendored corpus — the version actually in the repo is
`.docs`/`.meta` only, 241 files, 995K.

## Pinned for reproducibility

`EXERCISM_PYTHON_COMMIT = "1f6aab8667bf653b10cc3799f94352fcdb749db6"` — the
real HEAD of `exercism/python` as of 2026-08-10, fetched at a specific commit
rather than `main`, so this corpus stays byte-identical across every future
test run that references it, regardless of upstream edits.

## Where it lands

`data/coding_specialist_eval/exercism_subset/<slug>/...`, mirroring the
upstream layout exactly — no custom parsing needed by whatever harness reads
it later. A `NOTICE.md` is written into that directory at fetch time
recording the real source, pinned commit, and MIT license, so provenance
isn't left to a commit message alone.

**Recommendation: commit this to the repo, not gitignore it.** Unlike
`data/coding_training/synthetic/` (Nova's own generated output, expected to
churn), this is small (~30 exercises × ~5 small text files each), static,
third-party, and needs to be byte-identical for every future comparison run
— exactly the shape of a checked-in test fixture, not a regenerable
artifact.

## What's built vs. what's still a decision

**Built and verified:** `nova_pull_exercism_corpus.py` — real fetch logic
(GitHub Contents API + raw.githubusercontent.com), `--dry-run` confirmed
working, ruff-clean, writes the `NOTICE.md` attribution file automatically.

**Update, 2026-08-15 — the real fetch has since run.** 30 exercises are
vendored and committed (241 files, 995K). The corpus has already been used
for real: `nova_aci_harness.py` ran `bob` through Qwen2.5-Coder-7B end to
end. See the next section for what that run found.

## Real finding from first use: parsing leniency matters more than format choice

The first real exercise run against this corpus surfaced something worth
recording here, not just in the harness's own commit history — full writeup
in `docs/liberal-parsing-principle.md`. Short version: a strict-JSON-only
parser rejected the model's real edit attempts even when its underlying
Python logic was already correct, because it kept encoding that logic using
Python string-literal syntax instead of strict JSON. Adding graduated
leniency (`json.loads` → `ast.literal_eval` → a targeted repair for one
specific truncation pattern, each verified against real captured failures
before being trusted) took the same exercise from 0/26 tests passing across
a fully burned turn budget to 26/26 passing in 3 turns.

**Direct implication for this corpus's other intended use (`86bbch988`'s
edit-format comparison):** that plan currently proposes testing three
formats under strict parsing, matching Aider's own benchmark methodology.
Worth testing each format under the same graduated leniency before trusting
a ranking — a format that looks weak under strict parsing might perform
very differently once given the same tolerance `nova_coding_aci.edit()`
already has. Not yet done; flagged here so it isn't forgotten when that
comparison actually runs.
