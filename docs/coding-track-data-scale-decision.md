# Coding track: real data-scale gap, and what to do about it

Written 2026-08-20, after Phase 0-5 all had real shipped work for the coding track in one
session. Real question that prompted this: how does Nova's actual data volume compare to what
comparable small/local models needed to reach comparable results, and does that change what to
build next. Answer: yes, materially — self-generation alone cannot reach a comparable scale with
the current source pools, and real, already-existing alternatives exist that haven't been
evaluated yet.

## The real gap, in Nova's own numbers

| Stage | Nova's real count | A real comparable-scale reference | Gap |
|---|---|---|---|
| SFT-shaped examples (Phase 1, `nova_bulk_distillation.py`) | 171 (of 201, real ceiling — Phase 3's entire git-history task pool is exhausted) | phi-1's CodeExercises fine-tune: ~880K examples | ~5,000x |
| Execution-verified SFT examples (Phase 2, `nova_coding_execution_refinement.py`) | 27 verified_pass (of 30, real ceiling — the entire vendored Exercism corpus) | — | — |
| DPO preference pairs (Phase 4, `nova_coding_dpo_filter.py`) | 6 kept (of 8 real candidates, derived from Phase 2) | A modest real practical DPO run (neural-chat-7b-v3-1): 12.9K pairs | ~2,000x |

**171 vs. 6 is not an inefficiency to fix — it's a structural fact.** SFT examples (Phase 1) are
one Claude call each, no verification required. DPO pairs only exist where a real attempt
genuinely failed and a later attempt genuinely fixed it — a narrow slice of all attempts (8 of 30
exercises needed a real retry at all in today's Phase 2 run). Scaling DPO pair count requires
scaling the *source task pool* Phase 2 runs against, not just running Phase 4 harder.

**Real research finding worth taking seriously:** when both model scale and dataset size are
small, DPO has little room to meaningfully reshape a model's behavior beyond what SFT already
provides — preference tuning becomes reliably effective only once model capacity *and* data
scale are both large enough. At 6 real pairs, Phase 4's output is almost certainly below that
threshold. This isn't "train now and see" — the data volume itself is the blocker.

## Why self-generation alone can't close this gap right now

Both of the coding track's real data sources are structurally exhausted, not merely
under-utilized:
- Phase 1 → Phase 3's synthetic task pool = this repo's *entire* real commit history (201
  commits). No more raw material exists until the repo's own history grows.
- Phase 2 → the vendored Exercism corpus = 30 exercises, fully consumed this session.

Getting to a comparable scale from self-generation alone would need an order-of-magnitude larger
source task pool than either of these — not a tuning change to the existing scripts.

## Real external options, not yet evaluated

**Already-fine-tuned checkpoints of the exact base model Phase 0 already chose.** Two real, named
candidates found: `SWE-Gym-7B` (Pan et al.) and `SWE-Dev-7B` (Wang et al., 23.4% reported on their
own benchmark), both fine-tunes of Qwen2.5-Coder-7B-Instruct for SWE-agent tasks. Real caveat,
not yet resolved: both were trained against the **OpenHands** scaffold, not Nova's own constrained
ACI (`nova_coding_aci.py`'s `find_file`/`search_file`/`search_dir`/`view`/`edit`/`done` JSON
protocol) — an interface mismatch that may or may not transfer well, genuinely unknown until
tested. Also unconfirmed: whether either checkpoint has published weights on HF Hub at all (the
search surfaced the papers, not a direct download link). **This is close to free to check**:
`nova_aci_harness.py` already runs any Ollama-served model against the real 30-exercise corpus at
$0 (Ollama only, no API cost) — pulling a candidate checkpoint and running it through the existing
harness is a real, cheap, apples-to-apples comparison against the current Qwen2.5-Coder-7B
baseline, before committing to more from-scratch training.

**Real public data wells, one of which is already half-wired in.** `KodCode-V1` (487K real,
verified question/solution/test triplets, CC BY-NC 4.0) is **already integrated** in
`nova_finetune_qwen_coder_sft.py` — but for the older, deprioritized Qwen2.5-Coder-**32B** path,
not the current 7B Training Pipeline. Reusing it for the 7B target is a real, concrete, near-term
option, not a new build. Larger alternatives found: `OpenCodeInstruct` (5M examples, CC-BY-4.0),
`OpenCodeReasoning` (735K examples, CC-BY-4.0). For DPO specifically — the track's sharper
bottleneck — `Code-Preference-Pairs` (Vezora) is a real, existing synthetic dataset for bug
identification/correction, a close conceptual match to what Phase 4 is trying to produce from
Nova's own thin corpus.

## Storage is not the constraint

Checked directly, 2026-08-20: Aero has 372GB free, Omen has 67GB free. Every dataset above is
plain text — even the largest (`OpenCodeInstruct`, 5M rows) is almost certainly low tens of GB
raw. For scale, this project's own real history already handled 65GB *model checkpoints* multiple
times (the Qwen-32B fine-tune runs, `runpod_finetune_runbook.md`) — a full public text dataset is
smaller than one of those checkpoints. The real cost of adopting any of the above is engineering
time to wire it in and (for a downloaded checkpoint) real GPU time to run the comparison, not
disk space.

## How this interacts with the three new exploration tickets (filed 2026-08-19)

- `86bbh41p2` (specialist-squad architecture) cites the same real finding this doc leans on: small
  models only compete with verification/supervision in the loop, not on raw scale. Evaluating an
  existing checkpoint first is a cheap way to test that finding directly against Nova's own ACI
  before deciding whether to build toward a squad architecture at all.
- `86bbh0ad9` (oracle-based signal for `nova_corrector.py`) is the conversation-track's version of
  what Phase 2/4 already proved for coding this session — real execution grounding, not just
  Claude's text judgment. Directly informs whether `Code-Preference-Pairs` (execution/bug-fix
  shaped) is a better DPO seed than more Claude-graded corrections from Nova's own thin data.
- `86bbh41rk` (small proxy-model validation gate) is cheap insurance to adopt before the first real
  training run on any of the above — validate a data mixture/recipe on a small proxy before
  spending the full 3B-class training budget on it.

## Real open decision for next session

Not a build task yet — a scoping decision. Recommended order, cheapest-and-most-informative
first:

1. **Check whether `SWE-Gym-7B`/`SWE-Dev-7B` weights are actually published**, and if so, run one
   through `nova_aci_harness.py` against the real 30-exercise corpus (real, $0, ~30 min) —
   answers "is training from scratch even necessary" before anything else.
2. **If that's inconclusive or the checkpoints aren't usable**, wire `KodCode-V1` into a 7B-target
   SFT script (adapting the existing 32B-path integration, not a new build) — closes most of the
   171-vs-880K gap with zero new data-generation cost.
3. **For DPO specifically**, evaluate `Code-Preference-Pairs` as a seed dataset before assuming
   Phase 4 needs to keep growing Nova's own 6-pair corpus one exercise at a time.
4. Only after 1-3 look genuinely insufficient does "scale up self-generation's source task pools"
   become the right lever — and even then, it means finding a bigger task source, not tuning the
   existing scripts harder.

## Sources

- [Textbooks Are All You Need (phi-1 paper)](https://arxiv.org/abs/2306.11644)
- [CodeExercises dataset — Emergent Mind](https://www.emergentmind.com/topics/codeexercises-dataset)
- [LoRA Land: 310 Fine-tuned LLMs Technical Report](https://arxiv.org/pdf/2405.00732)
- [An Empirical Study of SFT-DPO Interaction and Parameterization in Small Language Models](https://arxiv.org/pdf/2603.20100)
- [Fine-tune a Mistral-7B model with DPO — Towards Data Science](https://towardsdatascience.com/fine-tune-a-mistral-7b-model-with-direct-preference-optimization-708042745aac/)
- [Training Software Engineering Agents and Verifiers with SWE-Gym](https://arxiv.org/html/2412.21139v2)
- [KodCode/KodCode-V1 — Hugging Face](https://huggingface.co/datasets/KodCode/KodCode-V1)
