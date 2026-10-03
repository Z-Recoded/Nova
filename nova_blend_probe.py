# nova_blend_probe.py
# Replays character-named queries through nova_query.ask() for the character
# pairs most at risk of blending (Section 6 of CLAUDE.md), so that real
# retrieval blends get logged to training_flags.jsonl by the normal
# log_blend() path and picked up by nova_corrector.py's cron.
#
# Why it exists: Lore DPO pairs only come from live queries that retrieve
# chunks from more than one character file. Query volume stopped on
# 2026-08-08, so the pair count froze at 26 of the 100 needed (2026-10-03).
#
# These are SYNTHETIC queries, not organic use. Every entry this script
# causes log_blend() to write is tagged "origin": "synthetic_probe" so it can
# be filtered out of the fine-tune set if only real traffic is wanted.
#
# Usage:
#   python nova_blend_probe.py --list                  # print queries, run nothing
#   python nova_blend_probe.py --run [--max-queries N] # run them through ask()

import argparse
import json
import os

# ── Constants ──────────────────────────────────────────────────
LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
TRAINING_FLAGS_PATH = os.path.join(LOGS_DIR, "training_flags.jsonl")
PROBE_ORIGIN_TAG = "synthetic_probe"
DEFAULT_MAX_QUERIES = 20

# Closest-by-embedding-distance pairs plus real blend history, CLAUDE.md
# Section 6 (re-run 2026-07-16). Keys are nova_query.CHARACTER_FILES keys.
AT_RISK_PAIRS = [
    ("helel", "luci"),
    ("null", "nullius"),
    ("beat", "rhythm"),
    ("aseir", "beat"),
    ("aseir", "luci"),
    ("fatale", "marisol"),
    ("helel", "raven"),
]

# Each template must name the first character so the query is
# character-filtered (the only case detect_blending() treats as a blend signal).
QUERY_TEMPLATES = [
    "Who is {a}?",
    "Describe {a}'s abilities.",
    "What is {a}'s personality like?",
    "Tell me about {a}'s relationship with {b}.",
]


# ── Query building ─────────────────────────────────────────────
def build_probe_queries() -> list[str]:
    """
    Expand every at-risk pair and template into a flat, de-duplicated list of
    query strings. Each pair is probed from both directions, since retrieval
    for "A" can pull in "B" without the reverse being true.
    """
    queries: list[str] = []
    for first, second in AT_RISK_PAIRS:
        for a, b in ((first, second), (second, first)):
            for template in QUERY_TEMPLATES:
                query = template.format(a=a.capitalize(), b=b.capitalize())
                if query not in queries:
                    queries.append(query)
    return queries


# ── Tagging ────────────────────────────────────────────────────
def _count_flag_lines() -> int:
    """Number of lines currently in training_flags.jsonl (0 if it doesn't exist)."""
    if not os.path.exists(TRAINING_FLAGS_PATH):
        return 0
    with open(TRAINING_FLAGS_PATH, encoding="utf-8") as f:
        return sum(1 for _ in f)


def _tag_new_flag_entries(lines_before: int) -> int:
    """
    Add the synthetic-probe origin tag to every entry appended after
    `lines_before`, leaving earlier (real) entries byte-for-byte untouched.
    Returns how many entries were tagged.
    """
    with open(TRAINING_FLAGS_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()

    new_lines = lines[lines_before:]
    tagged_lines = []
    for line in new_lines:
        entry = json.loads(line)
        entry["origin"] = PROBE_ORIGIN_TAG
        tagged_lines.append(json.dumps(entry, ensure_ascii=False))

    if not tagged_lines:
        return 0

    with open(TRAINING_FLAGS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[:lines_before] + tagged_lines) + "\n")
    return len(tagged_lines)


# ── Running ────────────────────────────────────────────────────
def run_probe(max_queries: int) -> None:
    """
    Run up to `max_queries` probe queries through ask() with persist=False
    (so conversation history isn't polluted), then tag whatever blend entries
    that produced. Imported lazily because nova_query connects to Chroma on
    import and --list shouldn't need it.
    """
    from nova_query import ask

    queries = build_probe_queries()[:max_queries]
    lines_before = _count_flag_lines()

    for number, query in enumerate(queries, start=1):
        result = ask(query, persist=False)
        print(f"[{number}/{len(queries)}] {query}  ->  category={result.get('category')}")

    tagged_count = _tag_new_flag_entries(lines_before)
    print(f"\n{tagged_count} blend(s) logged and tagged '{PROBE_ORIGIN_TAG}' from {len(queries)} queries.")


# ── Main / entry point ─────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Replay at-risk character queries to generate blend candidates.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Print the probe queries and exit.")
    mode.add_argument("--run", action="store_true", help="Run the queries through ask().")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    args = parser.parse_args()

    if args.list:
        for query in build_probe_queries()[: args.max_queries]:
            print(query)
        return

    run_probe(args.max_queries)


if __name__ == "__main__":
    main()
