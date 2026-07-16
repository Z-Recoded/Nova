# Nova Usage Logger — local Claude Code usage/cost history + activity-profile tracker.
#
# Scans every local Claude Code session transcript (~/.claude/projects/**/*.jsonl,
# across ALL projects, since usage draws from one account-wide subscription pool,
# not a per-project one) and aggregates token usage + estimated cost by calendar
# day. Full re-scan every run — the output file is a derived artifact, fully
# regenerated each time, matching nova_status_digest.py's convention rather than
# an append-only log.
#
# Built for the usage-history-baseline component of 86bawx7vj (headless Nova
# coding runner) — no live quota-forecast API exists for Claude Code, so this is
# the self-tracked substitute the task calls for.
#
# Also builds a second derived artifact from the same transcripts: an hour-of-day
# x day-of-week activity histogram (see build_activity_profile()), windowed to
# the last ACTIVITY_PROFILE_WINDOW_DAYS days. This is groundwork for the
# autonomous-dispatch dual-fuel design (86bawpvzz) — finding genuine "Marvin is
# away from Claude Code" windows instead of guessing a fixed reserve percentage.
# claude.ai chat activity isn't included — that data isn't accessible without a
# Claude Enterprise plan (checked directly against Anthropic's Usage/Cost and
# Enterprise Analytics API docs), so Claude Code activity is the realistic proxy.

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Windows consoles default to cp1252, which can't encode the em-dash in this
# script's printed summaries — a recurring gotcha in this repo (nova_benchmark.py,
# browser_hands). Reconfigure stdout to UTF-8 rather than avoid the character.
sys.stdout.reconfigure(encoding="utf-8")

# ── Constants ──

PROJECTS_DIR = Path.home() / ".claude" / "projects"
OUTPUT_PATH = Path(__file__).resolve().parent / "logs" / "claude_usage_history.json"
ACTIVITY_PROFILE_OUTPUT_PATH = Path(__file__).resolve().parent / "logs" / "claude_activity_profile.json"

# How far back the activity profile looks when bucketing messages by local
# hour-of-day/day-of-week. Windowed (not full-history) on purpose — the
# profile answers "what does Marvin's *current* schedule look like" for
# autonomous-dispatch idle-window detection, and old data would dilute a
# real schedule shift (job change, new sleep pattern) for months. 60 days
# gives ~8-9 samples per weekday/hour cell, enough to separate signal from
# single-day noise.
ACTIVITY_PROFILE_WINDOW_DAYS = 60

# Where to push this machine's daily aggregate for cross-machine centralization
# (see nova_api.py's POST /usage-history). Defaults to the Omen's permanent
# Tailscale address — the real centralization target from ANY machine,
# including the Omen itself (pushing to its own Tailscale IP works the same
# as localhost would, just through the tailscale0 interface). No more
# per-machine env var overrides needed — this is what makes it safe to run
# the same hook command on every machine, worktree sessions included.
NOVA_API_URL = os.environ.get("NOVA_API_URL", "http://100.114.197.117:8001")

# Identifies which machine pushed this data, e.g. "zeed" (Aero) or "nova" (Omen)
# — matches this project's existing Tailscale hostname convention.
SOURCE_MACHINE = socket.gethostname().lower()

CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0
CACHE_READ_MULTIPLIER = 0.1

# Claude Sonnet 5 has introductory pricing through this date (inclusive);
# after it, PRICING_PER_MILLION_TOKENS's standard rates apply instead.
SONNET_5_INTRO_CUTOFF_DATE = "2026-08-31"

# $ per 1M tokens. Source: claude-api skill, cached 2026-06-24.
PRICING_PER_MILLION_TOKENS = {
    "claude-fable-5": {"input": 10.00, "output": 50.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        "input_intro": 2.00,
        "output_intro": 10.00,
    },
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# Internal Claude Code markers that show up as a "model" in transcripts but
# aren't real billable models — skip these rather than flagging them as
# pricing gaps.
NON_BILLABLE_MODEL_MARKERS = {"<synthetic>"}

# Matches datetime.weekday()'s convention (Monday=0..Sunday=6), used to label
# build_activity_profile()'s printed summary.
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ── Helpers ──


def parse_local_datetime(timestamp: str) -> datetime:
    """
    Parse a Claude Code transcript's UTC ISO timestamp and convert it to this
    machine's local time zone, so hour-of-day/day-of-week bucketing reflects
    wall-clock activity rather than UTC. Each machine's own OS timezone
    setting determines its "local" — if a headless machine is left on UTC,
    its own activity profile shifts accordingly (only matters for a machine
    whose own Claude Code sessions get logged here, not for merged/pushed
    data from elsewhere).
    """
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone()

def normalize_model_id(model: str) -> str:
    """
    Strip a trailing dated-snapshot suffix (e.g. "-20251001") from a model ID
    so "claude-haiku-4-5-20251001" and "claude-haiku-4-5" both match the same
    PRICING_PER_MILLION_TOKENS entry.
    """
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[0]
    return model


def resolve_model_rates(model: str, entry_date: str) -> Optional[dict]:
    """
    Look up input/output $-per-million-token rates for a model on a given date,
    accounting for Claude Sonnet 5's introductory pricing window. Returns None
    for a model this logger doesn't have pricing for.
    """
    rates = PRICING_PER_MILLION_TOKENS.get(normalize_model_id(model))
    if rates is None:
        return None
    if "input_intro" in rates and entry_date <= SONNET_5_INTRO_CUTOFF_DATE:
        return {"input": rates["input_intro"], "output": rates["output_intro"]}
    return {"input": rates["input"], "output": rates["output"]}


def compute_entry_cost(model: str, usage: dict, entry_date: str) -> Optional[float]:
    """
    Compute the dollar cost of one assistant message's token usage. Splits
    cache-creation tokens by TTL (5-minute vs 1-hour) when the transcript
    records that split (usage["cache_creation"]); falls back to treating all
    cache-creation tokens as 5-minute-rate when it doesn't. Returns None if
    the model has no known pricing.
    """
    rates = resolve_model_rates(model, entry_date)
    if rates is None:
        return None

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_input_tokens", 0)

    cache_creation_split = usage.get("cache_creation")
    if cache_creation_split:
        cache_5m_tokens = cache_creation_split.get("ephemeral_5m_input_tokens", 0)
        cache_1h_tokens = cache_creation_split.get("ephemeral_1h_input_tokens", 0)
    else:
        cache_5m_tokens = usage.get("cache_creation_input_tokens", 0)
        cache_1h_tokens = 0

    cost = 0.0
    cost += (input_tokens / 1_000_000) * rates["input"]
    cost += (output_tokens / 1_000_000) * rates["output"]
    cost += (cache_5m_tokens / 1_000_000) * rates["input"] * CACHE_WRITE_5M_MULTIPLIER
    cost += (cache_1h_tokens / 1_000_000) * rates["input"] * CACHE_WRITE_1H_MULTIPLIER
    cost += (cache_read_tokens / 1_000_000) * rates["input"] * CACHE_READ_MULTIPLIER
    return cost


def find_transcript_files() -> list[Path]:
    """Return every Claude Code session transcript file across all local projects."""
    if not PROJECTS_DIR.exists():
        return []
    return list(PROJECTS_DIR.glob("*/*.jsonl"))


def iter_usage_entries(transcript_path: Path):
    """
    Yield (timestamp_string, model, usage_dict) for every assistant message
    with real token usage in one transcript file. Skips malformed lines and
    non-usage-carrying entries rather than failing the whole file. Yields the
    raw UTC ISO timestamp string — callers slice/parse it as needed (e.g.
    build_daily_usage_history() takes the [:10] date prefix directly;
    build_activity_profile() parses it into local hour/weekday).
    """
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            message = entry.get("message")
            timestamp = entry.get("timestamp")
            if not message or not timestamp:
                continue

            usage = message.get("usage")
            model = message.get("model")
            if not usage or not model or model in NON_BILLABLE_MODEL_MARKERS:
                continue

            yield timestamp, model, usage


# ── Core ──

def build_daily_usage_history() -> dict:
    """
    Full re-scan of every local transcript, aggregated into a per-day usage
    and cost summary. Returns a dict keyed by ISO date ("YYYY-MM-DD").
    """
    daily = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "estimated_cost_usd": 0.0,
        "unpriced_models": set(),
    })

    for transcript_path in find_transcript_files():
        for timestamp, model, usage in iter_usage_entries(transcript_path):
            entry_date = timestamp[:10]
            day = daily[entry_date]
            day["input_tokens"] += usage.get("input_tokens", 0)
            day["output_tokens"] += usage.get("output_tokens", 0)
            day["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
            day["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)

            cost = compute_entry_cost(model, usage, entry_date)
            if cost is None:
                day["unpriced_models"].add(model)
            else:
                day["estimated_cost_usd"] += cost

    # Convert the unpriced_models set to a sorted list so the result is JSON-serializable.
    for day in daily.values():
        day["unpriced_models"] = sorted(day["unpriced_models"])
        day["estimated_cost_usd"] = round(day["estimated_cost_usd"], 4)

    return dict(sorted(daily.items()))


def write_daily_usage_history(history: dict) -> None:
    """Write the aggregated daily usage history to logs/claude_usage_history.json, fully overwriting any prior contents."""
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def push_daily_usage_history(history: dict) -> bool:
    """
    Push this machine's daily usage history to nova_api.py's POST
    /usage-history for cross-machine centralization. Returns True on success,
    False on any failure — never raises, since a failed push (e.g. nova-api
    not running) shouldn't break the local write this script already did.
    """
    payload = json.dumps({"source_machine": SOURCE_MACHINE, "daily_usage": history}).encode("utf-8")
    request = urllib.request.Request(
        f"{NOVA_API_URL}/usage-history",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"Push to {NOVA_API_URL}/usage-history failed (local write still succeeded): {e}")
        return False


def build_activity_profile() -> dict:
    """
    Full re-scan of every local transcript (separate from
    build_daily_usage_history()'s scan — keeps this newer, lower-stakes
    feature's code path independent from the existing cost/billing numbers),
    aggregated into a 7x24 grid of message counts by local weekday and hour.
    Windowed to the last ACTIVITY_PROFILE_WINDOW_DAYS days so the profile
    reflects Marvin's current schedule rather than being diluted by stale
    history. counts[weekday][hour] follows datetime.weekday() (Monday=0).
    """
    counts = [[0] * 24 for _ in range(7)]
    total_messages = 0
    window_end = datetime.now().astimezone()
    window_start = window_end - timedelta(days=ACTIVITY_PROFILE_WINDOW_DAYS)

    for transcript_path in find_transcript_files():
        for timestamp, _model, _usage in iter_usage_entries(transcript_path):
            local_dt = parse_local_datetime(timestamp)
            if local_dt < window_start:
                continue
            counts[local_dt.weekday()][local_dt.hour] += 1
            total_messages += 1

    return {
        "window_days": ACTIVITY_PROFILE_WINDOW_DAYS,
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "total_messages": total_messages,
        "counts": counts,
    }


def write_activity_profile(profile: dict) -> None:
    """Write the activity profile to logs/claude_activity_profile.json, fully overwriting any prior contents."""
    ACTIVITY_PROFILE_OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(ACTIVITY_PROFILE_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)


def push_activity_profile(profile: dict) -> bool:
    """
    Push this machine's activity profile to nova_api.py's POST
    /activity-profile for cross-machine centralization. Returns True on
    success, False on any failure — never raises, mirroring
    push_daily_usage_history()'s failure handling.
    """
    payload = json.dumps({"source_machine": SOURCE_MACHINE, "activity_profile": profile}).encode("utf-8")
    request = urllib.request.Request(
        f"{NOVA_API_URL}/activity-profile",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"Push to {NOVA_API_URL}/activity-profile failed (local write still succeeded): {e}")
        return False


def summarize_quietest_hours(profile: dict, top_n: int = 5) -> list[tuple[str, int, int]]:
    """
    Return the top_n (weekday_name, hour, count) cells with the lowest
    message counts in the activity profile — the real idle windows the
    autonomous-dispatch design needs, instead of a guessed reserve
    percentage. Ties broken by weekday/hour order for stable output.
    """
    cells = [
        (WEEKDAY_NAMES[weekday], hour, profile["counts"][weekday][hour])
        for weekday in range(7)
        for hour in range(24)
    ]
    cells.sort(key=lambda cell: cell[2])
    return cells[:top_n]


# ── Main ──

if __name__ == "__main__":
    history = build_daily_usage_history()
    write_daily_usage_history(history)

    print(f"Wrote {len(history)} day(s) of usage history to {OUTPUT_PATH}")
    for date, day in list(history.items())[-7:]:
        print(
            f"  {date}: ${day['estimated_cost_usd']:.2f} "
            f"({day['input_tokens']:,} in / {day['output_tokens']:,} out / "
            f"{day['cache_read_tokens']:,} cache-read tokens)"
        )

    profile = build_activity_profile()
    write_activity_profile(profile)

    print(
        f"\nWrote a {profile['window_days']}-day activity profile "
        f"({profile['total_messages']:,} messages) to {ACTIVITY_PROFILE_OUTPUT_PATH}"
    )
    print("  Quietest weekday/hour blocks (real idle-window candidates):")
    for weekday_name, hour, count in summarize_quietest_hours(profile):
        print(f"    {weekday_name} {hour:02d}:00 — {count} message(s)")

    if "--push" in sys.argv:
        if push_daily_usage_history(history):
            print(f"Pushed usage history as '{SOURCE_MACHINE}' to {NOVA_API_URL}/usage-history")
        if push_activity_profile(profile):
            print(f"Pushed activity profile as '{SOURCE_MACHINE}' to {NOVA_API_URL}/activity-profile")
