# Nova Handoff

Read this first, on either side, before starting work. Rewrite the Current State
section in full when you close out a session — this is not a narration log, it's
current truth. The Recent Log below is a bounded history, not the source of truth.

Lives at `C:/Nova/NOVA_HANDOFF.md`, alongside `NOVA_STATUS.md` (board digest) and
`NOVA_BUILD_LOG.md` (narrative history) — same root-level-doc convention, different job:
those two are generated/append-focused, this one is a small, rewritten-in-place
sync point between a Chat planning session and a Code implementation session.

## Current State
*(last updated: Code · 2026-09-28)*

**Active thread:** Two small local-model coding-agent experiments, inspired by
a multi-agent-orchestration video (OpenRig) but grounded against what Nova
already has. **Experiment 1 is implemented and being ablation-tested now —
next session should read the ablation result and decide keep/revert before
starting Experiment 2.**

**Just built + first-tested (Experiment 1, `nova_aci_harness.py` +
`scripts/run_guard_ablation.py`):** Ported `GUARD_GOAL_REANCHOR` from
`nova_orchestrator_runpod.py` — new constants `GOAL_REANCHOR_INTERVAL_TURNS = 6`
/ `GUARD_GOAL_REANCHOR`, helper `_goal_reanchor_note()`, registered in
`ABLATABLE_GUARDS`, injected every 6 turns in the main tool-execution branch.
Smoke-tested clean on `bob` in both guard states. **Real ablation batch result
(n=60/condition, full corpus, $0):** baseline 3/60 pass / 8.47 avg turns /
26.7% max_turns vs. -goal_reanchor 1/60 / 8.55 / 28.3% — every axis favors
keeping the guard, and it's genuinely exercised (44/60 runs). **But n=60 is a
quarter of this file's own n≈240 trust bar** (the same bar that confirmed
`same_path_repeated_failure`/`_format_list_result()` net-negative) — direction
is real signal, not yet a settled verdict. Full detail + one loose thread
(an `abandoned_after_nudge`-while-passing `ledger` run) in
`project_goal_reanchor_ablation_result_1.md`. Left `goal_reanchor` default-ON,
not promoted or demoted. **Next session should decide: run a bigger
confirmatory batch (~repeat 6 more to reach n≈240 cumulative) before treating
this as a real win, or accept the tentative-positive signal and move to
Experiment 2 now, revisiting later.** **Not yet committed** — see git status
below.

**Still just scoped (not started) — Experiment 2, planner/executor split-brain:**
Test separating "which atomic action next" from "produce this action's exact
params" into two role-scoped Ollama calls inside `nova_aci_harness.py`'s turn
loop (~1140-1260), same model in both roles first (`qwen2.5-coder:7b` x2) to
isolate role-narrowing from model diversity. Add a `handoff_mismatch` counter
alongside the existing `guard_fires` dict so a negative result is diagnosable,
not just anecdotal. Eval on corpus slugs with a real nonzero baseline pass
rate (`raindrops`, `scrabble-score`, `two-fer`, `binary`, `ledger`), per
`project_squad_pilot_result_1.md`'s own recommendation — avoids the floor
effect that made the squad pilot's first run inconclusive.

**Also scoped, not started — Experiment 3, pinned context in `collapse_history()`
(2026-09-27):** `nova_coding_aci.collapse_history()` is recency-only (last
`HISTORY_KEEP_RECENT = 8` messages verbatim, everything older replaced by one
placeholder the model can't expand). Test keeping the last 8 plus pinned items —
latest successful `view` of each file being edited, last rejected edit + its
error — on the same slugs/ablation infra as #1. Inspired by AnyMAC's
Next-Context Selection. Open check first: can `GUARD_GOAL_REANCHOR`'s note get
collapsed away a few turns after injection? Detail in
`project_aci_context_selection_experiment_3.md`.

**Sequencing decision:** finish #1's ablation and decide keep/revert before
starting #2, so #2's baseline isn't confounded by a drift gap #1 might close.
#3 goes after both for the same reason.

**Paused mid-confirmation (2026-09-26):** the n=240 confirmatory batch got
killed by Claude Code's own low-memory safeguard mid-run (harness-level
protection, not a bug in the ablation code — see
`project_goal_reanchor_ablation_result_1.md` for the partial-run detail).
Investigated cause: Opera was using 4.3GB across 17 renderer + 4 utility
processes, but that's NOT necessarily "17 tabs" (Chromium spawns extra
renderer processes per cross-origin iframe, and Opera GX's sidebar mini-apps
run as persistent processes) — Marvin's own count was ~7 tabs, so the
process count didn't actually contradict that. Real diagnosis needs Opera's
own Task Manager (Shift+Esc), not WMI process-type inspection from outside.
**Session paused here at Marvin's request — those tabs had unfinished work
he needs to attend to before closing/restarting the browser.** Next session:
retry the n≈240 confirmatory batch once there's real headroom (check with
`Get-CimInstance Win32_OperatingSystem` free-RAM before starting, don't just
assume — this is the second time this session RAM looked fine at rest and
wasn't once Ollama actually loaded a model).

**Relevant prior art surfaced this session (both already in project memory,
just newly connected to this thread):** `nova_squad_pilot.py`
(`project_squad_pilot_result_1.md`) is the closest existing cousin to the
"fleet of small models" idea — not a role split, but retrieval-augmented
single-model context — and leaned negative on turn-efficiency (4.5x more
guard fires under retrieval). Direct warning for #2: injecting a second
model's output into a small model's context can act as noise, not help.

## Open questions for the other side
Numbered so they can be answered and removed, not left to linger in prose.

1. The Nova Tutor / Overworld ClickUp chain has pending SMT-naming comments that
   never got posted (rate limit) — see `nova_tutor_smt_naming_updates.md` if that
   file was ever handed to you separately. Worth folding into this handoff's log
   once posted, rather than tracking it in two places.

## Known drift
Design doc says X, implementation does Y (or vice versa) — stays here until
reconciled, either by updating the doc or fixing the code. Empty when clean.

- CLAUDE.md notes (Nova Tutor section) that the three newer "Overworld" ClickUp
  tasks (`86bbvcbby`/`86bbvcbcp`/`86bbvcbcv`) aren't yet reconciled in writing
  against the original 7-phase Tutor design doc on Drive. Not this file's job to
  fix, but flagging it here since it's a live cross-reference gap.

## Do-not-repeat
Mistakes already made and corrected, so neither side re-does them.

- Search the Nova Board before creating any task — 5 duplicate tutor tasks were
  created once before this rule existed.

---
## Recent log
Capped at ~10 entries. When it fills, summarize the oldest few into one line
and delete them — don't let this grow unbounded.

- **2026-09-20 · Chat:** Drafted this handoff file structure, confirmed `C:\Nova`
  as the real repo root (connected the folder mid-session), and placed the file
  at `C:\Nova\NOVA_HANDOFF.md`.
- **2026-09-20 · Chat:** Wired `NOVA_HANDOFF.md` into CLAUDE.md (file tree,
  Section 11 startup checklist read step, Section 11 end-of-session rewrite
  reminder) so it's part of the documented routine, not a side file.
- **2026-09-26 · Chat:** Scoped two ACI-harness experiments (goal-reanchor
  port, planner/executor split-brain) after a video-inspired discussion on
  multi-agent coordination failure modes; found both have close existing
  prior art (`nova_orchestrator_runpod.py`'s goal_reanchor/self_verify_nudge,
  `nova_squad_pilot.py`'s retrieval pilot) that reshaped the scope before
  anything was built. Session paused here — no code changes yet.
- **2026-09-26 · Chat (same session, resumed):** Built Experiment 1 (ported
  `GUARD_GOAL_REANCHOR` into `nova_aci_harness.py`, wired into
  `ABLATABLE_GUARDS`), smoke-tested both guard states on `bob`, kicked off
  the real ablation batch in the background. Awaiting result before deciding
  keep/revert and before starting Experiment 2.
- **2026-09-27 · Code:** Marvin checked in; no code changes. Researched two
  lecture-sourced papers (MAPLE, AnyMAC) — both saved as watch-item memories,
  neither actionable now. Read `collapse_history()`, scoped Experiment 3
  (pinned context) above. Experiment 1's confirmatory batch still pending
  RAM headroom.
- **2026-09-28 · Code:** Researched a third lecture-sourced paper (LTS,
  "Learning to Share") — saved as a watch-item memory, not actionable (needs
  parallel agent teams Nova lacks). Session closed; still no code changes.
