# nova_sources.py
# Configuration for Nova's ingestion pipeline
# Add, remove, or edit sources to control what Nova ingests

# Configures which directories ingest.py scans when building Nova's memory
SOURCES = [
    {
        "path": r"C:\Users\marvi\OneDrive\Documents\Second Brain",
        "project": "Second Brain",
        "description": "Obsidian vault — notes, projects, knowledge base"
    },
    {
        "path": r"C:\Nova",
        "project": "Nova",
        "description": "Nova project source files and documentation"
    },
]

SUPPORTED_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".csv",
}

IGNORE_PATTERNS = [
    ".git",
    "__pycache__",
    "node_modules",
    ".obsidian",
    "nova-env",
    "memory",        # skip Chroma's own storage folder
    ".trash",
    ".DS_Store",
    "venv",
    ".venv",
    "logs",              # training flags, watcher logs — not knowledge
    "history.json",      # conversation history — changes constantly
    "ingest_manifest.json",
    "nova_graph.json",   # structured graph data — not prose
]
