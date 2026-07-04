# graph_builder.py
# Nova graph layer — builds a node/edge map from Chroma wikilink metadata.
#
# Graph lives at C:/Nova/nova_graph.json
# Nodes:  { id, title, project, chunk_count }
# Edges:  { source, target, link_text }
#
# Public API:
#   build_graph()             — full rebuild from all Chroma chunks
#   rebuild_node(filepath)    — incremental: re-derive one file's edges
#   get_neighbors(filename)   — outgoing + incoming edges for a file
#   get_context_budget(query, n_seeds, n_files) — ranked file list for a query

import ast
import json
import os
from collections import defaultdict, deque

import chromadb
from chromadb.utils import embedding_functions

GRAPH_PATH = "C:/Nova/nova_graph.json"

# ── Chroma setup ───────────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path="C:/Nova/memory")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="nova_memory",
    embedding_function=embedding_fn,
)


# ── Internal helpers ───────────────────────────────────────────

def _parse_links(links_str: str) -> list[str]:
    """
    Parse the stringified list that ingest.py stores in metadata.
    e.g. "['My Note', 'Other File']"  →  ['My Note', 'Other File']
    Returns [] on any parse failure.
    """
    if not links_str or links_str in ("[]", ""):
        return []
    try:
        result = ast.literal_eval(links_str)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _wikilink_to_filename(link_text: str) -> str:
    """
    Convert a raw wikilink target to the filename Nova would store.
    [[My Note]]        → 'My Note.md'
    [[My Note|alias]]  → 'My Note.md'   (strip alias)
    [[My Note.md]]     → 'My Note.md'   (already has extension)
    """
    # Strip alias (Obsidian pipe syntax)
    link_text = link_text.split("|")[0].strip()
    if not link_text.lower().endswith(".md"):
        link_text = link_text + ".md"
    return link_text


def _fetch_all_chunks() -> list[dict]:
    """
    Pull every chunk from Chroma.
    Returns list of metadata dicts (one per chunk).
    """
    # Chroma requires a query or get — use get() with no filter to pull all.
    # We only need metadatas, not documents/embeddings.
    results = collection.get(include=["metadatas"])
    return results.get("metadatas", [])


def _build_graph_from_chunks(metadatas: list[dict]) -> dict:
    """
    Given a list of chunk metadata dicts, produce the graph dict:
        {
          "nodes": [ {id, title, project, chunk_count}, ... ],
          "edges": [ {source, target, link_text}, ... ],
        }
    """
    # Aggregate per file
    file_data: dict[str, dict] = {}   # filename → {project, chunk_count, links_seen}

    for meta in metadatas:
        filename = meta.get("filename", "")
        if not filename:
            continue

        if filename not in file_data:
            file_data[filename] = {
                "project": meta.get("project", ""),
                "chunk_count": 0,
                "links_seen": set(),     # raw link texts found in any chunk
            }

        file_data[filename]["chunk_count"] += 1

        links = _parse_links(meta.get("links", "[]"))
        for lk in links:
            file_data[filename]["links_seen"].add(lk)

    # Build nodes
    nodes = []
    for filename, data in file_data.items():
        title = os.path.splitext(filename)[0]
        nodes.append({
            "id": filename,
            "title": title,
            "project": data["project"],
            "chunk_count": data["chunk_count"],
        })

    # Build edges
    known_files = {n["id"] for n in nodes}
    edges = []
    seen_edges: set[tuple] = set()

    for filename, data in file_data.items():
        for raw_link in data["links_seen"]:
            target = _wikilink_to_filename(raw_link)
            # Only add edge if the target file exists in the graph
            if target in known_files and target != filename:
                key = (filename, target)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "source": filename,
                        "target": target,
                        "link_text": raw_link,
                    })

    return {"nodes": nodes, "edges": edges}


def _load_graph() -> dict:
    """Load nova_graph.json from disk; return empty graph if missing."""
    if not os.path.exists(GRAPH_PATH):
        return {"nodes": [], "edges": []}
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nodes": [], "edges": []}


def _save_graph(graph: dict) -> None:
    with open(GRAPH_PATH, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)


# ── Public API ─────────────────────────────────────────────────

def build_graph() -> dict:
    """
    Full rebuild: pull all Chroma chunks, derive nodes + edges, write to disk.
    Returns the graph dict.
    """
    metadatas = _fetch_all_chunks()
    graph = _build_graph_from_chunks(metadatas)
    _save_graph(graph)
    print(f"[graph_builder] Full rebuild: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges → {GRAPH_PATH}")
    return graph


def rebuild_node(filepath: str) -> dict:
    """
    Incremental update for a single file.

    - Removes the file's existing node + all its outgoing edges from the graph.
    - Fetches fresh chunks for that file from Chroma.
    - Re-derives the node + outgoing edges and merges them back in.
    - Saves the updated graph to disk.

    Returns the updated graph dict.
    """
    filename = os.path.basename(filepath)
    graph = _load_graph()

    # Count edges before for logging
    old_edge_count = len(graph["edges"])

    # Drop old node and its outgoing edges
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != filename]
    graph["edges"] = [e for e in graph["edges"] if e["source"] != filename]

    # Pull fresh chunks for this file from Chroma
    results = collection.get(
        where={"filename": {"$eq": filename}},
        include=["metadatas"],
    )
    metadatas = results.get("metadatas", [])

    if not metadatas:
        # File was deleted / has no chunks — just save the pruned graph
        _save_graph(graph)
        removed = old_edge_count - len(graph["edges"])
        print(f"[graph_builder] rebuild_node({filename}): node removed, {removed} edges dropped")
        return graph

    # Aggregate for this one file
    chunk_count = len(metadatas)
    project = metadatas[0].get("project", "")
    links_seen: set[str] = set()
    for meta in metadatas:
        for lk in _parse_links(meta.get("links", "[]")):
            links_seen.add(lk)

    # Add updated node
    title = os.path.splitext(filename)[0]
    graph["nodes"].append({
        "id": filename,
        "title": title,
        "project": project,
        "chunk_count": chunk_count,
    })

    # Add updated outgoing edges (only to known files)
    known_files = {n["id"] for n in graph["nodes"]}
    new_edges = []
    for raw_link in links_seen:
        target = _wikilink_to_filename(raw_link)
        if target in known_files and target != filename:
            new_edges.append({
                "source": filename,
                "target": target,
                "link_text": raw_link,
            })

    graph["edges"].extend(new_edges)
    _save_graph(graph)

    added = len(new_edges)
    removed = old_edge_count - (len(graph["edges"]) - added)
    print(f"[graph_builder] rebuild_node({filename}): {chunk_count} chunks, +{added} edges, -{removed} edges")
    return graph


def get_neighbors(filename: str) -> dict:
    """
    Return outgoing and incoming edges for `filename`.

    {
      "file": filename,
      "outgoing": [ {target, link_text}, ... ],
      "incoming": [ {source, link_text}, ... ],
    }
    """
    graph = _load_graph()
    outgoing = [
        {"target": e["target"], "link_text": e["link_text"]}
        for e in graph["edges"]
        if e["source"] == filename
    ]
    incoming = [
        {"source": e["source"], "link_text": e["link_text"]}
        for e in graph["edges"]
        if e["target"] == filename
    ]
    return {"file": filename, "outgoing": outgoing, "incoming": incoming}


def get_context_budget(
    query: str,
    n_seeds: int = 8,
    n_files: int = 15,
    max_hops: int = 2,
) -> list[str]:
    """
    Return a ranked list of filenames relevant to `query`.

    Strategy:
      1. Quick semantic search (n_seeds chunks) to find seed files.
      2. BFS over the graph from each seed file (up to max_hops).
      3. Rank: seeds first (by distance ascending), then neighbors by hop count.
      4. Return up to n_files filenames.

    Falls back to [] if Chroma or graph is empty (caller does unfiltered search).
    """
    # ── Seed search ────────────────────────────────────────────
    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_seeds,
            include=["metadatas", "distances"],
        )
    except Exception:
        return []

    if not results["metadatas"] or not results["metadatas"][0]:
        return []

    # Map filename → best (lowest) distance from semantic search
    seed_scores: dict[str, float] = {}
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        fn = meta.get("filename", "")
        if fn and (fn not in seed_scores or dist < seed_scores[fn]):
            seed_scores[fn] = dist

    if not seed_scores:
        return []

    # ── Graph BFS ──────────────────────────────────────────────
    graph = _load_graph()
    if not graph["nodes"]:
        # Graph not built yet — return seeds only
        return sorted(seed_scores, key=lambda f: seed_scores[f])[:n_files]

    # Build adjacency (undirected: both outgoing and incoming count as neighbours)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph["edges"]:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])

    # BFS from each seed
    hop_dist: dict[str, int] = {}   # filename → min hop distance from any seed
    queue: deque[tuple[str, int]] = deque()

    for seed in seed_scores:
        if seed not in hop_dist:
            hop_dist[seed] = 0
            queue.append((seed, 0))

    while queue:
        node, depth = queue.popleft()
        if depth >= max_hops:
            continue
        for neighbor in adjacency.get(node, set()):
            if neighbor not in hop_dist:
                hop_dist[neighbor] = depth + 1
                queue.append((neighbor, depth + 1))

    # ── Rank ───────────────────────────────────────────────────
    # Seeds first, sorted by semantic distance; then BFS neighbours by hop count
    seeds = sorted(seed_scores.keys(), key=lambda f: seed_scores[f])
    neighbours = sorted(
        (f for f in hop_dist if f not in seed_scores),
        key=lambda f: hop_dist[f],
    )

    ranked = seeds + neighbours
    return ranked[:n_files]


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        rebuild_node(sys.argv[1])
    else:
        build_graph()
