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
from nova_sources import SOURCES, SUPPORTED_EXTENSIONS, IGNORE_PATTERNS

MANIFEST_PATH = "C:/Nova/ingest_manifest.json"
CHROMA_HOST = "192.168.1.250"  # Chroma now runs as a standalone server on the Omen
CHROMA_PORT = 8000

# ── Setup ──────────────────────────────────────────────────────
client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = client.get_or_create_collection(
    name="nova_memory",
    embedding_function=embedding_fn
)

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

def chunk_text(text, chunk_size=500, overlap=50):
    """Split text into overlapping chunks for better retrieval."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = ' '.join(words[i:i + chunk_size])
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
        chunks = chunk_text(content)

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
