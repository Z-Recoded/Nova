# Eval Harness Initiative 2 — Per-Gate Verdicts

`86bbcfv9d`, "audit existing gates individually, not as a block." The single consolidated
output the charter asks for. Detail lives in `docs/aci-guard-cluster-ablation.md`,
`docs/aci-hybrid-verify-gate-audit.md`, `docs/aci-completion-gate-audit-scope.md`.

**Running caveat:** every dev-set number here comes from a corpus of 6–10 distinct tasks
(historical merges) or the 30-exercise ACI corpus at a ~7% pass ceiling. Verdicts are
directional. The held-out generalization pass (Group 2, below) is what turns the
completion-gate verdicts into real per-gate conclusions — **done 2026-09-02**, results folded
into the Group 2 table.

## Group 1 — ACI turn-loop guards (`nova_aci_harness.py`)

Method: individual ablation, full 30-exercise corpus, repeat=2 sweep (300 runs) + repeat=6
focused re-runs, $0 (Ollama only).

| Gate | Fire rate | Verdict | Action |
|---|---|---|---|
| `repeat_failed_call` | 76 would-fire / 300 runs (~40%) | **Workhorse** — only ablation that worsened *both* efficiency metrics when removed | Keep, no change |
| `done_without_edit` | 4 / 300 | Rare, but a **correctness safety net** (don't accept a `done` with zero work) — low fire rate is the healthy state | Keep, no change |
| `multiple_calls_ignored` | **0 / 300** | **Dormant** for Qwen2.5-Coder-7B (built from a `--progress-framing` transcript artifact this model doesn't hit) — near-zero cost, real for other models, contributes nothing here | Keep, documented as dormant |
| `same_path_repeated_failure` | 72 would-fire / 180 (~40%) | **Net-negative** on turn-efficiency, reproduced n=240/condition (−0.6 turns, −7 pts `max_turns`, no pass-rate cost). The 2026-08-17 "it helped" result was batch-to-batch noise | **DEMOTED to opt-in** (`--same-path-guard`, out of `ABLATABLE_GUARDS`) — commit `bfb2feb` |
| `_format_list_result()` empty-result feedback | ~always (all 3 search tools) | **Net-negative** too (−0.67 turns, −7.8 pts), quick-give-ups flat (the failure it was built for) — the *other* 2026-08-17 same-day change | Left as-is per Marvin; record corrected in `docs/aci-failure-mechanism-analysis.md` |
| `--hybrid-verify` style gate (split into GAMED / IDIOM) | 8–9 style calls / 60 runs | **Inconclusive** — 0 `IDIOM` verdicts across 120 runs, so the `octal` scenario never occurred; `GAMED` rejections didn't rise | `--advisory-idiom` stays opt-in; re-test needs a corpus that produces passing-but-unidiomatic solutions |

## Group 2 — production completion gate (`check_ground_truth_completion()`, `nova_completion_gate.py`)

Method: (a) $0 data audit over `ground_truth_gate_log.jsonl` (96 rows) + `coding_review_log.jsonl`
(109 rows), dev-set; (b) **held-out generalization pass, 2026-09-02** — 4 authored held-out
tasks (`hot-001`–`hot-004`) run agentically through the production **Claude** path
(`nova_eval_held_out_report.py` → `nova_orchestrator.run_via_claude`), then the gate run on
each diff with frozen requirements. 3 genuinely-good diffs + 1 genuine false-success (empty
diff). **Zero false hard-fails.** Full report: `logs/held_out_generalization_report_20260902_224722.md`.

| Check | Kind | Dev-set fired / sole | Held-out (OOD) result | Verdict | Action |
|---|---|---|---|---|---|
| `nonzero_diff` | hard | 15 / 15 | **FIRED correctly on `hot-004`** — a real held-out false-success (agent wrote tests for a signature it never built, then said "done"). First live OOD catch. | **Generalizes — OOD-confirmed.** 100% decisive | Keep, unconditionally |
| `cross_module_missing_export` | hard | 2 / 2 | No fire opportunity (no new cross-module name refs in the 4 diffs) | Dev-set verdict stands — 100% decisive when it fires | Keep |
| `lint_clean` | hard | 26 / 18 | **Quiet on 3 fresh non-trivial diffs** against ~current master — no false positive from pre-existing debt | **Open question CLOSED, 2026-09-04** — `_check_lint_clean` now diffs against `base_ref`'s own ruff output per touched file (multiset-subtracted by `(code, message)`) instead of assuming the whole repo is clean; verified live on 3 real throwaway repos (new violation on a file with pre-existing debt → only the new one flagged; edit that leaves pre-existing debt untouched → zero false reasons; brand-new file → still fully flagged) | Fixed |
| `module_level_name_order` | hard | 12 / 10 | Quiet — `hot-001` added module constants, correctly not flagged | Dev-set verdict stands | Keep |
| `required_files_touched` | hard | 8 / 5 | **Could not evaluate** — the one task that didn't touch its required file (`hot-004`) had an empty diff; `nonzero_diff` short-circuits first | Dev-set verdict stands | Keep |
| `narrow_scope_not_exceeded` | hard | 3 / 3 | **Quiet on 3 compliant OOD cases** (all 3 diffs stayed in their one narrow-scope file) | Correct OOD true-negatives; still no OOD "fires on violation" evidence | Keep |
| `cross_module_circular_import` | hard | 3 / **0** | No fire opportunity | Still unproven (n=3, 0 sole) — not killable | Keep, flagged unproven |
| `syntax_valid` | hard | **0 / 96** | Quiet — 3 valid-Python diffs, no broken syntax produced | Consistent; cheapest catastrophic-case catch | Keep |
| `powershell_syntax_valid` | hard | **0 / 96** | Quiet — no `.ps1` touched | Consistent | Keep |
| `forbidden_paths_untouched` | hard | **0 / 96** | **Quiet correctly on `hot-001`** — `nova_omen_dispatch.py` was the frozen forbidden path and Claude respected it (first real forbidden-path test in either corpus) | First OOD compliance data point — correct | Keep |
| `deliverables_present` | warn | 40 | **FALSE POSITIVE on `hot-002`** — deliverable named `send_notification()`, function *was* modified, but Claude reflowed the signature multi-line so the literal `send_notification()` never appears in an added line. Naive substring match. | **Does not generalize to `()`-suffixed deliverables.** The one gate-code defect the pass found | **FIXED 2026-09-02** — `_check_deliverables_present()` now strips a trailing `()` and matches the bare identifier; re-verified on `hot-002` (0 warnings) + 4 regression cases |
| `unused_new_import` | warn | 20 | **Quiet correctly on `hot-003`** — `import json` added and used | Correct OOD true-negative | Keep |
| `unexpected_deletion` | warn | **0 / 96** | Quiet — no deletions | Consistent | Keep |

**Group 2 conclusion:** the completion gate generalizes. Headline hard-fail (`nonzero_diff`)
confirmed decisive on a genuinely OOD task; no hard-fail false-positived on 3 good OOD diffs;
`lint_clean`'s pre-existing-debt risk didn't materialise on those 3 diffs (n=3), and was then
closed structurally on 2026-09-04 (base-ref violation diffing) rather than left as an
accumulate-more-evidence open question. Two real defects found and fixed:
`deliverables_present`'s substring match, and `lint_clean`'s lack of a base-ref comparison.

## Group 3 — A1-G2 turn-loop guards (RunPod / Devstral orchestrator lanes)

The 19-entry failure registry (`reference_coding_agent_failure_registry`), guards in
`nova_orchestrator_runpod.py` / `_devstral.py`.

**DEFERRED — formally scoped out of Initiative 2 (Marvin, 2026-08-31).** Ablating these needs
agentic runs against the RunPod/Devstral dense-model backends = real GPU $, and those lanes
were deprioritized in the 2026-08-12 pivot (dense-model debugging → MoE-tier + training
pipeline). Revisit only if the dense-model lane comes back. Some hand-analysis already exists
in the failure registry and `docs/aci-failure-mechanism-analysis.md`.

## What's left before `86bbcfv9d` closes

1. ~~**[HARD] Held-out generalization pass on the completion gate.**~~ **DONE 2026-09-02.**
   4 held-out tasks run through the production Claude path (`nova_eval_held_out_report.py`),
   gate run on each diff, per-check OOD verdicts folded into Group 2 above. Actual cost
   ~$1–2 (the $10–40 estimate was conservative — the 4 tasks are single-file changes).
   `nonzero_diff` OOD-confirmed; `lint_clean` open question downgraded; `deliverables_present`
   false-positive found and fixed.
2. ~~A1-G2 guard audit~~ — deferred, see Group 3.
3. This document — the consolidation. ✅

**All three items closed → `86bbcfv9d` complete.** New reusable infra from this pass:
`nova_orchestrator.run_via_claude()` (extracted from `run_coding_task()`'s inline loop) and
`nova_eval_held_out_report.py` (held-out runner, writes no shared training/telemetry log).
