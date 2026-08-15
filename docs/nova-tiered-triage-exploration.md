# Nova Architecture Exploration: Tiered Confirm/Deny Gates

## The pattern (name it, reuse it)

**Confirm/Deny Gate**: a small, cheaply-run check — heuristic, embedding similarity, or a tiny fine-tuned classifier — sits in front of an expensive model call. It resolves the obvious cases outright and escalates only the ambiguous ones. The large model never sees traffic the gate could already resolve.

Three questions define a candidate site for this pattern in any project:

1. Does this step call a model (or a person) when a cheaper check could resolve most instances?
2. Is there a cheap, learnable signal that predicts the expensive call's outcome most of the time?
3. Does getting it wrong occasionally cost less than running the expensive path every time?

If yes to all three, it's a gate candidate. Log this pattern in the Nova guiding values doc as an eighth reduction area, since it doesn't cleanly fit the existing seven (it's a structural placement decision, not a curation or routing technique on its own — routing already exists as one of the seven, but a gate is specifically about tier placement ahead of a model call).

## Task brief for Claude Code

**Objective**: audit Nova's current architecture end to end and flag every point where a Confirm/Deny Gate could sit ahead of the 3B model, the Claude API grounding calls, or any other expensive step — without proposing a full implementation yet. This is a discovery pass, not a build pass.

**Scope — inspect these specifically**:

- **RAG retrieval path**: is per-character Chroma filtering with fallback already acting as a gate, or does ambiguity still reach the model unfiltered? Check the 3-result cap logic for fiction queries — is that cap itself gated by a confidence signal, or fixed regardless of query type?
- **`nova_logger.py` blend detection**: what's the current detection mechanism? Is it already a cheap tier ahead of `nova_corrector.py`, or does every flagged instance go straight to the Claude API grounding call regardless of confidence?
- **`nova_corrector.py` correction loop**: could a local similarity check against prior corrected pairs resolve high-confidence cases before hitting the API?
- **Nova Controller routing**: does routing itself invoke the 3B model, or is it already a non-model classifier? This is the highest-value site if routing is currently model-based.
- **Coding agent eval harness** (the dev/held-out split work): identify where static checks (syntax, test pass/fail) could gate ahead of model-based correctness judgments, so the model only judges genuinely ambiguous task outcomes.

**Deliverable**: a findings doc listing each site, current behavior, proposed gate mechanism (heuristic vs. embedding vs. tiny classifier), and a rough estimate of what fraction of traffic the gate could plausibly resolve without escalation. No code changes in this pass — flag build candidates for a follow-up task.

## Applying this beyond Nova

Add "Confirm/Deny Gate candidate check" as a standing question during early architecture for any new dev project — the three-question test above takes seconds to run and catches unnecessary expensive-model calls before they get baked into the design.
