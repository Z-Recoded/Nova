# nova_sources.py
# Configuration for Nova's ingestion pipeline
# Add, remove, or edit sources to control what Nova ingests

import os

# Resolved relative to this file's own location, not hardcoded to the Aero's
# Windows path -- same bug class already fixed elsewhere in this project
# (86bb1pkpb). This file lives at the repo root, so its own directory IS the
# Nova source path.
NOVA_ROOT = os.path.dirname(os.path.abspath(__file__))

# Configures which directories ingest.py scans when building Nova's memory
SOURCES = [
    {
        "path": r"C:\Users\marvi\OneDrive\Documents\Second Brain",
        "project": "Second Brain",
        "description": "Obsidian vault — notes, projects, knowledge base"
    },
    {
        "path": NOVA_ROOT,
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
    "graphify-out",      # graphify's own derived graph/wiki output — machine-generated, not knowledge
    ".claude",           # Claude Code config dirs (settings.json etc.) — tooling config, not knowledge
]
