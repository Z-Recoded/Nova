# Nova Handoff

Read this first, on either side, before starting work. Rewrite the Current State
section in full when you close out a session — this is not a narration log, it's
current truth. The Recent Log below is a bounded history, not the source of truth.

Lives at `C:/Nova/NOVA_HANDOFF.md`, alongside `NOVA_STATUS.md` (board digest) and
`NOVA_BUILD_LOG.md` (narrative history) — same root-level-doc convention, different job:
those two are generated/append-focused, this one is a small, rewritten-in-place
sync point between a Chat planning session and a Code implementation session.

## Current State
*(last updated: Code · 2026-10-18)*

**Trade-proposal approval channel for the Kalshi agent — LIVE (2026-10-18, `86bcm93r8`).**
`nova_trade_proposals.py` (an `APIRouter` included by `nova_api.py`), flag
`trade_proposals.enabled` is ON on the Omen. Commits `0dd358d`, `5b228ae`, `092464f`,
`9c44e10`, `33b5085`, all pushed to `forgejo` + `origin` and synced to the Omen. Two tokens on
purpose: `NOVA_TRADE_TOKEN` (kta's; register / read own / timeout) vs. the Controller's
`NOVA_ESCALATION_TOKEN` (decide / list). Marvin generated the trade token himself and copied it
into the Omen's Nova `.env` and the `kalshi-agent` `.env` (`/home/kalshi-agent/kalshi/.env`,
`TRADE_PROPOSAL_TOKEN`); it never appeared in a transcript. Contract, shapes, and statuses
(`pending`/`approved`/`denied`/`timed_out`) are in CLAUDE.md "Trade Proposals" and were sent to
kalshi-agent by message. `scripts/verify_trade_proposals.py` is the regression check — run it after
any change to that module.

**Live test status: PASSED (Marvin, 2026-10-18).** kta built a separate no-order test mode (`python -m kta.propose_test_trade
--no-order --ttl-seconds 300`, never reaches an order call). Two earlier runs registered fine but
Marvin saw no card; cause was the Controller Feed (below), not the data. After the fix Marvin
confirmed the cards show on both devices, and **reported the live test succeeded** (Approve path
end to end with kta). Which extra variants (Deny, no-tap timeout) were also run was not specified.
The channel is done; remaining work is on the kta side (real orders stay gated by its own flow).

**Controller Feed rebuilt (same day).** Root cause of "no cards on the phone": a six-route
`Promise.all` plus a discard-if-superseded guard starved any client whose slowest route exceeded the
8s poll. Now each source loads/draws independently, pending cards first, resolved ones behind "Show
resolved", the 139KB tier history only on demand (`GET /tier-proposals?pending_only=true`). Also fixed:
trade-card countdown was off by hours (naive server timestamps vs. browser timezone) — the list view now
sends `expires_in_seconds`. A grey diagnostic line under "Feed" (loaded/drawn/trade count/last JS
error) was added and should stay.

**Open items from this work (none blocking):**
- `nova_omen_sync.py` crashed once with `TimeoutExpired` (30s limit; `nova-chroma` took ~37s to stop).
  Services recovered by themselves; later syncs were clean. Still wants a longer timeout + retry in its
  verify/restart step (same family as the 2026-10-04 startup race).
- Unauthenticated callers can learn the trade route's field names (FastAPI validates the body before the
  token check). Minor; fix only if Marvin wants it.
- Omen downtime window (~2:45AM-12:05PM ET) means no approvals then; kta's Stage-1 weather class will
  trade overnight. Needs a plan before that class ships (kta side knows).
- On the Omen the state DB is at `~/nova/C:/Nova/nova_state.db` (hardcoded Windows `DB_PATH`) —
  pre-existing, works.
- The classifier (auto mode) blocked some edits/commits mid-session; Marvin added a permission rule for
  git add/commit/push and `Edit(nova_config.json)`. Don't assume it is global.

**Uncommitted at wrap-up:** none of this session's docs (committed + pushed at Marvin's go-ahead,
along with `docs/tutor-learning-science-evidence.md`, 91 lines added before this session and never
reviewed here). Marvin's `screenshots/` changes were left alone.
`graphify-out/` also churned from `graphify update` — leave it out of commits unless Marvin wants it.

**Earlier state, still true (unchanged by this session):** the 2026-10-04 `multi_character_retrieval`
flag (default off) was never reviewed or tested with the flag on; Experiments 2 (planner/executor split)
and 3 (pinned `collapse_history()`) are scoped, not started; the Omen's nova-infra downtime window is live.

**Next pick-up (Marvin's call):** (a) harden `nova_omen_sync.py`, (b) review/test
`multi_character_retrieval`, (c) Experiment 2, (d) a plan for the overnight Omen downtime window
before kta's weather class ships.

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
- **2026-09-28/29 · Code (new session):** Committed+pushed+synced Experiment 1
  and the earlier Handoff/Tutor-docs work. Found+fixed a real 23-day-silent cron
  failure (PowerShell native-stderr-abort bug, both scheduled wrappers).
  Researched Dynamic Mixed-Precision Routing and SafeDream plus several concept
  questions — saved/answered, none actionable. Confirmatory ablation survived a
  RAM-pressure stall and completed at n=240 cumulative. **Experiment 1 decided:
  keep `goal_reanchor` default-ON** — Experiment 2 unblocked.
- **2026-10-04 · Code:** No code changes. Confirmed golden benchmark makes no
  Anthropic calls (local Ollama only); ran it 4x, all clean, blend rate 0.0%
  each time. Traced old 0.333 blend rate to the generic "tell me a story" query
  (likely fixed by `928e4d9`; one post-fix 08-08 run unexplained). Uncommitted
  `multi_character_retrieval` work committed+pushed (`e418ca6`) on Marvin's
  go-ahead, untested with the flag on; Omen synced (verify falsely failed on a
  nova-api/Chroma restart race, recovered on its own). Then evaluated Jev-style
  decision models: confirmed Jev is real (corrected a stale "satire" memory), ran a
  free local GLiNER2.5-Decide test vs. the Claude blend judge — negative/inconclusive,
  scratch files deleted, memory updated. Paused for the day.
- **2026-10-07 · Code:** Brainstorm only. Researched byte-level LLMs and LLM
  decompilation; counted ACI parse failures from logs; parked a constrained-output
  idea. Two memories added, no repo code touched.
- **2026-10-18 · Code:** Built, deployed, and live-verified the Kalshi trade-proposal approval channel
  (`nova_trade_proposals.py`, flag on) via a cross-session scoping exchange with `kalshi-agent`; fixed a
  timezone countdown bug and rebuilt the Controller Feed's loading after a phone showed no cards (starved
  by an all-or-nothing load). Docs (CLAUDE.md, build log) updated; Marvin reported the live no-order test
  with kta succeeded.
