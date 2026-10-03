# nova_blend_judge.py
# Answer-level cross-character error detector for Nova's RAG answers, judged by
# Claude. Replays relationship queries through nova_query.ask(), asks a no-tools
# Claude call whether the answer attributes something to the wrong character, and
# writes confirmed cross-character errors to training_flags.jsonl for
# nova_corrector.py to correct (the Lore Pairs pipeline).
#
# Why it exists: detect_blending() only fires on multi-file retrieval, which the
# $eq character filter now prevents, so the Lore pair count froze at 26 of 100.
# A name-matching answer-level rule was tried and rejected (2026-10-03: ~36% of
# answers flagged, almost all legitimate relationship mentions). A Claude judge
# found ~15% real cross-character errors in a 60-answer sample, concentrated in
# two-character "relationship" queries, where retrieval only fetches ONE of the
# two characters' files.
#
# Every verdict (not just errors) is also appended to logs/blend_judge_log.jsonl
# under a --label, so the same query set can be re-run before and after a
# retrieval change to measure whether the change actually helped.
#
# The judge call is structurally unable to write files (no `tools` argument).
# Flagged entries are tagged "origin": "synthetic_probe" like nova_blend_probe.py's.
#
# Usage:
#   python nova_blend_judge.py --list [--max-queries N] [--offset N]
#   python nova_blend_judge.py --run  [--max-queries N] [--offset N] [--label NAME]

import argparse
import itertools
import json
import os
import random
import re
from datetime import datetime

import anthropic
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))

# ── Constants ──────────────────────────────────────────────────
LOGS_DIR = os.path.join(_SCRIPT_DIR, "logs")
TRAINING_FLAGS_PATH = os.path.join(LOGS_DIR, "training_flags.jsonl")
JUDGE_LOG_PATH = os.path.join(LOGS_DIR, "blend_judge_log.jsonl")

JUDGE_MODEL = "claude-sonnet-4-6"  # same model nova_corrector.py uses
JUDGE_MAX_TOKENS = 1000
CHUNK_EXCERPT_CHARS = 300  # matches nova_logger.log_blend()'s excerpt length

FLAG_NAME = "answer_cross_character"
ORIGIN_TAG = "synthetic_probe"
VERDICT_CROSS_CHARACTER = "cross_character"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_SUPPORTED = "supported"
VERDICT_PARSE_ERROR = "parse_error"

DEFAULT_MAX_QUERIES = 20
DEFAULT_SEED = 0
DEFAULT_LABEL = "unlabeled"

# Keys of nova_query.CHARACTER_FILES minus the setting "symphony" (not a
# character). _check_characters_known() verifies this list against the real
# dict at run time, so a renamed key fails loudly instead of silently
# producing queries that never filter to a file.
RELATIONSHIP_CHARACTERS = [
    "null",
    "nullius",
    "helel",
    "raven",
    "fatale",
    "luci",
    "varas",
    "aseir",
    "beat",
    "rhythm",
    "felicity",
    "marisol",
    "kille",
]

RELATIONSHIP_TEMPLATES = [
    "Tell me about {a}'s relationship with {b}.",
    "How does {a} feel about {b}?",
    "What conflicts exist between {a} and {b}?",
    "How did {a} and {b} first meet?",
    "What does {a} think of {b}'s abilities?",
]

JUDGE_PROMPT = """You are auditing a small local model's answer against the source text it was given.

QUESTION:
{question}

SOURCE TEXT (the only ground truth):
{chunks}

ANSWER TO AUDIT:
{answer}

List every specific factual claim in the ANSWER that is contradicted by, or not supported by, the SOURCE TEXT.
Ignore style and ignore claims that are reasonable paraphrases. Pay special attention to attributes, abilities or
events attributed to the wrong character (cross-character blending).
Reply with ONLY JSON: {{"verdict": "supported" | "unsupported" | "cross_character",
"issues": [{{"claim": "...", "why": "..."}}]}}
"cross_character" = at least one issue is an attribute/event belonging to a different character than stated.
"unsupported" = issues exist but none are cross-character. "supported" = no issues."""


# ── Query building ─────────────────────────────────────────────
def build_relationship_queries(seed: int = DEFAULT_SEED) -> list[str]:
    """
    Expand every ordered pair of characters and every relationship template
    into one flat list, shuffled with a fixed seed so --offset can continue a
    previous run deterministically and pairs aren't clustered together.
    """
    queries = []
    for first, second in itertools.permutations(RELATIONSHIP_CHARACTERS, 2):
        for template in RELATIONSHIP_TEMPLATES:
            queries.append(template.format(a=first.capitalize(), b=second.capitalize()))
    random.Random(seed).shuffle(queries)  # nosec B311 - deterministic query ordering, not security-sensitive
    return queries


def _check_characters_known() -> None:
    """Raise if RELATIONSHIP_CHARACTERS has drifted from nova_query.CHARACTER_FILES."""
    from nova_query import CHARACTER_FILES

    unknown = [name for name in RELATIONSHIP_CHARACTERS if name not in CHARACTER_FILES]
    if unknown:
        raise ValueError(f"RELATIONSHIP_CHARACTERS not in nova_query.CHARACTER_FILES: {unknown}")


def _files_named_in_query(query: str) -> set[str]:
    """
    Every character file the query names, using the same word-boundary match
    ask() uses. Used so the corrector loads lore for BOTH characters in a
    relationship question, not just the one file retrieval happened to pick.
    """
    from nova_query import CHARACTER_FILES

    lowered = query.lower()
    return {filename for name, filename in CHARACTER_FILES.items() if re.search(rf"\b{re.escape(name)}\b", lowered)}


# ── Judging ────────────────────────────────────────────────────
def judge_answer(client: anthropic.Anthropic, query: str, chunk_text: str, answer: str) -> dict:
    """
    Ask Claude whether the answer misattributes anything against the source
    chunks. Returns {"verdict": ..., "issues": [...]}; a reply that can't be
    parsed as JSON comes back as verdict "parse_error" rather than raising.
    No `tools` argument, so the call cannot write anything.
    """
    message = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(question=query, chunks=chunk_text, answer=answer),
            }
        ],
    )
    reply = message.content[0].text
    json_match = re.search(r"\{.*\}", reply, re.S)
    if not json_match:
        return {"verdict": VERDICT_PARSE_ERROR, "issues": []}
    try:
        parsed = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return {"verdict": VERDICT_PARSE_ERROR, "issues": []}
    return {"verdict": parsed.get("verdict", VERDICT_PARSE_ERROR), "issues": parsed.get("issues", [])}


# ── Logging ────────────────────────────────────────────────────
def _append_jsonl(path: str, entry: dict) -> None:
    """Append one JSON object as one line, creating the logs directory if needed."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_verdict(label: str, query: str, retrieved_files: list[str], verdict: dict) -> None:
    """Record every verdict so a before/after rerun can compare error rates."""
    _append_jsonl(
        JUDGE_LOG_PATH,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "query": query,
            "retrieved": retrieved_files,
            "verdict": verdict["verdict"],
            "issue_count": len(verdict["issues"]),
        },
    )


def log_cross_character_flag(query: str, answer: str, chunks: list[dict], verdict: dict) -> None:
    """
    Write one confirmed cross-character error to training_flags.jsonl in the
    same shape nova_logger.log_blend() uses, so nova_corrector.py picks it up
    (correction left "" for it to fill). sources_mixed is the retrieved files
    plus every character the query names, so the corrector sees both
    characters' lore.
    """
    retrieved_files = {c["metadata"].get("filename", "unknown") for c in chunks}
    _append_jsonl(
        TRAINING_FLAGS_PATH,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "flag": FLAG_NAME,
            "category": "fiction",
            "sources_mixed": sorted(retrieved_files | _files_named_in_query(query)),
            "messages": [
                {"role": "user", "content": query},
                {"role": "assistant", "content": answer},
            ],
            "correction": "",
            "chunk_excerpts": [
                {
                    "filename": c["metadata"].get("filename", "unknown"),
                    "text": c["text"][:CHUNK_EXCERPT_CHARS],
                }
                for c in chunks
            ],
            "origin": ORIGIN_TAG,
            "judge_model": JUDGE_MODEL,
            "judge_issues": verdict["issues"],
        },
    )


# ── Running ────────────────────────────────────────────────────
def run_judge(max_queries: int, offset: int, seed: int, label: str) -> None:
    """
    Run a slice of the relationship queries through ask(), judge each answer,
    and log verdicts (every one) plus flagged entries (cross-character only).
    nova_query's log_query is silenced for this run so synthetic queries don't
    skew the Nova Log health dashboard's real-usage stats.
    """
    import nova_query

    _check_characters_known()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic(api_key=api_key)

    nova_query.log_query = lambda **_kwargs: None

    queries = build_relationship_queries(seed)[offset : offset + max_queries]
    counts = {VERDICT_SUPPORTED: 0, VERDICT_UNSUPPORTED: 0, VERDICT_CROSS_CHARACTER: 0, VERDICT_PARSE_ERROR: 0}

    for number, query in enumerate(queries, start=1):
        result = nova_query.ask(query, persist=False)
        chunks = result["chunks"]
        chunk_text = " ".join(c["text"] for c in chunks)
        verdict = judge_answer(client, query, chunk_text, result["answer"])

        counts[verdict["verdict"]] = counts.get(verdict["verdict"], 0) + 1
        log_verdict(label, query, result["sources"], verdict)
        if verdict["verdict"] == VERDICT_CROSS_CHARACTER:
            log_cross_character_flag(query, result["answer"], chunks, verdict)
        print(f"[{number}/{len(queries)}] {verdict['verdict']} | {query}", flush=True)

    total = len(queries)
    print(f"\nlabel={label} | {total} queries | {counts}")
    if total:
        print(f"cross-character rate: {counts[VERDICT_CROSS_CHARACTER]}/{total}")


# ── Main / entry point ─────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Find cross-character errors in Nova's answers with a Claude judge.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Print the queries and exit (no API or Chroma calls).")
    mode.add_argument("--run", action="store_true", help="Run queries through ask() and judge each answer.")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many queries (continue a prior run).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--label", default=DEFAULT_LABEL, help="Tag for the judge log, e.g. baseline_pre_fix.")
    args = parser.parse_args()

    if args.list:
        for query in build_relationship_queries(args.seed)[args.offset : args.offset + args.max_queries]:
            print(query)
        return

    run_judge(args.max_queries, args.offset, args.seed, args.label)


if __name__ == "__main__":
    main()
