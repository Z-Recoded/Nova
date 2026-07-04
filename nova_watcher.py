# nova_watcher.py
# Nova file watcher — monitors Second Brain for .md changes and triggers
# incremental ingest + graph rebuild automatically.
#
# Uses watchdog for cross-platform filesystem events.
# Debounce: ignores repeat events on the same file within 2 seconds
# (OneDrive/sync tools fire multiple events per save).
#
# Log: C:/Nova/logs/watcher.log
#
# Run:
#   cd C:/Nova
#   nova-env\Scripts\python nova_watcher.py
#
# Stop with Ctrl+C.

import os
import sys
import time
import threading
import logging
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from nova_sources import SOURCES
from ingest import ingest_file
from graph_builder import rebuild_node

# ── Config ─────────────────────────────────────────────────────
DEBOUNCE_SECONDS = 2.0
LOG_DIR = "C:/Nova/logs"
LOG_PATH = os.path.join(LOG_DIR, "watcher.log")
WATCH_EXTENSIONS = {".md"}

# Only watch source paths that contain the Second Brain / user notes.
# Excludes C:/Nova itself (source code changes shouldn't re-ingest).
WATCH_PATHS = [
    src["path"]
    for src in SOURCES
    if "Second Brain" in src["path"] or "second brain" in src["path"].lower()
]

# Fallback: if no Second Brain path is configured, watch all source paths
if not WATCH_PATHS:
    WATCH_PATHS = [src["path"] for src in SOURCES]

# Build a quick lookup: path prefix → (project, description)
SOURCE_MAP = {src["path"]: (src["project"], src["description"]) for src in SOURCES}


# ── Logging setup ──────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("nova_watcher")


# ── Source resolution ──────────────────────────────────────────

def _resolve_source(filepath: str) -> tuple[str, str]:
    """Return (project, description) for a file based on SOURCES config."""
    for prefix, (project, description) in SOURCE_MAP.items():
        if filepath.startswith(prefix):
            return project, description
    return "Unknown", ""


# ── Debouncer ──────────────────────────────────────────────────

class Debouncer:
    """
    Ensures the handler for a given filepath fires at most once every
    DEBOUNCE_SECONDS, even if watchdog fires multiple events rapidly.
    """

    def __init__(self, delay: float = DEBOUNCE_SECONDS):
        self._delay = delay
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def call_later(self, key: str, fn, *args, **kwargs):
        with self._lock:
            existing = self._timers.get(key)
            if existing:
                existing.cancel()
            timer = threading.Timer(self._delay, fn, args=args, kwargs=kwargs)
            self._timers[key] = timer
            timer.start()


debouncer = Debouncer()


# ── Event handler ──────────────────────────────────────────────

def _handle_change(filepath: str) -> None:
    """
    Called (debounced) when a watched .md file changes.
    1. Incremental ingest for that file.
    2. Graph rebuild_node for that file.
    3. Log result.
    """
    filename = os.path.basename(filepath)
    project, description = _resolve_source(filepath)

    log.info(f"Change detected: {filename}  (project={project})")
    t0 = time.monotonic()

    # Step 1: ingest
    try:
        chunks = ingest_file(filepath, project, description)
    except Exception as e:
        log.error(f"Ingest failed for {filename}: {e}")
        chunks = 0

    # Step 2: graph rebuild
    total_edges = 0
    outgoing_edges = 0
    try:
        graph = rebuild_node(filepath)
        total_edges = len(graph.get("edges", []))
        outgoing_edges = sum(1 for e in graph.get("edges", []) if e["source"] == filename)
    except Exception as e:
        log.error(f"Graph rebuild failed for {filename}: {e}")

    elapsed = time.monotonic() - t0
    log.info(
        f"  ✓ {filename} | chunks={chunks} | outgoing_edges={outgoing_edges} "
        f"| total_edges={total_edges} | {elapsed:.2f}s"
    )


class NovaWatchHandler(FileSystemEventHandler):
    """Watchdog event handler — fires on file create/modify."""

    def on_modified(self, event):
        if event.is_directory:
            return
        if os.path.splitext(event.src_path)[1].lower() in WATCH_EXTENSIONS:
            debouncer.call_later(event.src_path, _handle_change, event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        if os.path.splitext(event.src_path)[1].lower() in WATCH_EXTENSIONS:
            debouncer.call_later(event.src_path, _handle_change, event.src_path)

    def on_moved(self, event):
        # Treat a rename as a change to the destination
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", None)
        if dest and os.path.splitext(dest)[1].lower() in WATCH_EXTENSIONS:
            debouncer.call_later(dest, _handle_change, dest)


# ── Main ───────────────────────────────────────────────────────

def main():
    if not WATCH_PATHS:
        log.error("No watch paths configured in nova_sources.py. Exiting.")
        sys.exit(1)

    handler = NovaWatchHandler()
    observer = Observer()

    for watch_path in WATCH_PATHS:
        if not os.path.isdir(watch_path):
            log.warning(f"Watch path not found (skipping): {watch_path}")
            continue
        observer.schedule(handler, watch_path, recursive=True)
        log.info(f"Watching: {watch_path}")

    observer.start()
    log.info(f"Nova watcher running. Debounce={DEBOUNCE_SECONDS}s. Log → {LOG_PATH}")
    log.info("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping watcher...")
        observer.stop()

    observer.join()
    log.info("Nova watcher stopped.")


if __name__ == "__main__":
    main()
