# Third-Party Coding-Agent Harness Comparison — What's Worth Borrowing

Written 2026-08-17 for `86bbch95y` (coding specialist ACI design). Nova's own
`nova_coding_aci.py` was built from first principles and proven live (bob 26/26 after the
liberal-parsing fix, then the full 30-exercise corpus). Before continuing to harden it by hand,
this checks what three real, published third-party coding-agent harnesses already do, and
whether any of that generalizes to Nova rather than being re-derived from scratch turn by turn.

## SWE-agent — the actual origin of the term "ACI"

SWE-agent (Yang et al., "SWE-agent: Agent-Computer Interfaces Enable Automated Software
Engineering," NeurIPS 2024) is where "Agent-Computer Interface" comes from as a concept — Nova's
own module name is a direct descendant of this work, not a coincidence.

Confirmed real design points: the ACI's job is to shape actions, their documentation, and
environment feedback to complement an LM's specific limitations, "just like how typical
language models require good prompt engineering." Concretely, **SWE-agent's edit command runs
a linter and refuses the edit outright if the result isn't syntactically valid** — the exact
mechanism `nova_coding_aci.edit()` already independently converged on (`{"accepted": false,
"syntax_error": ...}` before a bad edit ever lands). That's a real, non-obvious point of
convergence, not a superficial naming overlap: Nova built the same core safety property SWE-agent's
own paper identifies as central to why the interface works, without having copied it. At the
time of publication this design reached 12.5% pass@1 on SWE-bench and 87.7% on HumanEvalFix —
state of the art for that generation of models.

**Update 2026-08-17, source read completed once GitHub connectivity was restored (see
`reference_aero_outbound_https_degradation` memory — the earlier fetch failures were a local
DHCP/IPv4 fault on the Aero, not GitHub or arXiv actually being unreachable):**

Confirmed directly from `docs/background/aci.md` and `swe-agent.com`'s reference docs:
- The linter-gated edit, windowed file viewer (100 lines/turn, scroll + in-file search), and a
  directory-search command that shows one match summary per file (not every line) are all real
  and match what was inferred from search summaries earlier.
- One concrete, cheap, directly-applicable idea found here: SWE-agent gives explicit natural-
  language feedback on an empty result — *"Your command ran successfully and did not produce any
  output"* — rather than a bare empty structure. This is a real, low-cost fix worth making to
  `nova_coding_aci.py`'s `find_file`/`search_file`/`search_dir` (which currently just return
  `json.dumps([])` on no matches) — directly relevant to the `affine-cipher` transcript in
  `docs/aci-failure-mechanism-analysis.md`, where the model read a bare empty search result as
  decisive proof a file didn't exist. Not yet implemented.

**The original recommendation — "go read SWE-agent's source, it's a battle-tested reference for
repeat-failure/no-progress handling" — turned out to rest on an assumption that didn't hold.**
SWE-agent's five documented history processors (`DefaultHistoryProcessor`,
`LastNObservations`, `TagToolCallObservations`, `CacheControlHistoryProcessor`, `RemoveRegex`)
are all generic context-truncation/tagging tools — none of them detect or specifically handle a
*repeated* action. The closest thing in the agent config is `max_requeries: int = 3`, a bounded
retry counter for re-querying the model after a parse/format/blocked-action error — a narrower,
different mechanism than Nova's `GUARD_REPEAT_FAILED_CALL` (which detects an exact repeat of a
call that already failed for a domain reason, like `bob`'s rejected edit, not a formatting
error). No max-step/no-progress/stuck-agent detection is documented in the public config surface
either. **Conclusion: there was nothing to borrow here — Nova's two guards
(`docs/aci-failure-mechanism-analysis.md`) are not behind this reference implementation on this
specific point, they cover ground SWE-agent's own public design doesn't.**

**A more significant discovery, found while looking for the above:** SWE-agent itself is now in
"maintenance-only mode." Its own team's actively maintained successor is
[`mini-swe-agent`](https://github.com/SWE-agent/mini-swe-agent) — a ~100-line agent, no
structured tool-calling interface at all, no windowed viewer, no named commands — the model just
runs raw bash directly, closer to OpenHands/CodeAct's unconstrained-action-space philosophy than
to the original SWE-agent's constrained ACI. It reportedly scores >74% on SWE-bench Verified,
well above original SWE-agent's 12.5% pass@1 (though this reflects newer, more capable backend
models too, not purely a controlled interface-only comparison). The team's own stated reasoning:
"as LMs have become more capable, a lot of the complexity from the original SWE-agent is not
needed at all."

**This complicates, without refuting, this doc's earlier OpenHands-comparison conclusion.** The
argument that "a constrained ACI suits Nova's small local-model tier because that's what
SWE-agent's own design principle argues for" is weaker than it looked before this read — the team
that made that original argument has since moved toward *less* structure, not more, as capability
grew. The real open question this doesn't resolve: is Qwen2.5-Coder-7B closer to the "still needs
guardrails" regime the original SWE-agent targeted, or capable enough that a minimal interface
would do better? That's genuinely unknown and is exactly what the proposed experiment below should
now test against — with `mini-swe-agent`, not the maintenance-only original, as the sharper
comparison target, since it represents the field's current actual bet on the opposite side of the
same question Nova is asking.

## Aider — real, controlled edit-format benchmark data Nova doesn't have to re-derive

Aider is a mature, widely-used CLI pair-programmer, not a benchmark-suite research harness — but
it publishes real, controlled comparisons between edit formats (whole-file rewrite,
search/replace blocks, unified diff, patch format) against the same models, which is exactly the
open question `86bbch988` ("edit-format test plan") already exists to answer for Nova's own
stack.

Confirmed real numbers: on GPT-4 Turbo, Aider's search/replace format scored 20% on their code-editing
benchmark; switching to a unified-diff format raised the same model to 61% and cut "lazy" (elided/
placeholder) completions roughly 3x. On June 2024's GPT-4, search/replace scored 26% vs. 59% for
unified diff. The pattern isn't universal — Aider's own docs note format performance is
model-dependent, and whole-file rewrite remains the easiest format for a model to use correctly
at the cost of far more tokens per edit, which matters more for a small local model's context
budget than for a frontier API model.

**Real implication:** `nova_coding_aci.edit()`'s current format (line-range replace: `start_line`,
`end_line`, `new_content`) sits closer to Aider's "search/replace" family than to unified diff —
the family Aider found *weaker* for lazy/error-prone models on their own benchmark. Given
Qwen2.5-Coder-7B's real observed failure modes this session (bob's stray-brace syntax error,
resent verbatim) look exactly like the kind of imprecise-edit-under-pressure behavior a diff-style
format is designed to reduce, this is a concrete, testable hypothesis for `86bbch988` rather than
a hunch: test Nova's real corpus against a unified-diff variant of the ACI's edit command before
assuming the current format is close to optimal for this model tier.

## OpenHands (CodeAct) — a genuinely different design axis, not a drop-in upgrade

OpenHands (formerly OpenDevin) uses the CodeAct approach: instead of a constrained, named set of
tools, the agent's action space **is arbitrary executable code** (Python, bash) run in a sandbox.
This is the opposite design philosophy from both SWE-agent's ACI and Nova's own
`nova_coding_aci.py` — unconstrained action space vs. a small, closed set of safe, structured
commands. Confirmed real number: OpenHands reports roughly 53% issue resolution on SWE-bench
Verified — clearly higher than SWE-agent's original 12.5%, though this comparison spans different
model backends and roughly a year of model-generation improvement, not just interface design, so
it is not evidence that free-code-execution beats a constrained ACI in isolation. That controlled
comparison doesn't appear to exist in what was found this session.

**Real implication for Nova specifically:** SWE-agent's own stated design principle — shape the
interface to complement the *model's* limitations — cuts against adopting CodeAct's unconstrained
action space for Nova's current model tier. A 7B local model is exactly the class the constrained-ACI
argument was built for; a bigger, more capable model is where free-form code execution has more
room to pay off. Nova already has a blocked ticket for this exact integration
(`86barex1u`, "Integrate OpenHands as Nova's coding lane sub-agent" — blocked on Docker/OpenHands
sandboxing being deferred, unrelated to this analysis). This comparison doesn't argue for reviving
that ticket now; it argues CodeAct is a better reference for a *future, larger-model* coding lane
than for hardening the current small-model ACI, and that `86bbch9cy` (AST-level action primitives)
sits on the same constrained-vs-unconstrained spectrum worth being explicit about before extending
Nova's action set further.

## Bottom line

Still not "stop building the custom ACI and adopt X instead" — but less clean-cut than it first
looked. Nova's edit-gate design independently matches the original SWE-agent's actual reference
implementation, and Nova's own repeat-failure/no-progress guards turned out to cover ground that
reference implementation's public design doesn't. Two concrete, low-cost actions came out of
this: (1) give `find_file`/`search_file`/`search_dir` explicit natural-language empty-result
feedback, matching SWE-agent's own pattern, directly motivated by the `affine-cipher` transcript
— not yet done; (2) fold Aider's real published diff-vs-search/replace numbers into `86bbch988`'s
edit-format test plan as a starting hypothesis. The complication: the field's own reference team
has since moved toward *less* structure (`mini-swe-agent`) as models got more capable, which
means "constrained ACI is right for Nova's small-model tier" is a real, testable hypothesis now,
not a settled conclusion this comparison can claim to have validated.

## Proposed experiment: SWE-agent head-to-head (scoped 2026-08-17, not started)

A third option beyond "read SWE-agent's source" and "borrow specific ideas": run the *same*
model (Qwen2.5-Coder-7B, local Ollama) through SWE-agent's actual ACI implementation instead of
Nova's own, against a comparable task set, and compare pass rates directly. This is a genuinely
different kind of evidence than a source read — it would separate "the model is just bad at this
task class" from "Nova's specific interface is leaving performance on the table," which no
amount of reading SWE-agent's code can answer on its own.

**Update 2026-08-17 — retarget to `mini-swe-agent`, not the original SWE-agent.** Now that the
source read is done (above), the sharper version of this experiment is Nova's constrained ACI
vs. `mini-swe-agent`'s raw-bash, no-structure approach — both real, current implementations
representing opposite bets on the same open question (does a small local model benefit more from
guardrails or freedom), rather than comparing against a project its own team has since moved on
from.

**What it would take:**
- `mini-swe-agent` is reportedly much lighter-weight than the original SWE-agent (~100 lines, no
  Docker-per-repo requirement implied by its own pitch) — plausibly cheaper to stand up than
  originally scoped, but not independently verified yet.
- **Open feasibility question, still unresolved:** both SWE-agent-family tools' native task shape
  is "GitHub issue + repo → patch," not "self-contained exercise + test file" like Nova's
  vendored Exercism corpus. Whether either can be pointed at the existing corpus via an adapter,
  or a genuinely comparable task set needs to be built/found instead, determines whether this is
  a days-scale or weeks-scale effort.
- Needs a real license check before any code from either repo is pulled in or run locally
  (believed permissive from general knowledge, not independently confirmed).
- Real compute cost is likely small (same local Qwen2.5-Coder-7B, same Aero GPU already proven to
  have headroom) — the cost is engineering time to stand up the harness, not GPU/API spend.

**Feasibility scoping done 2026-08-17 (GitHub reachable):** the previously-open questions are now
resolved, favorably:
- **License:** MIT — confirmed permissive.
- **Environment:** bash is the only hard requirement. Docker/Podman/Singularity/Bubblewrap are
  supported sandboxing options, not mandatory — a `LocalEnvironment` runs via direct
  `subprocess.run`, no Docker-per-repo setup needed. Lighter than originally assumed.
- **Task shape — the main open question, now answered:** confirmed via a real code sample from
  the README, not inference: `agent.run("Write a sudoku game")`. The agent accepts an arbitrary
  free-text task description, not a GitHub-issue-specific format. Adapting it to Nova's
  self-contained-exercise corpus looks plausible without a GitHub-issue wrapper.
- **Stop conditions:** step limits and cost limits are real, documented config options (comparable
  to Nova's `MAX_TURNS`). No repeat-loop/stuck-detection mechanism was found across the README,
  project overview, or config reference pages checked — consistent with, not contradicting, the
  earlier finding that this family generally lacks that mechanism.

**Revised verdict:** this now looks like a days-scale adaptation, not a weeks-scale build — the
two things that could have made it impractical (Docker requirement, GitHub-issue task lock-in)
both turned out not to be true. Still not started; this was feasibility scoping only, not a
green light to build without a separate decision to spend the time.

**Status:** feasibility scoped 2026-08-17, looks tractable, not yet started. Filed as `86bbfwbwc`
(ClickUp).

## Sources

- [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering (arXiv:2405.15793)](https://arxiv.org/abs/2405.15793)
- [SWE-agent/SWE-agent — docs/background/aci.md](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md) (directly read 2026-08-17 once GitHub connectivity was restored)
- [SWE-agent documentation — History Processor reference](https://swe-agent.com/latest/reference/history_processor_config/)
- [SWE-agent documentation — Agent Config reference](https://swe-agent.com/latest/reference/agent_config/)
- [SWE-agent/mini-swe-agent — the ~100-line successor, active maintenance](https://github.com/SWE-agent/mini-swe-agent/)
- [SWE-agent deep dive & build-your-own guide (DEV Community)](https://dev.to/truongpx396/swe-agent-deep-dive-build-your-own-guide-ade)
- [Aider — unified diffs make GPT-4 Turbo 3x less lazy](https://aider.chat/docs/unified-diffs.html)
- [Aider — edit formats](https://aider.chat/docs/more/edit-formats.html)
- [Aider — GPT code editing benchmarks](https://aider.chat/docs/benchmarks.html)
- [Aider — code editing leaderboard](https://aider.chat/docs/leaderboards/edit.html)
- [OpenHands — Main Agent and Capabilities docs](https://docs.openhands.dev/openhands/usage/agents)
- [Executable Code Actions Elicit Better LLM Agents (CodeAct paper)](https://arxiv.org/pdf/2402.01030)
- [OpenHands/OpenHands — codeact_agent README](https://github.com/OpenHands/OpenHands/blob/main/openhands/agenthub/codeact_agent/README.md)
- [What Are Coding Agents? A Developer's Guide to Agentic Coding (2026)](https://www.openhands.dev/blog/what-are-coding-agents)
