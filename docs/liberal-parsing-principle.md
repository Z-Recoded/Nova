# Liberal Parsing of Model Output — a Borrowed Principle That Paid Off

Written up 2026-08-15 at Marvin's request, after the principle proved itself live on the
ACI harness. This is the standalone note; the finding itself is also folded into
`docs/coding-specialist-exercise-corpus-plan.md` since it's directly relevant to how that
corpus should eventually get used.

## The principle

**Be conservative in what you produce, liberal in what you accept.** This is Postel's
Robustness Principle, originally written for TCP implementations (RFC 761, 1980) — a
network protocol handler should send strictly-conformant output but tolerate imperfect
input from the other side, rather than rejecting anything that deviates from spec.

Applied here to LLM tool-calling: a harness parsing a small model's structured output
should tolerate a near-miss that clearly carries the intended meaning, rather than
demanding byte-exact conformance to one schema and discarding everything else as a
failure.

## The real evidence, not just the theory

Building `nova_aci_harness.py` (the coding specialist's constrained-action-space
interface, `86bbch95y`), a strict-JSON-only parser rejected every one of Qwen2.5-Coder-7B's
real edit attempts against the vendored `bob` exercise. The model's *reasoning* was
correct — a live inspection of the raw output showed genuinely correct Python logic for
the task — but it kept encoding that logic using Python string-literal syntax (single- and
triple-quoted strings) instead of strict JSON, and repeated the same "mistake" across
every retry because the original error message gave it nothing specific to act on.

The fix, verified against real captured data before being trusted:

1. **Strict JSON first** (`json.loads`) — the fast path when the model gets it exactly right.
2. **`ast.literal_eval` as a safe fallback** — accepts Python literal syntax (the model's
   actual, consistent output shape) without accepting arbitrary code execution the way
   `eval()` would. Tested against all 15 real turns from a failed run before shipping:
   recovered 14 of 15 immediately.
3. **One targeted repair** for the remaining case — a single missing closing quote right
   before the call's trailing braces, confirmed by inspecting the exact raw bytes (not
   reconstructed from terminal display, which had already mangled the escaping once).
   Recovered the 15th turn too.

Re-run with all three tiers in place: the same exercise went from **0/26 real tests
passing across a fully burned 15-turn budget** to **26/26 passing in 3 turns** — turn 2's
edit call parsed via the `ast.literal_eval` tier on the very first real attempt.

## Why this matters beyond one exercise

**It changes the diagnosis of "small model failure."** What looked like a capability
problem was a rigidity problem in the harness. That's a materially different, and
materially cheaper, thing to fix than it first appeared.

**It's worth re-examining `86bb72gpa`/`86bb728nj`** (RunPod-Qwen/Devstral repeatedly
re-verifying already-successful edits, burning full turn budgets without progress) through
this same lens — an open question, not yet checked: how much of that read as reasoning
failure but was actually a rigid parser rejecting a near-miss with no informative feedback
loop back to the model.

**It's a second, independent lever from the ACI's original design.** SWE-agent's own
contribution (the research the ACI is modeled on) was narrowing the *action space* — fewer,
more structured commands. This finding is a separate axis: leniency in how an action gets
*encoded*, even within an already-narrow action space. Both matter; neither substitutes
for the other.

**It should inform `86bbch988`'s edit-format comparison.** That plan currently proposes
testing three formats (whole-file/search-replace/unified-diff) under strict parsing,
matching Aider's own benchmark methodology. Worth asking directly: would a lenient
implementation of each format change which one wins? A format that looks weak under strict
parsing might turn out fine once given the same leniency `nova_coding_aci.edit()` just got.

## The broader point, per Marvin

This was a principle borrowed from a completely different field (networking protocol
design, 1980) and it produced a real, measured, order-of-magnitude improvement on a
genuinely hard current problem (small-model tool-calling reliability) — not by training
harder or picking a bigger model, but by importing a 45-year-old idea that was never about
AI at all. Nova already has a kindred, though narrower, initiative on the board
(`86bafunj2`, "Classical Algorithm Integration into Nova's Decision Layer" — A*, DP,
priority queues, memory decay for the retrieval/routing layer specifically); this is the
same instinct — look outside ML research for established solutions before assuming a
problem needs a bigger model or more training data — applied to a different part of the
system. Worth treating as a standing practice, not a one-off: when a Nova subsystem is
stuck, it's worth asking whether an established principle from an unrelated field already
solved a structurally similar problem.
