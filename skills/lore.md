# Nova Skill: Lore

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked for Symphony.EXE worldbuilding or fiction-adjacent tasks on character rules, source boundaries, and the fiction query cap that prevents character blending.

## Conventions

- Symphony.EXE's core themes are memory, fragmentation, love, and disconnection — new material should serve at least one of these, not introduce unrelated tonal elements.
- Every character's established facts live in their own source-bounded notes (filename-prefixed). When writing new material about a character, ground it in what's already established for that character specifically — cross-character consistency is checked, not assumed.
- Fiction/lore retrieval queries cap at 3 chunks (tighter than the general 6-chunk cap) — this is inherited from the retrieval skill and restated here because lore tasks are where blending risk is highest.
- New notes are written as Obsidian-ready markdown: YAML frontmatter with tags, wikilinks ([[Character Name]]) for cross-references, and the established file-naming convention (lowercase, underscore-separated, e.g. scene_07_name.md).
- Cosmological rules established in prior sessions are canon and must not be contradicted without an explicit, flagged retcon decision — new material extends canon, it doesn't quietly overwrite it.

## Constraints

- Never merge two characters' distinct voices, powers, or histories into one without clear in-universe justification and explicit flagging that this is a deliberate connection, not an error.
- Never introduce a new cosmological rule that contradicts an established one without calling out the contradiction and asking whether it's an intentional retcon.
- Never write character material as a generic archetype filler — ground every addition in the specific established world document set.
- Never let fiction content leak into non-fiction domain answers (financial, work, etc.) or vice versa — the source boundary is bidirectional.

## Output format

New lore material is delivered as one or more Obsidian markdown notes, each with: YAML frontmatter (title, tags, character/world links), body content, and a wikilink section connecting to related existing notes.

## Examples

Good:
```
---
title: Vex — The Mirror Fracture
tags: [character, vex, cosmology, fragmentation]
---
Vex's fragmentation is not a power she chose — see [[Cosmology Foundations]] for the mechanism. This note extends her established backstory in [[Scene 04 — Mirror Break]] without contradicting it.
```

Bad: "Vex and Kess are basically the same kind of character so here's some new lore that works for either of them." (Treats two distinct characters as interchangeable without justification, produces no wikilinks or frontmatter, and risks exactly the blending this skill file exists to prevent.)
