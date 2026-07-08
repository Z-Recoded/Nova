# Nova Skill: Memory

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked for memory read/write tasks on the Chroma-vs-nova_state.db routing rule, consolidation criteria, and what must never enter the corpus.

## Conventions

- Routing rule: unstructured, narrative, or reference material (lore, conversation history, documentation) goes into Chroma. Structured, queryable, frequently-updated state (financial figures, task status, game phase) goes into nova_state.db. If a task seems to need both, write structured facts to nova_state.db and a narrative summary (without the raw structured values) to Chroma.
- Memory crystal consolidation: recurring or high-salience facts get consolidated into a single durable "crystal" note rather than living scattered across many raw conversation-log chunks — this reduces retrieval noise over time.
- Two-tier decay (per nova_memory_store.py): recent raw entries decay in retrieval priority faster than consolidated crystals, which persist at a much slower decay rate. New writes are always raw-tier; consolidation is a separate promotion step, not automatic on write.
- Filename-prefix convention is enforced at ingestion — every chunk written into Chroma must carry a source filename prefix; this is a memory-layer responsibility, not something downstream retrieval fixes after the fact.

## Constraints

- Financial data, credentials, API keys, and any secret/sensitive config values never enter Chroma under any circumstance — these stay in nova_state.db or nova_config.json only.
- Never consolidate/summarize away specific details that a later query would need (e.g. don't crystallize "Marvin worked on Nova stuff" — preserve the specific module/decision names).
- Never write duplicate crystals for the same fact — check for an existing crystal on the same topic before creating a new one.
- Never let decay rate silently make a still-relevant fact unretrievable — decay affects ranking priority, not deletion; nothing is purged without an explicit retention-policy decision (see Nova Log rotation, 86barby7t, for the one existing purge rule).

## Output format

A memory-write action reports: what was written (crystal vs raw), which store it went to (Chroma vs nova_state.db) and why, and whether it triggered a consolidation of prior related entries.

## Examples

Good: "Wrote a consolidated crystal to Chroma: 'Token Budget Governor thresholds finalized 2026-07-06 — see nova_token_budget_governor_thresholds.md.' Superseded 2 prior raw entries on the same topic. No financial/secret data involved."

Bad: "Saved everything from this session into memory so Nova remembers it." (No routing decision made between Chroma and nova_state.db, no check for duplicate/prior entries, and "everything" risks writing sensitive data into the corpus — exactly what this skill file's constraints exist to prevent.)
