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

**Real implication:** rather than treating today's two new ACI guards
(`docs/aci-failure-mechanism-analysis.md`) as Nova-original discoveries, worth a direct source
read of SWE-agent's actual repository (`SWE-agent/SWE-agent` on GitHub) for how *it* handles
repeat-failure and no-progress cases — a battle-tested reference implementation of the same
interface concept is a faster way to find a good guard design than re-deriving one from three
transcripts each time. (Attempted to pull the actual `docs/background/aci.md` source directly
this session; both GitHub and arXiv fetches failed from this environment — worth a follow-up
attempt from a session where outbound fetches are working, since the primary source would sharpen
this section considerably.)

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

Nothing here says "stop building the custom ACI and adopt X instead." The comparison instead
validates the current direction (Nova's edit-gate design independently matches the field's actual
reference implementation) while surfacing two concrete, low-cost next steps that don't require
re-deriving anything from scratch: (1) read SWE-agent's real source for its own repeat-failure/
no-progress handling before iterating further on Nova's guards by hand, and (2) fold Aider's
real published diff-vs-search/replace numbers into `86bbch988`'s edit-format test plan as a
starting hypothesis, not a from-scratch benchmark design.

## Proposed experiment: SWE-agent head-to-head (scoped 2026-08-17, not started)

A third option beyond "read SWE-agent's source" and "borrow specific ideas": run the *same*
model (Qwen2.5-Coder-7B, local Ollama) through SWE-agent's actual ACI implementation instead of
Nova's own, against a comparable task set, and compare pass rates directly. This is a genuinely
different kind of evidence than a source read — it would separate "the model is just bad at this
task class" from "Nova's specific interface is leaving performance on the table," which no
amount of reading SWE-agent's code can answer on its own.

**What it would take:**
- SWE-agent expects its own environment per task (it was built around Docker-per-repo for real
  GitHub issues) — real setup cost, not a drop-in library call. Needs to be scoped, not assumed
  cheap.
- **Open feasibility question, not yet answered:** SWE-agent's native task shape is "GitHub
  issue + repo → patch," not "self-contained exercise + test file" like Nova's vendored
  Exercism corpus. Whether SWE-agent can be pointed at the existing corpus with a reasonable
  adapter, or whether a genuinely comparable task set would need to be built/found instead, is
  unresolved — this determines whether the experiment is a days-scale adaptation or a
  weeks-scale one.
- Needs a real license check before any code from the repo is pulled in or run locally
  (believed permissive from general knowledge, not confirmed this session — GitHub was
  unreachable throughout).
- Real compute cost is likely small (same local Qwen2.5-Coder-7B, same Aero GPU already proven
  to have headroom for this class of run) — the cost is engineering time to stand up the harness,
  not GPU/API spend.

**Status:** scoped, not started. Filed as a ClickUp task (see comment on `86bbch95y`) rather than
picked up immediately — the repeat-failure source read (blocked on GitHub connectivity, see
above) is the cheaper, more targeted next step and should happen first; this experiment is a
larger, separate decision.

## Sources

- [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering (arXiv:2405.15793)](https://arxiv.org/abs/2405.15793)
- [SWE-agent/SWE-agent — docs/background/aci.md](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md) (fetch attempted, failed this session — content above is from search-result summaries of this page, not a direct read)
- [SWE-agent deep dive & build-your-own guide (DEV Community)](https://dev.to/truongpx396/swe-agent-deep-dive-build-your-own-guide-ade)
- [Aider — unified diffs make GPT-4 Turbo 3x less lazy](https://aider.chat/docs/unified-diffs.html)
- [Aider — edit formats](https://aider.chat/docs/more/edit-formats.html)
- [Aider — GPT code editing benchmarks](https://aider.chat/docs/benchmarks.html)
- [Aider — code editing leaderboard](https://aider.chat/docs/leaderboards/edit.html)
- [OpenHands — Main Agent and Capabilities docs](https://docs.openhands.dev/openhands/usage/agents)
- [Executable Code Actions Elicit Better LLM Agents (CodeAct paper)](https://arxiv.org/pdf/2402.01030)
- [OpenHands/OpenHands — codeact_agent README](https://github.com/OpenHands/OpenHands/blob/main/openhands/agenthub/codeact_agent/README.md)
- [What Are Coding Agents? A Developer's Guide to Agentic Coding (2026)](https://www.openhands.dev/blog/what-are-coding-agents)
