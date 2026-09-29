# Marvin's Study-Session Dataset (Nova Tutor training data)

Established 2026-09-26. Schema version 1. Marvin decided to record how each study session goes
so Nova Tutor can later be built on his own data, not generic assumptions. This doc defines what
is recorded and why. It does not define how Tutor uses it; that is a design-doc decision.

## Where it lives, and why

- **Data:** `C:\Nova\data\tutor_sessions\events.jsonl` (append-only, one JSON object per line).
- **Helper:** `C:\Nova\data\tutor_sessions\log_event.py` validates and appends rows. Usage is in its
  docstring. Rows that break the schema are rejected, not written.
- **Local only.** `data/*` is gitignored and `C:\Nova` is outside OneDrive. This is personal
  learning data (Architecture Principles v1.1: local-only data sensitivity rule). **Never commit,
  sync, upload, or paste rows into a cloud service or vault note** without Marvin's explicit say-so.
- **Not in the vault.** The Anki Card Standard forbids mastery, scheduling, and review state in
  vault notes. Vault session notes (`Study Sessions/`) hold the Q&A prose and cards; this dataset
  holds the measurements. Anki review data (lapses, due counts) may be *read* via AnkiConnect
  and snapshotted here, never written back.
- `log_event.py` is untracked (it sits in the ignored `data/` folder). If it is lost, recreate it
  from this schema.

## Event types

Every row gets `schema_version` and `ts` (local time, ISO 8601) automatically. All rows carry a
`session_id` of the form `YYYY-MM-DD-NN`.

| type | when written | required fields |
|---|---|---|
| `session_start` | session opens | `session_id`, `mode`, `topics` |
| `question` | a question is resolved | `question_id`, `kind`, `tier`, `prompt`, `outcome`, `hints_used` |
| `card` | a card is proposed, approved, rejected or edited | `card_id`, `status`, `front`, `back` |
| `anki_review` | Anki's review log is read (session end) | `review_id`, `card_id`, `button`, `duration_ms` |
| `meaning_check` | Claude judges a typed answer's meaning (session end) | `review_id`, `card_id`, `judgment` |
| `link` | a cross-topic or cross-domain connection is made | `from`, `to`, `relation` |
| `note` | any observation worth keeping | `text` |
| `session_end` | session closes | `session_id` |

### Optional fields worth filling in

- **session_start:** `sources` (wikilinks), `deck`, `goal`, `self_report` (`energy`, `focus`,
  `confidence_in_material`, each 1 to 5), `anki_snapshot` (due and lapse counts for touched decks),
  `context` (time of day, interruptions, prior sleep/food if Marvin volunteers it).
- **question:** `topic`/`subtopic`/`parent_topic` and `source_note` (same fields as Tutor's chunk
  schema and the vault frontmatter), `chunk_id` (null until Tutor exists), `expected_answer`,
  `trigger` (`marvin_question`, `claude_quiz`, `retest`), `unknown_type` (`supporting_concept`,
  `origin`, `usefulness`, `other`), `card_ref`, `card_prompt` (what the Anki card is asking),
  `attempts` (`answer`, `judged`), `hints` (`level`, `text`), `error_type`, `your_answer`,
  `correction`, `confidence_before` (1 to 3, asked before the answer is revealed), `retest_of`
  (an earlier `question_id`), `card_id`, `feedback_delivered`.
- **card:** `note_type`, `deck`, `question_id`, `reject_reason` (why Marvin rejected or edited it is
  itself signal), and `media`: a list of `{kind, file, origin, source_url, license}`.
- **link:** `confidence` (0 to 1) and `status` (`pending`/`approved`/`rejected`), matching Tutor's
  `SynthesisLink` so approved links can be promoted later.
- **session_end:** `felt_difficulty`, `confidence_after`, `what_worked`, `what_didnt`,
  `retest_next` (question ids or topics), `cards_approved`.

### Enumerations (enforced by the helper)

- `mode`: review, supplemental_cards, quiz, retest, research_review, other.
- `kind`: why, what, how, compare, apply, predict, other.
- `tier`: `fact` or `higher_order` (per Agarwal 2019, see the evidence doc).
- `outcome`: `correct_unaided`, `correct_after_hint`, `revealed`.
- `attempts[].judged`: correct, partial, incorrect, blank, idk.
- `error_type` (null when correct unaided): omission, partial_recall, confused_similar_concept,
  reversed_relationship, terminology_only, misconception, overgeneralization, procedure_slip,
  unable_to_apply, guess, other.
- `media.kind`: image, gif, svg, audio. `media.origin`: generated, downloaded, screenshot,
  user_supplied.

Consistency rules the helper enforces: `hints_used` is 0 to 3 and equals the number of recorded
hints; `correct_unaided` requires 0 hints; `correct_after_hint` requires at least 1.

## Typed answers and the session-end meaning check (added 2026-09-26)

Goal (Marvin's): learn concepts **in his own language**, not memorize the lecture's wording. Anki's
type-in check compares characters, which rewards verbatim recall, so meaning is judged separately.

- **Capture:** the `nova_typed_answer_log` Anki add-on (installed in `%APPDATA%\Anki2\addons21\`,
  canonical source in `data\tutor_sessions\anki_addon\`) appends one row per graded card to
  `data\tutor_sessions\anki_typed_answers.jsonl`: `review_id`, `card_id`, `ease`, `typed_answer`,
  `expected_answer`. Local only, no network, never changes a card. Errors go to
  `anki_typed_answers_errors.log`. It loads only after Anki restarts. **Untested inside real
  Anki as of 2026-09-26**: the handler was tested with stand-in objects, and whether Anki's
  `reviewer.typedAnswer` holds the text at grading time is confirmed only once real rows appear.
  If `typed_answer` is null in real rows, the attribute is not available at that hook and the
  add-on needs a different hook.
- **Session end (automatic when Marvin says the session is done):** `finish_session.py <session_id>`
  reads Anki's review log for the session's deck, joins typed answers by `review_id`, and logs
  `anki_review` events. Claude then judges each typed answer against the expected answer **by
  meaning, not wording** and logs a `meaning_check`: `verbatim`, `paraphrase_same_meaning`,
  `partial`, `off`, or `blank`, with optional `missing_points` and `note`. Marvin gets a short
  readback of what he had right in his own words and what was missing.
- **Dictated answers:** if Marvin dictates into the answer box, the transcript is what gets logged,
  and a speech-recognition error can look like a wrong answer. Judge meaning charitably and note
  suspected transcription errors. Set `response_mode` (`typed`, `spoken_dictated`, `spoken_aloud`,
  `mixed`) on `session_start` so typed and spoken sessions can be compared later.

## Hint ladder (how Marvin wants his study questions handled)

Set and clarified by Marvin 2026-09-26. **The trigger is Marvin's own question, not a quiz.** While
studying an Anki card he may ask Claude a why/what question about something the card leans on
that he does not fully understand: a **supporting concept** the answer requires, the **origin** of
how the knowledge was constructed, or **why it is useful** (`unknown_type`). Claude treats that
question as his *attempt at understanding*, so it does not give the full answer first. Instead it
gives a hint that ties back to **what the Anki card is prompting** (`card_prompt`), up to
**three hints per question**:

1. **Hint 1, cue:** points at the category or a related idea; contains no answer content.
2. **Hint 2, narrowing:** a contrast, a structural clue, or a partial statement.
3. **Hint 3, near-answer:** a fill-in-the-blank or most of the explanation.
4. After hint 3 (or once Marvin says he has it, or asks for the answer): the full explanation, with
   cross-topic links. Log `outcome: revealed` if Claude had to give it, `correct_after_hint` if
   Marvin reached it from the hints, `correct_unaided` if his question already contained the answer.

Marvin may reason aloud between hints; each reply is an entry in `attempts`. "Just tell me" always
overrides the ladder. Feedback always follows. This is attempt-then-feedback, which is what the
retrieval studies support (see `tutor-learning-science-evidence.md`).

Log these as `trigger: marvin_question`, with `card_ref` (the Anki note or deck path) and
`card_prompt`. `claude_quiz` and `retest` triggers are for when Claude asks the questions (later
retests, below); those follow the same ladder.

**Claude does not watch Anki.** It cannot see Marvin's reviews as they happen or notice a wrong
answer on its own. The ladder runs only inside a conversation, when Marvin asks. What Claude *can*
do is read Anki's review log through AnkiConnect (read-only) at the start or end of a session.
See "What can and cannot be measured".

**Why it matters for Tutor:** `hints_used` plus `outcome` is a graded difficulty signal, richer than
right/wrong, and `error_type` + `your_answer` + `correction` maps directly onto Tutor's
`StruggleEntry` (`date`, `error_type`, `your_answer`, `correction`). How hint counts should map to
`MasteryModel.score` is **not decided**; the raw counts are stored so that mapping can be tested
against real data later.

## What can and cannot be measured

- **Response latency in chat.** Chat gives no trustworthy per-answer timing. `ts` stamps when a row
  is logged, so gaps between consecutive rows bound the time; they are not stopwatch times.
- **Anki's own review log (captured as `anki_review` events, format verified 2026-09-26).**
  AnkiConnect's `cardReviews(deck, startID)` returns rows of
  `[review_id_ms, card_id, usn, button, new_interval, prev_interval, ease_factor, duration_ms, review_type]`.
  Button is 1 Again, 2 Hard, 3 Good, 4 Easy. A negative interval is seconds (a learning or relearning
  step: -600 is 10 minutes), a positive one is days. This is the real source for "which cards were
  failed" and for true per-card answer time. Caveats: **Anki caps duration at its "maximum answer
  seconds" setting (60 s here)**, so a 60000 ms row is a lower bound and is flagged
  `duration_capped: true`; rows come back unordered, so sort by `review_id`; typed answers are not
  stored, only the grade Marvin chose. `review_id` is unique, and the helper skips an
  already-logged one, so re-reading the log is safe. Read-only; nothing from it goes into the vault.
- **Real retention.** In-session correctness after hints says little about durable learning (the
  LLM-tutoring papers in the evidence doc fail here). Use `retest_of`: re-ask missed or hinted
  questions at the start of a later session, with no hints on the first try, and log it as
  `mode: retest`. This is the delayed, unassisted measure the evidence doc recommends.
  Agreed by Marvin 2026-09-26 ("I like that").

## Media on cards (recorded via `card.media`)

The Anki Card Standard's "Media on cards" section governs the format. Practical notes:

- Anki's media store is flat, so a filename collision silently overwrites another card's image.
  Name files `YYYY-MM-DD-<topic-slug>-<fact-slug>.<ext>`.
- Files must live inside the vault for the plugin to copy them. The vault has no attachments
  folder yet; the first approved card with media creates one (needs Marvin's approval).
- Generated diagrams and animated GIFs (matplotlib/PIL) need no licence. For downloaded
  images, record `source_url` and `license`, and prefer public-domain or openly licensed sources.
- Video and inline interactive HTML are unsupported by the plugin. Use a static image or GIF on
  the card and link the interactive version from the source note.

## Provenance

The decisions to record sessions, build media-rich cards, and use the three-hint ladder are
Marvin's (2026-09-26). The field list, enumerations, retest protocol, and the specific hint tiers
are Claude's proposals and can be changed. Change `SCHEMA_VERSION` in `log_event.py` if fields are
removed or renamed, so old rows stay interpretable.
