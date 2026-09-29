# Learning-Science Evidence for the Nova Tutor Module

Written 2026-09-26. Evidence digest only: **not a spec and not a set of decisions.** Tutor design
decisions belong in the Nova Learning Layer design doc (Drive, 2026-07-12) and ClickUp. Where
this doc suggests an implication, it is labeled a hypothesis for Marvin to accept or reject.

Companion note (Marvin's own discussion prep, includes his tensions with his study rules):
`Second Brain/Learning Research - Discussion.md`. That note is vault content and is not ingested
by Nova; this doc is the agent-facing version.

## How to use this doc

- Every number below was read from the full text of the paper, on 2026-09-26, unless flagged
  "second-hand". Do not add numbers from memory or from abstracts.
- Evidence strength is stated per finding. Several results are weaker than their abstracts read.
- Full PDFs are in `C:\Nova\nova-tutor-papers\` (filenames given per paper).
- Scope caveat for everything here: almost all participants are undergraduates or school
  students in classroom or lab settings, and most outcomes are short-term. Marvin's use is
  self-directed study. Treat transfer to Nova as a hypothesis.

## Bottom line

1. A failed attempt followed by feedback beats just reading, for **fact recall** (Kornell 2009,
   Richland 2009). Solid, replicated, but limited to fact-level items.
2. Retrieval practice transfers to application/inference questions only modestly, and only under
   certain conditions (Pan & Rickard 2018).
3. Fact-quiz practice did not improve higher-order test performance in Agarwal (2019); higher-order
   and mixed quizzes did. One paper, three experiments.
4. "Attempt before instruction" for concepts has a moderate meta-analytic effect (g = 0.36), but it
   is age-, topic-, and fidelity-dependent, and there is a serious opposing literature.
5. The two LLM-specific papers in the folder are weak evidence. Neither measured learning outcomes
   with a sound design. See "Weak or preliminary".

## Findings by paper

### Kornell, Hays & Bjork (2009), file `Kornell.Hays.Bjork.2009.pdf`
*Unsuccessful retrieval attempts enhance subsequent learning.* J Exp Psych: LMC. Six experiments,
UCLA undergraduates, n = 15 to 84.

- Test-then-answer beat read-only for items that could not be answered, on a later cued-recall
  test. Effect sizes: Exp 1 d = 0.58 (.41 vs .31); Exp 4 d = 0.38 (equal 13 s trial time); Exp 5
  d = 0.94 after about 38 h; Exp 6 d = 0.44 (between-subjects).
- **Not uniform:** Exp 2 (fictional trivia, equal total time) showed no difference (.32 vs .32).
  Exp 3's large effect (d = 1.49) is confounded by more time in the test condition.
- Wrong guess vs leaving it blank made no reliable difference (Exps 3 to 5). Exp 6 favored wrong
  guesses slightly (.71 vs .63).
- Feedback (the correct answer) followed every attempt. The authors call it critical.
- Materials: fictional trivia and weak word associates. **Facts and word pairs only.**
- The mechanism is the authors' conjecture, not tested.

### Richland, Kornell & Kao (2009), file `RichlandKornellKao.pdf`
*The pretesting effect.* J Exp Psych: Applied. Five experiments, n = 61 to 158 undergraduates,
one 2-page science passage, 10 fill-in-the-blank fact questions.

- Test-first beat extended study on tested items that the student got wrong on the pretest:
  75 vs 56% (d = 1.1); 71 vs 54% (d = 0.61, italics control); 82 vs 64% (d = 0.84, bold control);
  55 vs 45% after 1 week (d = 0.45); Exp 5: 90% test-first vs 78% memorize-the-questions vs 63%
  extended study.
- Attempting to answer beat merely studying the questions (Exp 5, d = 0.59), so it is not just
  "seeing the questions".
- Limits: Exp 1 assigned by seating section; Exps 4 and 5 used one-tailed tests; only
  fact-recall questions; one text. The authors state a pretest with no instruction after it is
  "of little use".

### Pan & Rickard (2018), file `PanRickard2018.pdf`
*Transfer of test-enhanced learning: meta-analytic review.* Psych Bulletin. 192 effect sizes, 122
experiments, N = 10,382.

- Overall transfer from testing vs a re-exposure control: **d = 0.40** [0.31, 0.50].
- Transfer to **application and inference questions: d = 0.32**, CI [0.085, 0.56] (41 effect sizes,
  17 papers), with substantial between-paper heterogeneity.
- Strongest: across test formats, medical-diagnosis problems, mediator cues. Weakest: rearranged
  stimulus-response items, untested material, and problems involving worked examples.
- Three robust moderators: **response congruency**, **elaborated retrieval practice**, and
  **initial test success**. Higher initial accuracy was associated with more transfer (about
  +0.0058 d per +0.01 initial accuracy; +0.46 d across the observed range 0.19 to 0.98).
- After publication-bias correction (PET-PEESE), moderator effects held, but the intercept
  predictions dropped substantially, "often indicating no positive transfer when none of the
  aforementioned moderators are present". Transfer is conditional, not automatic.

### Agarwal (2019), file `Agarwal2019.pdf`
*Retrieval practice and Bloom's taxonomy.* J Educ Psych. Exp 1 and 2: 48 college students each;
Exp 3: 142 sixth graders (world history, in a real classroom). All multiple-choice with feedback.

- Retrieval practice raised delayed test scores by about 20 to 30% vs no quizzes or rereading.
- **Fact quizzes improved fact tests but not higher-order tests.** Higher-order and mixed quizzes
  improved higher-order tests. Best results came when the practice matched the final test's type.
- The author's own caveats: students may not have known the fact quizzes were relevant to the
  reasoning questions (no prompt to transfer); multiple-choice format; effect may differ by age
  (mixed quizzes helped the sixth graders more).
- This is retrieval practice **after reading**, not before instruction.

### Sinha & Kapur (2021), file `SinhaKapur2021.pdf`
*When problem solving followed by instruction works.* Rev Educ Res. Meta-analysis of 53 studies,
166 comparisons, problem-solving-then-instruction (PS-I) vs instruction-then-problem-solving (I-PS).

- Conceptual understanding and transfer: **Hedges g = 0.36** [0.20, 0.51]. Stronger (g 0.37 to 0.58)
  with high fidelity to Productive Failure design principles. No cost to procedural knowledge.
- The frequently quoted **0.87** is a p-curve estimate of the true effect, not the pooled
  effect. Funnel-plot asymmetry was not detected (Egger p = .22). Prefer 0.36 as the headline.
- **Boundary conditions found in the paper:** effects favored I-PS for grades 2 to 5 (g about
  -0.09) and for domain-general skills; effects rise with grade level; about 75% of comparisons
  were math and physics; controlled experiments had much lower PF fidelity (mean 32%) than
  quasi-experiments (77%). The authors cite work suggesting the advantage may diminish as material
  complexity rises, and note that failure rates were rarely reported.

### Kirschner, Sweller & Clark (2006), file `KirschnerSwellerClark2006.pdf`
*Why minimal guidance during instruction does not work.* Educ Psychologist. The main opposing
position. A theoretical and literature-review argument, not an experiment.

- Claim: novices have limited working memory, so unguided problem search consumes it without
  building long-term knowledge. Strongest evidence cited is the worked-example effect.
- **Their own boundary condition:** the advantage of guidance recedes, and the worked-example
  effect reverses (expertise reversal), as learner prior knowledge rises.
- The paper predates the Productive Failure literature. Sinha & Kapur argue PF is not "minimal
  guidance" because it ends with teacher-led consolidation. Hmelo-Silver, Duncan & Chinn (2007)
  reply that problem-based and inquiry learning are heavily scaffolded (**second-hand: abstract
  only, not read**).

## Weak or preliminary evidence

### Puech et al. (2025), files `2410.03781v2.pdf` (arXiv) and `2025.findings-acl.1348.pdf` (ACL, same paper)
*StratL: pedagogical steering of LLMs, case study on Productive Failure.*

- Design: intent transition graph. Student state is classified each turn, a learning-scientist
  graph selects the next tutor intent, and intent-specific prompt additions steer the LLM.
- Evidence: 17 ninth graders, 15 minutes, 2 problems x 2 conditions (3 to 5 per cell). PF Score
  (a process rubric) p = .046 and RSM count p = .05 on one problem.
- **No learning outcome was measured.** It shows the tutor followed the strategy, not that students
  learned. In simulation a trivial "Constant Intent" ablation matched StratL's PF Score.
- Student-state classifier micro-F1 = 0.77, on 10 conversations annotated by one author.
- "No negative spillover" is absence of evidence (questionnaire n = 3 to 5 per cell); StratL's mean
  perceived helpfulness was lower on one problem (1.20 vs 2.00 of 3).
- Usable as an implementation pattern (intent graph + per-intent prompt additions). Not usable as
  evidence that it improves learning.

### Akgun & Toker (2024), file `2412.13487v1.pdf`
*Pretesting with conversational AI.* Preprint, not peer reviewed.

- Reported: pretest 91.85 vs no-pretest 78.59 on an 11-item multiple-choice transfer test,
  t(58) = -5.97, p < .001; analysis n = 31 vs 29.
- **The paper's Cohen's d = 8.59 is wrong;** its own means and SDs give d of about 1.5.
- The final test followed a 10-minute break in the same session: **not a delayed test**.
- The groups were treated differently (scripted ChatGPT protocol for the no-pretest group; 10 min of
  unassisted struggle for the pretest group), so the failed attempt itself is not isolated. One
  course, one topic. 12 of 73 recruits excluded, split by group not reported.

## Candidate implications (hypotheses only)

Mapped to the phases in the 7-phase Learning Layer design. Phase 2 (SM-2/FSRS scheduler, freeform
answer eval, ClickUp `86bawnkc6`) is the nearest.

1. **Feedback always follows an attempt.** Every positive finding above assumed the answer or
   instruction came right after. A quiz flow that ends on an unanswered or unmarked attempt has no
   support here.
2. **Wrong answers are still useful.** Kornell found wrong guesses and blanks about equal. Storing
   the wrong answer's content (not just a score) in `struggle_history` would be consistent with that.
3. **Support question tiers.** Agarwal suggests fact-only quizzes will not build reasoning.
   A per-question tier tag (fact / higher-order / mixed) would let the quiz engine select them.
4. **Prefer elaborated retrieval** (explain, apply, infer) over bare recall when reasoning
   transfer is the goal, and keep practice congruent with how the material will be tested
   (Pan & Rickard). Freeform answer evaluation is a prerequisite for this.
5. **Do not assume "attempt-first" universally.** Benefits vary by learner level, topic type, and
   complexity, and the strongest clean evidence is for fact recall. Making it configurable per
   topic or learner level is more defensible than a global mode.
6. **Measure learning, not just in-session behavior.** The LLM-tutoring papers here fail on exactly
   this point. Any evaluation of Tutor should include a delayed, unassisted test. Marvin's
   session dataset (`tutor-session-dataset.md`) records `retest_of` links to support this.
7. **StratL is a pattern, not a proof.** Its structure (state tracing, deterministic intent graph,
   per-intent prompts) may fit `struggle_history` as an input, but its measured benefit is unproven.

## Not yet reviewed (deferred by Marvin, 2026-09-26)

- Bastani et al. (2025, PNAS), "Generative AI without guardrails can harm learning". Only a web
  summary was read; **no figures from it are included here, and none should be added until the paper is read.**
- Kestin et al. (2025, Scientific Reports), AI tutoring vs active learning. Search summaries only.
- Ashman, Kalyuga & Sweller (2020), problem-solving vs explicit instruction under high element
  interactivity. Known only through Sinha & Kapur's citation.
- Hmelo-Silver et al. (2007), response to Kirschner et al. Abstract only.

## Open questions for Marvin (from the 2026-09-27 discussion prep)

1. Does Tutor need an attempt-first (pretest) mode, and is it Phase 2 or later?
2. Is StratL-style intent control needed, or is a quiz engine plus scheduler enough for now?
3. Which of these findings should become requirements in the design doc or ClickUp?
