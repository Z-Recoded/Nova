# nova_pull_exercism_corpus.py
# Vendors a fixed, difficulty-stratified subset of the canonical Exercism
# Python track into data/coding_specialist_eval/exercism_subset/ -- the
# shared exercise corpus scoped for both 86bbch988 (edit-format test plan)
# and 86bbch95y (ACI parameter tuning), per
# docs/coding-specialist-exercise-corpus-plan.md.
#
# Pulls from github.com/exercism/python (MIT-licensed, the real upstream
# Aider's own benchmark docs cite -- not the Aider-AI/polyglot-benchmark
# repo's 34-exercise "hardest" subset, which is a narrower, harder-skewed
# curation, not the full-breadth track this project wants for a difficulty
# sweep). Pinned to a specific commit SHA (EXERCISM_PYTHON_COMMIT) rather
# than "main" -- upstream exercises can be edited or removed over time, and
# this corpus needs to stay byte-identical across every future test run
# that references it.
#
# Usage:
#   python nova_pull_exercism_corpus.py           # fetch the real subset
#   python nova_pull_exercism_corpus.py --dry-run  # print the plan, no network calls

import argparse
import json
import os
import sys
import time
import urllib.request

# ── Config ─────────────────────────────────────────────────────

EXERCISM_PYTHON_COMMIT = "1f6aab8667bf653b10cc3799f94352fcdb749db6"  # pinned 2026-08-15, real HEAD as of 2026-08-10
RAW_BASE_URL = f"https://raw.githubusercontent.com/exercism/python/{EXERCISM_PYTHON_COMMIT}"
API_CONTENTS_URL = (
    f"https://api.github.com/repos/exercism/python/contents/exercises/practice/{{slug}}?ref={EXERCISM_PYTHON_COMMIT}"
)

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "coding_specialist_eval", "exercism_subset"
)

# Real difficulty-stratified sample (seed=2026, weighted toward the common
# easy/medium tiers while still including every exercise at the two rarest
# tiers, 8 and 9, which have exactly one real exercise each) -- see
# docs/coding-specialist-exercise-corpus-plan.md for the full selection
# methodology and the real difficulty distribution (140 exercises total,
# tiers 1-9) this was sampled from.
SELECTED_EXERCISES = {
    1: ["bob", "list-ops", "raindrops", "secret-handshake", "space-age", "two-fer"],
    2: ["luhn", "nth-prime", "proverb", "scrabble-score", "yacht"],
    3: ["binary", "crypto-square", "error-handling", "octal", "poker"],
    4: ["all-your-base", "complex-numbers", "ledger", "meetup", "rail-fence-cipher"],
    5: ["binary-search-tree", "bowling", "zebra-puzzle"],
    6: ["affine-cipher", "two-bucket"],
    7: ["dominoes", "sgf-parsing"],
    8: ["rest-api"],
    9: ["pov"],
}
EXPECTED_EXERCISE_COUNT = 30

# Real upstream rate limit: unauthenticated GitHub API calls are capped at
# 60/hour. _fetch_full_repo_tree() uses exactly 1 of those (see its own
# docstring for the real per-exercise-recursion bug this replaced); every
# per-file download after that goes through raw.githubusercontent.com, which
# is not API-rate-limited the same way. A small delay between exercises is
# still polite to that CDN, not required for correctness.
REQUEST_DELAY_SECONDS = 0.3


# ── Fetch helpers ──────────────────────────────────────────────
def _fetch_full_repo_tree() -> list[str]:
    """
    Every real file path in the whole exercism/python repo at
    EXERCISM_PYTHON_COMMIT, via ONE call to GitHub's recursive git-trees API.

    Real bug found live (2026-08-15): the original implementation walked
    each exercise's directory tree with its own recursive Contents API calls
    (one call per directory level -- exercise root + .docs/ + .meta/, so
    ~3 calls/exercise, ~90 total for 30 exercises) and hit GitHub's
    unauthenticated 60-calls/hour limit partway through a real run (9 of 30
    exercises fetched before a 403). This module's own comment had
    underestimated the real call count as "~30" -- it never accounted for
    the nested-directory recursion. The git-trees API with `recursive=1`
    returns the ENTIRE repo's file listing in one call regardless of how
    many exercises are selected, so this is the correct fix, not just a
    slower workaround -- it makes the real call count constant (1), not
    proportional to corpus size.
    """
    url = f"https://api.github.com/repos/exercism/python/git/trees/{EXERCISM_PYTHON_COMMIT}?recursive=1"
    with urllib.request.urlopen(url) as response:  # nosec B310 -- hardcoded https:// URL, no user input
        tree = json.loads(response.read())
    if tree.get("truncated"):
        raise RuntimeError(
            "GitHub's tree API truncated the response -- the repo is too large for a single "
            "recursive listing. Would need per-directory fallback, not silently accept partial data."
        )
    return [entry["path"] for entry in tree["tree"] if entry["type"] == "blob"]


# Real subdirectory types found under every one of these exercises (checked
# live across all 30, not assumed): .docs/ and .meta/, which the plan
# accounted for -- but also .approaches/ (multiple worked ALTERNATE solutions
# with full explanations) and .articles/ (deep-dive content, including a full
# working benchmark solution). Excluded deliberately: these carry real
# solution code well beyond the single .meta/example.py the plan named as
# "never shown to the model," and add nothing this harness needs (no
# .docs/.meta = extraneous). Found live on the first real fetch: raindrops
# alone pulled 29 files instead of the ~5-6 expected -- 98 of 311 total files
# across the corpus (~31%) were .approaches/.articles content before this fix.
EXCLUDED_SUBDIR_PREFIXES = (".approaches/", ".articles/")


def _list_exercise_files(slug: str, full_tree: list[str]) -> list[str]:
    """
    Filters the already-fetched full repo tree down to just the files this
    corpus actually needs under one exercise's directory -- .docs/ and
    .meta/ only, excluding EXCLUDED_SUBDIR_PREFIXES.
    """
    prefix = f"exercises/practice/{slug}/"
    matched = [path for path in full_tree if path.startswith(prefix)]
    return [
        path
        for path in matched
        if not any(path[len(prefix) :].startswith(excluded) for excluded in EXCLUDED_SUBDIR_PREFIXES)
    ]


def _download_file(repo_path: str, local_path: str) -> None:
    """Fetches one real file's raw content and writes it to local_path, creating parent dirs as needed."""
    url = f"{RAW_BASE_URL}/{repo_path}"
    # RAW_BASE_URL is a hardcoded https:// prefix; repo_path (from GitHub's own tree API
    # response) can only extend the path component below, never switch the scheme.
    with urllib.request.urlopen(url) as response:  # nosec B310
        content = response.read()
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(content)


# ── Main ───────────────────────────────────────────────────────
def pull_corpus(dry_run: bool = False) -> None:
    """
    Fetches every file for every selected exercise into OUTPUT_DIR, mirroring
    each exercise's real upstream layout exactly (exercises/practice/<slug>/...
    becomes exercism_subset/<slug>/...) so downstream tooling reading this
    corpus doesn't need any custom parsing beyond what the real Exercism
    format already requires.
    """
    total = sum(len(slugs) for slugs in SELECTED_EXERCISES.values())
    if total != EXPECTED_EXERCISE_COUNT:
        raise RuntimeError(
            f"SELECTED_EXERCISES has {total} entries, expected {EXPECTED_EXERCISE_COUNT} -- fix the list."
        )

    print(f"Pinned commit: {EXERCISM_PYTHON_COMMIT}")
    print("Fetching the full repo tree (1 API call)...")
    full_tree = _fetch_full_repo_tree()
    print(f"Fetching {total} exercise(s) into {OUTPUT_DIR}\n")

    for difficulty in sorted(SELECTED_EXERCISES):
        for slug in SELECTED_EXERCISES[difficulty]:
            exercise_files = _list_exercise_files(slug, full_tree)
            print(f"[difficulty {difficulty}] {slug} ({len(exercise_files)} file(s))")
            if not exercise_files:
                raise RuntimeError(f"No files found for '{slug}' in the real repo tree -- check the slug is correct.")
            if dry_run:
                continue

            for repo_path in exercise_files:
                # repo_path looks like "exercises/practice/bob/.docs/instructions.md" --
                # strip the "exercises/practice/" prefix so the local layout is just
                # "<slug>/.docs/instructions.md", not nested under redundant parent dirs.
                relative_path = repo_path.split("exercises/practice/", 1)[1]
                local_path = os.path.join(OUTPUT_DIR, relative_path)
                _download_file(repo_path, local_path)
            time.sleep(REQUEST_DELAY_SECONDS)

    if dry_run:
        print(f"\nDry run — {total} exercise(s) would be fetched, no network calls made.")
        return

    _write_attribution_notice()
    print(f"\nDone — {total} exercise(s) written to {OUTPUT_DIR}")


def _write_attribution_notice() -> None:
    """
    Real third-party content is being vendored here -- writes a NOTICE file
    recording the real source, license, and pinned commit, so provenance
    isn't left to a commit message alone.
    """
    notice_path = os.path.join(OUTPUT_DIR, "NOTICE.md")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(notice_path, "w", encoding="utf-8") as f:
        f.write(
            "# Attribution\n\n"
            "This directory vendors a subset of exercises from the Exercism Python "
            "track: https://github.com/exercism/python\n\n"
            f"Pinned commit: `{EXERCISM_PYTHON_COMMIT}`\n\n"
            "Exercise content is copyright (c) Exercism and used under Exercism's "
            "MIT license (https://github.com/exercism/python/blob/main/LICENSE).\n\n"
            "Selection: a difficulty-stratified sample (30 exercises spanning real "
            "difficulty tiers 1-9), not a random or exhaustive pull -- see "
            "docs/coding-specialist-exercise-corpus-plan.md for the full methodology.\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull the shared coding-specialist exercise corpus from Exercism.")
    parser.add_argument("--dry-run", action="store_true", help="Print the fetch plan without making network calls.")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pull_corpus(dry_run=args.dry_run)
