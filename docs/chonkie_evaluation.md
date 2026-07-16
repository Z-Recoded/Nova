# Chonkie Evaluation (ingest.py chunking)

> Provenance: produced by the first autonomous Nova dispatch (ClickUp 86baux7bb,
> 2026-07-16) via nova_scheduled_dispatch.py. The headless session ran with web/
> python3/graphify blocked, so library version/dependency specifics are from the
> model's prior knowledge, not live verification -- treat those as unverified.
>
> **Status of the key finding:** the 500-word-vs-256-token truncation bug this eval
> surfaced was independently measured and FIXED the same day (token-aware chunking,
> commit f52fa75). Chonkie itself was NOT adopted -- in-house token-aware chunking
> got the core win with zero new dependencies. This doc is kept for the Chonkie
> recommendation if ingest chunking is revisited (RecursiveChunker/SentenceChunker,
> skip the Handshake/Chef/Porter pipeline).

Confirmed: web access, `python3`, and `graphify` are all blocked in this session's permission mode — only `git` and file reads are going through. I couldn't run live verification (current Chonkie version/deps, or a hands-on chunk comparison). I've done the evaluation from the code I read directly plus what I know about Chonkie and Chroma's embedder, and I've flagged every claim that needs a live check before acting on it. This is a "not urgent" evaluation task with no code change required, so a written assessment is the deliverable.

---

## Evaluate Chonkie for `ingest.py` chunking

### 1. What Nova's chunking does today

`ingest.py:59` — `chunk_text(text, chunk_size=500, overlap=50)`:

```python
words = text.split()                      # split on whitespace
for i in range(0, len(words), 450):       # 500-word window, 50-word overlap
    chunk = ' '.join(words[i:i+500])
```

Then `ingest_file()` (`ingest.py:69`) anchors each chunk with `[filename]\n` **before** embedding (a Section 6 character-blending fix — must not be reverted), and upserts with a fixed metadata schema (`source, filename, project, description, chunk_index, total_chunks, links`). Chunks are embedded by Chroma's `DefaultEmbeddingFunction()` and retrieved in `nova_query.py` (`retrieve` / `retrieve_with_graph`).

**Three real weaknesses this surfaced — the first is the important one:**

1. **Chunk size (500 words) massively overshoots the embedding model's input window.** Chroma's `DefaultEmbeddingFunction` is `all-MiniLM-L6-v2`, whose max sequence length is **256 tokens (~180–190 English words)**. Every 500-word chunk is silently truncated at embedding time — roughly **the back ~60% of each chunk never influences its vector.** Retrieval matches only on each chunk's opening. The full text is still handed to the LLM as context, so answers aren't obviously broken, which is exactly why this has gone unnoticed. *This is a config bug independent of Chonkie.*
2. **All formatting is destroyed.** `' '.join(words)` collapses newlines, headings, bullet lists, and tables into one flat line. For an Obsidian vault that's structurally rich (headings, wikilinks, tables), the chunker throws away boundary cues that both the embedder and the LLM could use.
3. **No sentence/paragraph awareness** — chunks routinely split mid-sentence, which hurts embedding coherence and context readability.

### 2. What Chonkie is (⚠️ version/dep specifics unverified — web was blocked)

Chonkie is a lightweight, fast, RAG-focused chunking library (MIT). Its selling points versus a hand-rolled splitter:

- **Token-aware sizing** — chunk sizes are measured in *tokens*, not words, so you can size chunks to exactly fit `all-MiniLM-L6-v2`'s 256-token window. This directly fixes issue #1 above.
- **Multiple chunkers**: `TokenChunker` (fast, fixed-token), `SentenceChunker` (sentence boundaries), `RecursiveChunker` (hierarchical: paragraphs → sentences → words, with a markdown recipe), plus heavier `SemanticChunker` / `SDPMChunker` / `LateChunker` / `NeuralChunker` / `CodeChunker`.
- **Lightweight core** — the basic chunkers install small and fast; the semantic/neural ones pull heavier deps (sentence-transformers/torch) only via extras.
- **Pipeline extras** — Chefs (preprocessing), Refineries (overlap/embeddings), Porters (export), and **Handshakes** (write chunk→embed→store directly to a vector DB, including **Chroma and Qdrant** — the "built-in handshake" the task mentions).

### 3. Fit for Nova

**Good fit — the chunking core:**
- `RecursiveChunker` (markdown recipe) or `SentenceChunker`, token-sized to ~256, would fix all three weaknesses at once and is a **contained change to a single function** (`chunk_text`). It's upstream of Chroma, so it doesn't touch the FastAPI Golden Rule / Interface Stability at all.
- Chonkie returns `Chunk` objects (`.text`, `.token_count`, `.start_index`…). Nova would take only `.text` and keep its existing `[filename]\n` anchoring + metadata + upsert **exactly as-is** — nothing load-bearing changes.

**Poor fit — Chonkie's handshake/pipeline (the feature the task highlights):**
- The Chroma/Qdrant **Handshake is the one part Nova should *not* adopt.** Nova deliberately owns chunk→embed→store through `ingest.py` + its Chroma `HttpClient`, and the anchoring prefix + exact metadata schema are load-bearing (blending fix + graph/retrieval). A handshake would bypass both. So the headline "one-call chunk→embed→store" convenience is actively wrong for Nova's constraints — its value here is the *chunkers*, not the pipeline.

**Costs / risks:**
- New dependency → needs your approval (CLAUDE.md §9). Keep to Chonkie's light core; avoid the semantic/neural chunkers for v1.
- Re-chunking changes chunk boundaries and `doc_id`s → requires a **full `--full` re-ingest + re-embed** of the whole corpus (currently 479 chunks). Small in absolute terms, but it shifts the embedding-viz / blend-analysis baselines in Section 6.
- Follows the Phase 1.75 pattern well: gate it behind a `nova_config.json` flag so old vs. new chunking can be compared with `nova_benchmark.py --golden` before committing.

### 4. Recommendation

- **Not an urgent swap** — agrees with the task's own framing.
- **Do this first, regardless of Chonkie:** drop `chunk_size` to ~200 words (or make it token-aware). The 500-word-vs-256-token mismatch is a real, silent retrieval-quality loss and a ~2-line fix.
- **Chonkie is worth adopting for chunking specifically** when `ingest.py` chunking next gets revisited: use `RecursiveChunker`/`SentenceChunker`, token-sized to the embedder, feature-flagged, benchmark-gated, keeping Nova's anchoring + metadata + upsert untouched. **Skip the Handshake/Chef/Porter pipeline** — it conflicts with Nova's Interface Stability and blending fixes.

I did **not** change any code — this task's scope is the evaluation only, and both a dependency add and a chunk-size change need your go-ahead per CLAUDE.md.
