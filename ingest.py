# ingest.py
# Nova document ingestion pipeline
# Reads from nova_sources.py paths, embeds content into Chroma
# Supports --full flag for full re-ingest; default is incremental (changed files only)

import os
import re
import json
import sys
import chromadb
from chromadb.utils import embedding_functions
from transformers import AutoTokenizer
from nova_sources import SOURCES, SUPPORTED_EXTENSIONS, IGNORE_PATTERNS

# Windows consoles default to cp1252, which can't encode the checkmark/box
# characters in this file's progress output — force UTF-8 so a real re-ingest
# from a plain PowerShell or piped console doesn't crash mid-run (same fix
# already applied to nova_benchmark.py).
sys.stdout.reconfigure(encoding="utf-8")

MANIFEST_PATH = "C:/Nova/ingest_manifest.json"
CHROMA_HOST = "192.168.1.250"  # Chroma now runs as a standalone server on the Omen
CHROMA_PORT = 8000

# ── Chunking token budget ─────────────────────────────────────
# Chroma's DefaultEmbeddingFunction is all-MiniLM-L6-v2, which truncates
# input at 256 tokens. Chunking by word count silently overshot this (a
# real, measured bug: 69% of the corpus exceeded 256 tokens and lost its
# tail before embedding, because markdown/wikilinks tokenize at 1.8-3.3
# tokens/word). We now chunk by the SAME tokenizer the embedder uses, so a
# chunk can never be truncated at embed time.
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MAX_TOKENS = 256  # hard input cap of all-MiniLM-L6-v2
# ingest_file() anchors each chunk with a "[filename]\n" prefix (a Section 6
# blending fix) BEFORE embedding, and the model adds special tokens. The
# prefix's token cost varies per file (a few tokens for "Null.md", up to ~60
# for long hash-named .json files in the vault), so the per-chunk content
# budget is computed per file in content_token_budget(), not fixed here.
CHUNK_OVERLAP_TOKENS = 24  # ~10% overlap, matching the old 50/500 word ratio
BOUNDARY_SAFETY_TOKENS = 6  # absorb WordPiece merges at the prefix/chunk seam AND any drift between HF's tokenizer (used to size chunks) and Chroma's ONNX embedder tokenizer

# ── Setup ──────────────────────────────────────────────────────
client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = client.get_or_create_collection(
    name="nova_memory",
    embedding_function=embedding_fn
)
# Same tokenizer the embedder uses, so chunk_text() can size chunks in real
# tokens. Loaded lazily (not at import) so merely importing ingest.py — which
# nova_api.py does for its /ingest route — doesn't pay the tokenizer load or
# pull in a startup-time network dependency on the HF Hub; only actual
# ingestion (chunk_text/content_token_budget) needs it.
_tokenizer = None

def get_tokenizer():
    """Load the embedder's tokenizer on first use and cache it for reuse."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
    return _tokenizer

# ── Manifest (tracks last-modified times) ─────────────────────
def load_manifest() -> dict:
    if not os.path.exists(MANIFEST_PATH):
        return {}
    try:
        with open(MANIFEST_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

def file_changed(filepath: str, manifest: dict) -> bool:
    """Return True if file is new or has been modified since last ingest."""
    try:
        mtime = os.path.getmtime(filepath)
        return manifest.get(filepath) != mtime
    except Exception:
        return True

# ── Helpers ────────────────────────────────────────────────────
def should_ignore(path):
    for pattern in IGNORE_PATTERNS:
        if pattern in path:
            return True
    return False

def extract_links(content):
    """Extract Obsidian [[wikilinks]] from content."""
    return re.findall(r'\[\[([^\]]+)\]\]', content)

def content_token_budget(filename):
    """
    Tokens available for a chunk's own text after reserving room for the
    "[filename]\n" anchor (added in ingest_file before embedding) and the
    model's special tokens, so the final anchored chunk fits under
    EMBEDDING_MAX_TOKENS. Computed per file because the anchor's token cost
    depends on the filename's length.
    """
    prefix = f"[{filename}]\n"
    prefix_tokens = len(get_tokenizer().encode(prefix, add_special_tokens=True))
    return EMBEDDING_MAX_TOKENS - prefix_tokens - BOUNDARY_SAFETY_TOKENS

def chunk_text(text, max_tokens, overlap=CHUNK_OVERLAP_TOKENS):
    """
    Split text into overlapping chunks sized by real embedding tokens, not
    word count. Uses the same tokenizer as Chroma's embedder and slices the
    ORIGINAL text at token boundaries (via offset mapping), so two things
    hold that the old word-count version broke: no chunk is silently
    truncated at embed time, and the source formatting (newlines, headings,
    wikilinks) is preserved instead of being flattened by ' '.join().
    max_tokens is the per-file content budget from content_token_budget().
    """
    encoding = get_tokenizer()(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets = encoding["offset_mapping"]
    if not offsets:
        return []

    chunks = []
    step = max(1, max_tokens - overlap)
    for start in range(0, len(offsets), step):
        window = offsets[start:start + max_tokens]
        char_start = window[0][0]
        char_end = window[-1][1]
        chunk = text[char_start:char_end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def ingest_file(filepath, project, description):
    """Ingest a single file into Nova's memory."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        if not content.strip():
            return 0

        filename = os.path.basename(filepath)
        links = extract_links(content)
        chunks = chunk_text(content, content_token_budget(filename))

        for i, chunk in enumerate(chunks):
            anchored_chunk = f"[{filename}]\n{chunk}"
            doc_id = f"{filepath}::chunk{i}".replace("\\", "/")
            collection.upsert(
                documents=[anchored_chunk],
                ids=[doc_id],
                metadatas=[{
                    "source": filepath,
                    "filename": filename,
                    "project": project,
                    "description": description,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "links": str(links),
                }]
            )
        return len(chunks)

    except Exception as e:
        print(f"  Error ingesting {filepath}: {e}")
        return 0

# ── Main ingestion loop ────────────────────────────────────────
def run_ingestion(full: bool = False):
    mode = "full" if full else "incremental"
    print(f"Nova ingestion pipeline starting... ({mode} mode)\n")

    manifest = {} if full else load_manifest()
    updated_manifest = {}
    total_files = 0
    total_chunks = 0
    skipped = 0

    for source in SOURCES:
        path = source["path"]
        project = source["project"]
        description = source["description"]

        print(f"Source: {path}")
        print(f"Project: {project}\n")

        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not should_ignore(d)]

            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                filepath = os.path.join(root, filename)
                if should_ignore(filepath):
                    continue

                # Track mtime regardless of whether we ingest
                try:
                    mtime = os.path.getmtime(filepath)
                    updated_manifest[filepath] = mtime
                except Exception:
                    pass

                if not full and not file_changed(filepath, manifest):
                    skipped += 1
                    continue

                chunks = ingest_file(filepath, project, description)
                if chunks > 0:
                    print(f"  ✓ {filename} ({chunks} chunks)")
                    total_files += 1
                    total_chunks += chunks

    save_manifest(updated_manifest)

    print(f"\nIngestion complete ({mode} mode).")
    print(f"Files ingested: {total_files}")
    if not full:
        print(f"Files unchanged (skipped): {skipped}")
    print(f"Total chunks stored: {total_chunks}")
    print(f"Nova's memory is ready.\n")

if __name__ == "__main__":
    full_mode = "--full" in sys.argv
    run_ingestion(full=full_mode)
