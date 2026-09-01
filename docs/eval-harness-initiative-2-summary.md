# Eval Harness Initiative 2 — Per-Gate Verdicts

`86bbcfv9d`, "audit existing gates individually, not as a block." The single consolidated
output the charter asks for. Detail lives in `docs/aci-guard-cluster-ablation.md`,
`docs/aci-hybrid-verify-gate-audit.md`, `docs/aci-completion-gate-audit-scope.md`.

**Running caveat:** every dev-set number here comes from a corpus of 6–10 distinct tasks
(historical merges) or the 30-exercise ACI corpus at a ~7% pass ceiling. Verdicts are
directional. The held-out generalization pass (below) is what turns them into real per-gate
conclusions.

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

Method: $0 data audit over `ground_truth_gate_log.jsonl` (96 rows) + `coding_review_log.jsonl`
(109 rows). Dev-set half only — held-out half is gate #1 below.

| Check | Kind | Fired / sole-reason | Verdict | Action |
|---|---|---|---|---|
| `nonzero_diff` | hard | 15 / 15 | **100% decisive** — empty diff is unambiguously not-done | Keep |
| `cross_module_missing_export` | hard | 2 / 2 | **100% decisive** — imports a non-existent name → real `ImportError` | Keep |
| `lint_clean` | hard | 26 / 18 | **Dominant** check + a plausible pre-existing-lint-debt precision risk (lints the whole file, assumes master is always ruff-clean). Coarse task-text join can't confirm | Keep; **open question** — held-out pass resolves it |
| `module_level_name_order` | hard | 12 / 10 | Fires mostly on genuinely-bad attempts (8/12 all-rejected) | Keep |
| `required_files_touched` | hard | 8 / 5 | Catches incomplete attempts | Keep |
| `narrow_scope_not_exceeded` | hard | 3 / 3 | Small n, directionally fine | Keep |
| `cross_module_circular_import` | hard | 3 / **0** | `86bb77vk6`'s check — **never independently decisive** here (always co-fired). n=3 — unproven, not killable | Keep, flagged unproven |
| `syntax_valid` | hard | **0 / 96** | Never fired — model's Python parses. Cheapest possible check, catastrophic-case catch | Keep |
| `powershell_syntax_valid` | hard | **0 / 96** | Never fired — only 1–2 tasks had `.ps1` | Keep |
| `forbidden_paths_untouched` | hard | **0 / 96** | Never fired — model never tried a forbidden path. Safety check, low fire rate healthy | Keep |
| `deliverables_present` | warn | 40 | Dominant warning | Keep |
| `unused_new_import` | warn | 20 | — | Keep |
| `unexpected_deletion` | warn | **0 / 96** | Never fired | Keep |

## Group 3 — A1-G2 turn-loop guards (RunPod / Devstral orchestrator lanes)

The 19-entry failure registry (`reference_coding_agent_failure_registry`), guards in
`nova_orchestrator_runpod.py` / `_devstral.py`.

**DEFERRED — formally scoped out of Initiative 2 (Marvin, 2026-08-31).** Ablating these needs
agentic runs against the RunPod/Devstral dense-model backends = real GPU $, and those lanes
were deprioritized in the 2026-08-12 pivot (dense-model debugging → MoE-tier + training
pipeline). Revisit only if the dense-model lane comes back. Some hand-analysis already exists
in the failure registry and `docs/aci-failure-mechanism-analysis.md`.

## What's left before `86bbcfv9d` closes

1. **[HARD] Held-out generalization pass on the completion gate.** For each of the 4 authored
   held-out tasks (`nova_eval_held_out.AUTHORED_HELD_OUT_TASKS`): run a candidate model
   agentically to produce a real diff (**cost:** Anthropic Console ~$10–40 for the Claude
   candidate across all 4, or RunPod GPU $ for a remote model — the held-out tasks have no
   pre-existing diff to reuse). Then run `check_ground_truth_completion()` on each ($0 — the
   `requirements` are frozen) and record per-check "generalizes / doesn't / hurts OOD
   precision" vs. the dev-set behaviour above. Directly resolves the `lint_clean` question
   (fresh tasks on current master → any `lint_clean` fire is diff-caused by construction).
2. ~~A1-G2 guard audit~~ — deferred, see Group 3.
3. This document — the consolidation. ✅
