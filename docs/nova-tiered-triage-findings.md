# Nova Confirm/Deny Gate Audit — Findings

Discovery pass against the brief in `nova-tiered-triage-exploration.md`. No code changed in
this pass — flagging real gate candidates found by reading the actual call sites, not guessing
from docstrings.

## Summary

Nova already has three genuinely well-built Confirm/Deny Gates in production, plus one site
(`_review_coding_diff()`) where an apparent gap turned out to be a deliberate existing design
decision, not a real gap. Status as of 2026-08-14, after building Gaps A and evaluating B/C/D:

| Site | Already gated? | Status |
|---|---|---|
| Query routing (`nova_router.py`) | ✅ Yes — pure keyword match, zero model calls | No further work needed |
| Character-file retrieval scoping (`nova_query.py`) | ✅ Yes — hard `$eq` filter + fallback | No further work needed |
| Blend detection (`nova_logger.py`) | ✅ Yes — free filename-count heuristic | No further work needed |
| Ground-truth completion checks (`nova_completion_gate.py`) | ✅ Yes — 10+ static/AST checks, all free | No further work needed |
| `nova_corrector.py` correction loop | ❌ No | **Gap A — BUILT** (exact-match + embedding-similarity reuse cache) |
| `nova_task_queue.propose_tier()` | ❌ No | **Gap B — evaluated, not built** (real savings too marginal, safety asymmetry — see below) |
| `_review_coding_diff()` review call | Looked ungated, isn't really | **Gap C — not pursued** (conflicts with a deliberate independent-cross-check design decision) |
| `extract_task_requirements()` | N/A (not a judgment call) | **Gap D — BUILT** (SHA-256 task-text cache, verified live) |

---

## Already gated — no action needed

**RAG retrieval path (`nova_query.py` / `nova_router.py`):** `route()` is 100% keyword-table
matching with zero model calls before retrieval even starts — this is already the ideal
Confirm/Deny shape (cheap check resolves category, model only sees the final prompt). Fiction
queries additionally get a hard `$eq` character-file filter (word-boundary regex match) that
scopes Chroma before any generation happens, with a clean fallback to unfiltered search. There is
no "should we call the model" decision point here to gate — the local Ollama call always fires
once routing/retrieval finishes, so there's no expensive-call-avoidance opportunity beyond what's
already built.

**Blend detection (`nova_logger.py`):** `detect_blending()` is a free filename-count check
(`len(filenames) > 1`, only when `character_filtered=True`) sitting directly ahead of
`nova_corrector.py`'s API calls. This is the pattern working exactly as intended.

**Ground-truth completion (`nova_completion_gate.py`):** this file is almost entirely static
checks — nonzero-diff, required/forbidden-files-touched, Python syntax (`ast.parse`), PowerShell
syntax (`PSParser.Tokenize`), ruff lint, module-level name-order (NameError detection), circular
imports, missing cross-module exports, narrow-scope change-ratio — all free, all running at
`nova_orchestrator.py:736`, before `_review_coding_diff()`'s Claude call at line 745. The module's
own header cites real research (LLM judges reading a trajectory/diff: AUROC ≤ 0.65) as the reason
these checks exist instead of trusting a model judgment. This is the single most mature gate in
the codebase.

---

## Gap A — `nova_corrector.py`'s correction loop has no similarity gate

**Current behavior:** `run()` only skips two cases before calling Claude — already-corrected
entries and exact-string matches against `GOLDEN_QUERY_STRINGS` (`_is_golden_duplicate()`).
Every other flagged blend entry gets a full `request_correction()` API call, unconditionally.

**Evidence this matters (verified directly against `logs/training_flags.jsonl`, 26 entries, all
currently corrected):** real recurrence exists in this exact file. `sources_mixed` shapes repeat —
`('Null.md', 'Nullius.md')` 9x, `('Helel.md', 'Luci.md')` 4x, `('Fatale Wildman.md', 'Femme
Wildman.md')` 3x — and so do near-identical queries: "who is null?" 6x, "who is fatale wildman?"
3x, "tell me about helel." 3x. (Note: an earlier draft of this doc cited a "6 tasks / 36 pairs"
figure here — that was actually `nova_coding_corrector.py`'s data, a different corrector for
coding-diff review, not this one. Corrected.)

**Proposed gate:** before calling `request_correction()`, check the new entry's
`(category, sources_mixed)` pair — or an embedding-similarity check against prior queries with an
existing `correction` — against already-corrected entries. On a high-confidence match, reuse the
prior correction (or a lightly re-templated version) instead of a fresh API call.

**Rough estimate:** moderate-to-high. The exact-query repeats (6x "who is null?") are a clean win
with zero risk — a query-text-normalized exact-match cache resolves those for free. The
same-source-pair-but-different-query repeats (9x Null/Nullius, different questions each time) are
riskier to auto-answer from a cached correction, since the correction text is grounded in the
specific query, not just the character pair — those likely still need a fresh call, or at most a
cheap similarity-gated reuse with a conservative threshold.

---

## Gap B — `nova_task_queue.propose_tier()` has no pre-filter — evaluated, not built

**Current behavior:** every task with no tier watermark yet (`detect_tier_candidates()`) goes
straight to `propose_tier()`'s Claude call. There is no heuristic short-circuit anywhere in this
path — confirmed by reading `propose_tier()` and its callers directly.

**Original proposed gate:** a keyword/pattern heuristic in the same style as `nova_router.py`
resolving the obvious ends of the distribution — clear `manual only` language (credentials,
force-push, delete, production data) or clear `autonomous` language — escalating only the
ambiguous middle to Claude.

**Why this wasn't built, on closer evaluation (2026-08-14):**
- **Real cost is already small.** `propose_tier()` is `claude-sonnet-4-6` at `max_tokens=300` —
  one of the cheapest calls in the codebase — and call volume is low by design (only genuinely
  new/rescoped tasks, polled every 2 hours; the ~100-task backlog sweep already ran once). Nowhere
  near `nova_corrector.py`'s real repeat-question pattern that justified Gap A.
- **Safety asymmetry rules out half the gate.** `propose_tier()` already fails toward `"manual
  only"` on any parse failure — the system is built to never look safer than it is. A heuristic
  could defensibly short-circuit toward `"manual only"` only, never toward `"autonomous"` (that
  direction risks misclassifying a real judgment call as safe to run unattended — the exact harm
  this tiering system exists to prevent). That halves the gate's value versus the original framing.
- **Decision, confirmed with Marvin:** not worth building given marginal savings against real
  misclassification risk. Left as documented, evaluated, and deliberately not implemented.

---

## Gap C — NOT PURSUED: conflicts with an existing deliberate design decision

**Original framing (superseded, kept for the record):** `check_ground_truth_completion()` runs
and is logged (`nova_orchestrator.py:736`) **before** `_review_coding_diff()`'s Claude call
(line 745) — but the review call fires unconditionally whenever `coding_review_pass` +
`runpod_coding_agent` are both on, regardless of what the gate found. The original idea was to
skip or downgrade the review call when the gate already hard-fails.

**Why this was walked back (2026-08-14):** `_log_ground_truth_gate()`'s own docstring
(`nova_orchestrator.py:896-904`) states the gate log is kept **separate** from
`coding_review_log.jsonl` *"since this is a mechanical check result, not a judged verdict, and
gives a training/monitoring signal that's independent of (and a useful cross-check against)
Claude's own review pass."* That is a deliberate, already-documented design choice — the gate and
the LLM review are meant to stay independent so each can validate the other. Skipping the review
call on gate failure (or worse, synthesizing a review-log entry from the gate's own hard-fail
reasons) would collapse that intentional independence, not just cost a Claude call.

**Decision (confirmed with Marvin):** leave `_review_coding_diff()` running unconditionally. This
isn't a Confirm/Deny Gate candidate the way A/D are — it's a site where the existing architecture
already made a considered tradeoff in the other direction, and the "waste" is intentional. Not
building anything here.

---

## Gap D — BUILT: `extract_task_requirements()` now caches on disk

Not a Confirm/Deny Gate in the strict sense (it's not judging correctness), but the same
avoidable-cost shape: `nova_coding_eval.py`'s three backend runners
(`run_runpod_backend`/`run_devstral_backend`/`run_qwen3_backend`, `nova_coding_eval.py:243/303/355`)
each call `extract_task_requirements()` fresh for the same fixed dev-set task text, repeated across
every eval session — plus `check_ground_truth_completion()`'s own fallback re-extraction
(`nova_completion_gate.py:1444-1445`) when a caller doesn't pass `requirements` in, already flagged
in that function's own docstring as "a real, avoidable duplicate cost."

**Built:** a SHA-256-of-exact-task-text cache (`TASK_REQUIREMENTS_CACHE_PATH`,
`logs/task_requirements_cache.json`, gitignored, regenerable) inside `extract_task_requirements()`
itself, so every caller benefits automatically. Only genuinely successful, parsed extractions are
cached — the fail-open `empty_result` (missing API key, parse/API failure) is never cached, so a
transient failure can't permanently poison the cache for a task that would extract cleanly on a
real retry.

**Verified live:** a real first call (`TEST-CACHE-VERIFICATION-2026-08-14`, a synthetic task
naming `nova_api.py` as required and `nova_tools.py`/`nova_orchestrator.py` as forbidden) extracted
correctly and got cached. A second call with the exact same text was forced through with
`anthropic.Anthropic` mocked to raise if constructed — it never fired, and the returned data was
byte-identical to the first call, confirming a genuine zero-API-call cache hit, not just "the
function ran quickly." Test entry removed from the cache afterward.

---

## Session summary (2026-08-14)

- **Gap A** — built and verified (`nova_corrector.py` exact-match + embedding-similarity reuse cache).
- **Gap B** — evaluated, deliberately not built (marginal real savings against a real
  autonomy-misclassification risk).
- **Gap C** — not pursued; turned out to conflict with an already-deliberate independent-cross-check
  design decision in `nova_orchestrator.py`/`nova_completion_gate.py`.
- **Gap D** — built and verified (`nova_completion_gate.py` task-requirements cache).

Two real corrections were made to this doc along the way: Gap A's original "evidence" cited the
wrong corrector's data (fixed against real `training_flags.jsonl` numbers), and Gap C's original
framing missed an explicit design-intent comment already in the code (fixed after re-reading the
actual docstring). Both are left in the text above rather than silently rewritten, since the
correction itself is part of the record.
