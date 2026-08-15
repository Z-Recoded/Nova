# Coding Specialist: Edit-Format Test Plan

Scoping pass for ClickUp `86bbch988`. This is a methodology document only — no
comparison run happens in this pass. Grounded against Aider's real, public
benchmark methodology (see Sources) rather than assumed from general leaderboards,
per the ticket's own instruction that format performance is model-size-dependent
and needs testing against Nova's actual candidate model.

## Objective

Determine which edit-output format — whole-file rewrite, search/replace blocks,
or unified diff — actually performs best for **Qwen2.5-Coder-7B** specifically
(Nova Training Pipeline Phase 0's settled base-model choice, confirmed in
CLAUDE.md's 2026-08-12 entry), rather than assuming a format choice transfers
from general-purpose leaderboards dominated by much larger models.

## Real prerequisite gap found while scoping

**Qwen2.5-Coder-7B is not pulled locally on the Aero yet** (`ollama list`
confirmed 2026-08-15: `qwen3:8b`, `llama3.1:8b`, `gemma3:4b`, `phi4-mini`,
`llama3.2` are present; no `qwen2.5-coder` variant). `ollama pull
qwen2.5-coder:7b` (or the exact tag Phase 0 settled on) is a real step before
any comparison run, not assumed available.

## Formats under test

Mapped to Aider's own real format names (confirmed via its benchmark README) so
this plan's terminology is unambiguous and directly comparable to published
numbers:

| This plan's name | Aider's name | What the model must emit |
|---|---|---|
| Whole-file rewrite | `whole` | The entire file content, every time, for every touched file. Simplest for the model, but token-expensive — Aider's own docs recommend starting here specifically "when working with an experimental LLM," i.e. exactly Qwen2.5-Coder-7B's situation. |
| Search/replace blocks | `diff` | A SEARCH block (text to match exactly) + REPLACE block, one pair per edit location. Real anchor-matching brittleness when SEARCH doesn't match exactly — but each edit stands alone, so one bad block doesn't invalidate the rest. **Directly comparable to Nova's own existing `file_replace()` tool** (CLAUDE.md Section 2: `old_str` must match exactly once) — this test is partly asking whether the mechanism Nova already prefers for the Claude-backed lane also holds up for a much smaller model. |
| Unified diff | `udiff` | Standard `--- +++ @@` hunks. Best fidelity when it works, but requires the model to track line numbers and hunk lengths correctly — a global-consistency constraint smaller models handle worse than large ones. |

(Aider also defines a fourth, `diff-fenced` — a markdown-fenced variant of
`diff` some model families parse more reliably. Worth including as a 4th arm if
early results show `diff` failing primarily on fence/formatting confusion
rather than genuine anchor mismatches — not included by default to keep the
first pass to the 3 formats the ticket actually named.)

## Methodology (borrowed from Aider's own benchmark)

1. **Exercise set:** small, scoped, Python-only exercises (matches the ACI
   task's own "Python only, for now" scope decision), each with a natural-
   language task description and a pre-existing unit test suite that defines
   success — the same Exercism-derived shape Aider's own benchmark uses.
   Reuse a subset of Aider's actual public benchmark exercises rather than
   authoring new ones from scratch (proven corpus, already unit-test-equipped,
   no authoring burden) — a sample size in the 20-30 range is enough to see a
   real spread between formats without the cost of the full 133-exercise set.
   **Contamination caveat:** these are a public, well-known benchmark — Qwen2.5-
   Coder-7B may have seen them in training. This test is measuring *format
   compliance and edit mechanics*, not held-out task-solving ability, so some
   contamination risk is more tolerable here than it would be for
   `nova_eval_held_out.py`'s pool — but it means an unusually high correctness
   number shouldn't be read as "this model solves novel problems this well."
2. **Per format:** run the identical exercise set through Qwen2.5-Coder-7B
   three times, once per format, with a format-specific system prompt
   specifying exactly how edits must be emitted (mirroring how each format is
   actually specified in Aider's own system prompts).
3. **Retry policy:** allow one retry on failure, feeding back the failing unit
   test output — matching Aider's own `--tries` mechanic and Nova's own
   orchestrator turn loop, which already supports multi-turn self-correction in
   production. Track first-try and after-retry results **separately**
   (Aider's own `pass_rate_1` / `pass_rate_2` pattern) so a format's raw
   reliability isn't hidden behind retry compensating for it.
4. **Execution environment:** this test only needs to run small, self-contained
   Python exercises against their own unit tests — it does **not** need the
   full ACI task's container/sandbox decision to be resolved first. A
   lightweight isolated Python execution step (e.g. a disposable venv or a
   minimal container, decided at implementation time) is sufficient. Flagging
   this because `86bbch95y`'s described blocker ("container setup needs to be
   chosen before build starts") does not actually block *this* task.

## Metrics (tracked separately, matching the ticket's explicit ask)

- **Task correctness rate** — % of exercises whose resulting code passes its
  unit test suite (first-try and after-retry, separately).
- **Format compliance rate** — % of responses that were successfully parsed
  and applied without a malformed-edit failure (mirrors Aider's
  `percent_cases_well_formed` / `num_malformed_responses`). This is the metric
  that actually isolates "can this small model reliably produce this format,"
  independent of whether the underlying fix was correct.
- **Token cost per exercise** — relevant since whole-file is named as
  "token-expensive" in the ticket, and Nova already tracks real per-task cost
  elsewhere (`nova_orchestrator_runpod.py`'s cost tracking, the Token Budget
  Governor) — this format decision has a real, comparable cost dimension, not
  just a correctness one.
- **Turns/latency to completion** (secondary) — only meaningful if retries are
  in scope, per point 3 above.

## Decision rule

Adopt whichever format has the highest first-try task correctness rate. If two
formats are within 5 percentage points of each other, use format compliance
rate and token cost as tie-breakers — favoring the cheaper format only when
correctness is genuinely tied, not as a default preference. This mirrors the
ticket's own framing of whole-file as "the safest default for an unproven
model" — the test should confirm or overturn that assumption with real
numbers, not assume it going in.

## Relationship to other pending work

- **`86bbch9ak`** (verifiable task template) explicitly assumes edits happen
  "only through the constrained action-space interface" — whichever format
  this test selects becomes a real input to that interface's design.
- **Nova Training Pipeline Phase 1/2** (`nova_bulk_distillation.py` and its
  planned grounded-refinement successor) will need to format training
  trajectories to match whatever edit format the serving model actually uses —
  this decision has real downstream effect on training data shape, not just
  runtime behavior.

## Explicitly not done in this pass

No comparison run happened. No exercises were selected or authored. No system
prompts were written. This document defines methodology only, per the ticket's
own "test plan" framing — running it is real API/compute cost and a separate,
future task.

---

## Sources

- [Code editing leaderboard | aider](https://aider.chat/docs/leaderboards/edit.html)
- [aider/benchmark/README.md at main · Aider-AI/aider](https://github.com/Aider-AI/aider/blob/main/benchmark/README.md)
- [GPT code editing benchmarks | aider](https://aider.chat/docs/benchmarks.html)
