# Nova Skill: Retrieval

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked for a retrieval task (answering from Nova's memory corpus) on graph vs flat search decisions, chunk limits, and character/source boundary rules.

## Conventions

- Default to flat vector search (Chroma) for direct factual queries. Use A* graph-guided traversal only when the query implies relational/multi-hop reasoning (e.g. "how does X connect to Y") — graph traversal costs more latency and should not be the default path.
- Chunk limits: general retrieval caps at 6 chunks per query. Fiction/lore queries (Symphony.EXE material) cap at 3 chunks per query — a tighter cap specifically to reduce character-blending risk in creative content.
- Per-character Chroma filtering: when a query names a specific character, filter retrieval to that character's namespace first; fall back to unfiltered corpus search only if the filtered search returns zero results.
- Every retrieved chunk carries a filename prefix identifying its source document — never strip this before passing chunks to the answering model.
- DP context window packing: when multiple chunks compete for a limited context window, pack by relevance-density, not simple truncation from the end.

## Constraints

- Never blend two characters' established facts/voice into a single answer without clearly attributing which source said what.
- Never present a graph-traversal inference (a connection Nova derived, not one stated directly in the corpus) as if it were a directly retrieved fact — flag inferred connections explicitly.
- Never exceed the chunk cap even if more relevant chunks exist — this is a hard ceiling, not a suggestion, specifically to control token cost and blending risk.
- Never retrieve or cite a source without its filename-prefix intact.

## Output format

A retrieval answer includes: the synthesized answer, then a source list (filenames of chunks actually used), then a confidence note if the corpus coverage was thin (fewer than half the chunk cap returned relevant results).

## Examples

Good: "Vex's fragmentation power was first established in scene_04_mirror_break.md; this is consistent with the cosmology rule in cosmology_foundations.md that fragmentation requires a broken reflective surface. [Sources: scene_04_mirror_break.md, cosmology_foundations.md]"

Bad: "Vex can fragment things and so can Kess, they both work the same way." (Blends two characters' rules without checking whether they're actually the same, cites no sources, and asserts equivalence that may not exist in the corpus — exactly the blending failure this skill file exists to prevent.)
