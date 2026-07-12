# nova_chroma_omen_check.py
# Standalone, throwaway diagnostic script -- confirms whether the just-migrated
# Chroma-on-Omen setup (192.168.1.250) is actually reachable and returns real
# data, before nova_query.py/graph_builder.py/ingest.py get pointed at it for
# real. Does NOT modify any existing Nova file.
#
# Current setup in nova_query.py, graph_builder.py, and ingest.py (all three
# match exactly, checked directly):
#   chromadb.PersistentClient(path="C:/Nova/memory")
#   embedding_functions.DefaultEmbeddingFunction()   (from chromadb.utils)
#   collection name "nova_memory"
# nova_memory_store.py does NOT touch Chroma at all -- it's plain JSON
# conversation history at C:/Nova/history.json (HISTORY_PATH), unrelated to
# the vector store. Nothing there to migrate or point anywhere.
#
# KNOWN GOTCHA THIS SCRIPT GUARDS AGAINST: chromadb's get_or_create_collection()
# silently CREATES a new empty collection if the name doesn't already exist on
# the target server -- that would make a totally broken connection look like a
# "successful" query that just happens to return zero results. This script uses
# get_collection() instead (raises if the collection doesn't already exist) to
# turn that specific silent-failure mode into a loud one, and separately treats
# a zero-count collection or a zero-result query as a FAIL to investigate, not
# a pass.

import socket
import sys

OMEN_HOST = "192.168.1.250"
CHROMA_HTTP_PORT = 8000  # chromadb's own default standalone-server port (`chroma run`)
COLLECTION_NAME = "nova_memory"
# Same query CLAUDE.md documents as the known-good /context-budget verification
# query (Section 5) -- reusing it here gives a sanity check against a
# previously-confirmed-working baseline, not an arbitrary new string.
TEST_QUERY = "Tell me about Null"
N_RESULTS = 3
TCP_PROBE_TIMEOUT_SECONDS = 5


def _tcp_reachable(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT_SECONDS) -> bool:
    """Raw socket check -- distinguishes 'nothing listening at all' from a Chroma-level error."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print(f"--- Step 1: raw TCP reachability check, {OMEN_HOST}:{CHROMA_HTTP_PORT} ---")
    if not _tcp_reachable(OMEN_HOST, CHROMA_HTTP_PORT):
        print(
            f"FAIL: nothing is listening on {OMEN_HOST}:{CHROMA_HTTP_PORT} from this machine (the Aero).\n"
            f"This is an infrastructure gap, not a code bug: either Chroma isn't running as a server\n"
            f"process on the Omen yet, it's bound to a different port, or a firewall is blocking it.\n"
            f"Nothing further to test until a Chroma server is actually up and reachable on that port."
        )
        return 1
    print(f"OK: {OMEN_HOST}:{CHROMA_HTTP_PORT} is accepting TCP connections.")

    print("\n--- Step 2: chromadb.HttpClient connection + heartbeat ---")
    import chromadb
    from chromadb.utils import embedding_functions

    try:
        client = chromadb.HttpClient(host=OMEN_HOST, port=CHROMA_HTTP_PORT)
        client.heartbeat()
    except Exception as e:
        print(
            f"FAIL: TCP port is open but the Chroma HTTP API didn't respond as expected: {e}\n"
            f"Something is listening on that port, but it doesn't look like a Chroma server (or it's\n"
            f"an incompatible Chroma version). Not a client-side code bug -- infra/version mismatch."
        )
        return 1
    print("OK: Chroma server responded to heartbeat.")

    print(f"\n--- Step 3: fetch the '{COLLECTION_NAME}' collection (get_collection, not get_or_create) ---")
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    try:
        collection = client.get_collection(name=COLLECTION_NAME, embedding_function=embedding_fn)
    except Exception as e:
        print(
            f"FAIL: collection '{COLLECTION_NAME}' does not exist on this server: {e}\n"
            f"The server is reachable, but the migrated data either isn't there under this exact\n"
            f"name, or the migration didn't actually copy the Chroma data directory over. Deliberately\n"
            f"did NOT use get_or_create_collection() here -- that call would have silently created a\n"
            f"new EMPTY collection with this name instead of raising, which would have made this look\n"
            f"like a false pass."
        )
        return 1

    count = collection.count()
    print(f"OK: collection found, count() reports {count} stored chunks.")
    if count == 0:
        print("FAIL: collection exists but is empty -- treating this as a failure to investigate, not a pass.")
        return 1

    print(f"\n--- Step 4: real query against '{COLLECTION_NAME}' (query='{TEST_QUERY}', n_results={N_RESULTS}) ---")
    try:
        results = collection.query(query_texts=[TEST_QUERY], n_results=N_RESULTS)
    except Exception as e:
        print(f"FAIL: query() raised: {e}")
        return 1

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not documents:
        print(
            "FAIL: query returned zero results despite a non-empty collection. This is exactly the\n"
            "silent-failure mode flagged up front -- investigate before wiring anything else to this\n"
            "endpoint (mismatched embedding function/config between ingest-time and query-time is the\n"
            "most likely cause)."
        )
        return 1

    print(f"OK: query returned {len(documents)} real result(s):\n")
    for i, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances), start=1):
        preview = doc[:200].replace("\n", " ")
        print(f"  [{i}] distance={dist:.4f}  metadata={meta}")
        print(f"      {preview}{'...' if len(doc) > 200 else ''}")

    print("\nPASS: Chroma on the Omen is reachable, holds the nova_memory collection, and returns real results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
