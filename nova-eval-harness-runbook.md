# Nova Agent Evaluation & Training Runbook

> Source: research pass on eval-harness design, held-out testing, small-model (3B-class) agent training methods beyond scale, and whether that methodology generalizes to larger parameter counts. This document is the actionable output — six initiatives, prioritized, each written to drop directly into Nova's ClickUp board.

---

## The core diagnosis

The recurring problem — gates built against observed failures, the same failure states slipping past anyway — isn't a training gap. It's a measurement gap, and it has a name: task overfitting.

Gates written from a fixed set of tasks and evaluated on that same set get very good at catching exactly what they were built from and stay blind to anything shaped slightly differently. This is a documented, common failure mode in agent evaluation research, not something specific to Nova's setup. The fix is procedural, not a bigger model or more training data: separate the tasks used to *build* gates from the tasks used to *test whether gates generalize*, and never let the second set leak into the first.

---

## What the research says about scale

Two findings matter most for deciding how far to invest in small-model specialization before reaching for something bigger:

- **Test-time compute (verifier-guided search, process reward models) helps weak models a lot and strong models only a little.** The smaller / less capable the base model, the more a verifier or reward-guided search improves it — to the point of small models beating models over 100x their size in published results. This means specializing and verifying small models isn't a workaround for lacking compute — it's the higher-leverage regime, not a lesser one.  
- **A small tuned model isn't throwaway work if a module eventually needs a bigger backing model.** Weak-to-strong search research shows a small tuned model can steer a much larger base model at inference time, achieving similar outcomes to directly tuning the large model. The 3B DPO investment carries forward as a steering signal even if a module later needs more horsepower.  
- **Caveat — specialization isn't automatically a win.** Evidence is mixed: some studies show narrow specialist models losing to general-purpose models on the same tasks; others show specialists winning clearly (e.g., JS/TS-specific models beating larger general models). The difference seems to come down to execution quality of the specialization, not whether specialization is inherently correct. Each specialist needs its own vetting, not an assumed win.

---

## The six initiatives

### 1\. Split the eval task pool: dev set vs. held-out set

**Priority: first — everything else depends on this being in place.**

Separate the current 6 recurring eval tasks (dev set — safe to keep iterating gates against) from a new, second set of tasks that are never used to write or tune a gate, only to test whether a gate generalizes. This directly targets the exact failure described: gates that catch known failure states but let the same shape of failure through when it shows up differently. No held-out set, no way to know if a gate actually generalizes versus just memorizing the dev set.

*Depends on: nothing — this can start immediately.*

---

### 2\. Audit existing gates individually, not as a block

Go through the current gate set one at a time against both the dev set and (once built) the held-out set. Expect uneven results — some gates will carry most of the real lift, others will contribute little or actively hurt precision. This is a documented pattern in gate-based agent evaluation research, not a sign anything was built wrong the first time.

*Depends on: Initiative 1 (held-out set needs to exist to actually test generalization).*

---

### 3\. Add a verifier at inference time, before touching training data volume

Highest-evidence, lowest-cost lever available right now. Build a lightweight verifier (or process-reward-style checker) that scores the coding agent's output at inference time and guides retry/search — not a training-loop change, a runtime addition. Published results show this kind of verifier-guided approach lets small models outperform models over 100x larger. Prefer a generative/reasoning-style verifier over a simple pass/fail classifier — generative verifiers hold up much better on novel, out-of-domain failures, which matters directly for a coding agent that will keep encountering bugs it hasn't seen before.

*Depends on: nothing structurally, but most useful once Initiative 1's held-out set exists to measure whether the verifier is actually helping generalization vs. just the dev set.*

---

### 4\. Do not fold the verifier into the training/RL reward loop yet

Not a build task — a scoping guardrail. Research shows reward models used directly as an RL training signal can backfire (reward hacking, degraded results vs. a plain success-reward baseline). Keep the verifier from Initiative 3 as an inference-time tool only, until there's a specific, evidenced reason to move it into the training loop.

*No dependency — this is a standing constraint on Initiative 3's scope, log it as a note on that task rather than a separate action item if that fits the board better.*

---

### 5\. If pursuing language-specific specialists (Python/JS/C), scope a vetting harness per specialist

Evidence on specialization is mixed — some studies show specialists losing to general models, others show clear wins. Any specialist built (Python, JavaScript, C, etc.) needs the same dev-set/held-out-set discipline from Initiative 1 applied individually, not an assumption that "narrower is better" transfers automatically. This is what turns the original specialization idea from a plausible direction into something actually validated before investing further training time in it.

*Depends on: Initiative 1 (reuses the same held-out methodology, extended per specialist).*

---

### 6\. Treat 3B DPO fine-tunes as durable infrastructure, not disposable prototypes

Not a build task — a standing principle to carry into future scoping decisions. If a module outgrows a 3B model, the tuned model doesn't get thrown away; weak-to-strong search research shows it can steer a larger backing model at inference time instead. Worth keeping in mind when deciding whether "the small model isn't working" actually means "start over" or means "pair it with something bigger."

*No dependency — reference this when scoping future model-size decisions rather than treating it as a discrete task.*

---

## Suggested board sequencing

1\. Split eval pool (dev vs. held-out)

        │

        ├──► 2\. Audit existing gates individually

        │

        ├──► 3\. Add inference-time verifier ──► (4. guardrail note, not a task)

        │

        └──► 5\. Per-specialist vetting harness (if/when specialization work resumes)

6\. Standing principle — no task, reference during future scoping

Initiatives 1 and 3 are the two with the clearest immediate payoff and the least dependency on anything else being built first — reasonable candidates for "next up" if choosing where to start.  
