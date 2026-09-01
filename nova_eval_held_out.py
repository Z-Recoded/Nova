# nova_eval_held_out.py
# The genuinely held-out task pool for the coding sub-agent's completion
# gates (ClickUp 86bbcfv8d, "Eval Harness -- Initiative 1"). Distinct from
# nova_coding_eval.py's dev set: every task here must NEVER be used to
# write, debug, or tune a gate in nova_completion_gate.py -- its only job
# is testing whether a gate generalizes to a failure shape it wasn't built
# from. The dev/held-out split lives in separate files on purpose, not just
# separate variable names, so it's much harder to accidentally copy a
# held-out task into a debugging script.
#
# Real motivating finding (2026-08-12): logs/agent_task_outcomes.jsonl has
# only 8 "merged" tasks in this repo's entire history, and all 8 are
# already spent -- 6 tuning gates directly (nova_coding_eval.py's dev set),
# 2 excluded as already-spike-tested. There is currently no fresh real task
# anywhere to build a held-out set from, so this module supports two
# sources instead of one: tasks authored deliberately right now (see the
# discipline below), and real organic merges added going forward via
# add_organic_merge_task().

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

import nova_orchestrator

LOGS_DIR = nova_orchestrator.LOGS_DIR
HELD_OUT_POOL_LOG_PATH = os.path.join(LOGS_DIR, "held_out_pool.jsonl")


@dataclass
class HeldOutTask:
    """
    Everything check_ground_truth_completion() needs to test a candidate
    model's diff against, plus provenance. `requirements` is extracted via
    nova_completion_gate.extract_task_requirements() once at authoring
    time and frozen here -- re-extracting it live on every eval run would
    let the requirements drift between runs of the same task.
    """

    task_id: str  # stable slug, e.g. "hot-001-headroom-timeout-guard"
    task_description: str  # same plain-English register run_coding_task() takes
    base_ref: str  # real commit SHA the worktree branches from
    requirements: (
        dict  # extract_task_requirements()-shaped: required_files/forbidden_files/narrow_scope_files/deliverables
    )
    source: str  # "authored" | "organic_merge"
    added: str  # ISO date this entry entered the pool
    note: str = ""  # why this task was picked, or promotion/exclusion history


# ── Hand-authored tasks ──────────────────────────────────
# Empty until the follow-up authoring pass (deliberately out of scope for
# the mechanism this module builds -- see ClickUp 86bbcfv8d). When adding a
# task here, follow this discipline exactly, or the task is contaminated
# before it's ever run:
#   1. Pick a real, narrow, objectively-checkable improvement to Nova's own
#      current code (source candidates: CLAUDE.md's Phase Roadmap,
#      NOVA_BUILD_LOG.md's deferred items -- real undone work, not invented
#      busywork).
#   2. Confirm the target file/function has never appeared in
#      logs/agent_log.jsonl, logs/coding_review_log.jsonl,
#      logs/ground_truth_gate_log.jsonl, or nova_coding_eval.py's
#      DEV_SET_BRANCH_COMMITS branches.
#   3. Write task_description in the same plain-English register real
#      production tasks use -- a "test-shaped" task lets a gate key off
#      phrasing instead of substance, its own leakage vector.
#   4. Set base_ref = the real current HEAD commit SHA at authoring time
#      (git rev-parse HEAD), not a branch name, so it stays reproducible
#      as master moves forward.
#   5. Call extract_task_requirements(task_description) once by hand,
#      review/correct the output, freeze it into `requirements` below.
#   6. Never run the task through any backend or gate while authoring it --
#      doing so would itself be "using it to tune a gate," recreating the
#      exact contamination this module exists to prevent. Only structural
#      sanity checks are allowed at authoring time (does base_ref resolve
#      to a real commit, is requirements valid).
#   7. Get one line of human sign-off (a note here, or Marvin's own review)
#      before the task enters this list.
#
# First authoring pass: 2026-08-31, 4 tasks, signed off by Marvin same day.
# Targets picked from real deferred work (CLAUDE.md notes + a gap hit live
# during the 2026-08-30 Tailscale incident). Each target verified against
# the discipline: zero edit-targets and zero gate-fires in agent_log.jsonl
# / coding_review_log.jsonl / ground_truth_gate_log.jsonl, and none is a
# DEV_SET_BRANCH_COMMITS target file. requirements were extracted once via
# extract_task_requirements() and reviewed; the tasks were never run through
# any backend or gate while authoring.
AUTHORED_HELD_OUT_TASKS: list[HeldOutTask] = [
    HeldOutTask(
        task_id="hot-001-omen-sync-lan-fallback",
        task_description=(
            "nova_omen_sync.py connects to the Omen at a single hardcoded Tailscale IP (the "
            "OMEN_HOST constant). When the Aero's Tailscale link to the Omen degrades but both "
            "machines are still on the same home LAN, every sync fails even though the Omen is "
            "fully reachable at its LAN IP 192.168.1.250. Add a fallback: if the Tailscale host "
            "is not reachable on the SSH port, retry the sync against the Omen's LAN IP instead, "
            "and print which path was used. Keep the change entirely inside nova_omen_sync.py -- "
            "do not modify nova_omen_dispatch.py or any other module that also talks to the Omen. "
            "Do not change the default Tailscale-first behaviour; the LAN IP is only a fallback "
            "when the Tailscale path is down."
        ),
        base_ref="a7decf34e306662d16adeebbf73547067af9a0e0",
        requirements={
            "required_files": ["nova_omen_sync.py"],
            "forbidden_files": ["nova_omen_dispatch.py"],
            "narrow_scope_files": ["nova_omen_sync.py"],
            "deliverables": ["OMEN_HOST"],
        },
        source="authored",
        added="2026-08-31",
        note=(
            "Real gap hit live during the 2026-08-30 Aero<->Omen Tailscale-over-Wi-Fi incident. "
            "nova_omen_sync.py: 45 incidental mentions in agent_log.jsonl (reasoning context only), "
            "0 edit-targets, 0 gate-fires. Tests forbidden_paths_untouched + narrow_scope on a fresh case."
        ),
    ),
    HeldOutTask(
        task_id="hot-002-notify-per-category-topic",
        task_description=(
            "nova_notify.py's send_notification() publishes every notification to one shared "
            "ntfy.sh topic read from the NTFY_TOPIC environment variable. Add optional "
            "per-category routing: send_notification() should take a new optional 'category' "
            "argument, and when a category is given and an environment variable "
            "NTFY_TOPIC_<CATEGORY> (category uppercased) is set, publish to that topic instead of "
            "the shared one; otherwise fall back to NTFY_TOPIC exactly as today. Callers that pass "
            "no category must keep working unchanged. Keep the change inside nova_notify.py."
        ),
        base_ref="a7decf34e306662d16adeebbf73547067af9a0e0",
        requirements={
            "required_files": ["nova_notify.py"],
            "forbidden_files": [],
            "narrow_scope_files": ["nova_notify.py"],
            "deliverables": ["send_notification()"],
        },
        source="authored",
        added="2026-08-31",
        note=(
            "CLAUDE.md Nova Controller UX section explicitly names this as an unbuilt fast-follow "
            "('splitting into per-category topics is a plausible fast-follow, not built'). "
            "nova_notify.py: 4 incidental mentions in agent_log.jsonl, 0 edit-targets, 0 gate-fires."
        ),
    ),
    HeldOutTask(
        task_id="hot-003-chunk-viz-json-output",
        task_description=(
            "nova_chunk_viz.py is a retrieval-audit CLI that prints the chunks Chroma retrieved "
            "for a query as a colored terminal table. Add a --json flag that instead prints the "
            "same retrieved-chunk data (source file, chunk index, distance, character tag, text "
            "preview) as a single JSON array to stdout, with no color codes, so the output can be "
            "piped into another tool. Reuse the existing resolve_chunks() function rather than "
            "re-implementing retrieval. The default colored-table output must be unchanged when "
            "--json is not passed."
        ),
        base_ref="a7decf34e306662d16adeebbf73547067af9a0e0",
        requirements={
            "required_files": ["nova_chunk_viz.py"],
            "forbidden_files": [],
            "narrow_scope_files": ["nova_chunk_viz.py"],
            "deliverables": ["--json"],
        },
        source="authored",
        added="2026-08-31",
        note=(
            "CLAUDE.md 86bara3tj: 'Stage 1 of 3 ... Only the CLI is built here; resolve_chunks() is "
            "a clean standalone function so a future stage can reuse it.' This is a narrow slice of "
            "Stage 2. nova_chunk_viz.py: 1 incidental mention in agent_log.jsonl, 0 edit-targets, "
            "0 gate-fires. Imports from nova_query (a dev-set file) but must not edit it -- "
            "narrow_scope enforces that."
        ),
    ),
    HeldOutTask(
        task_id="hot-004-log-rotation-dry-run",
        task_description=(
            "nova_log_rotation.py archives old Nova Log telemetry entries and rewrites the active "
            "log files in place. Add a --dry-run flag that reports, per rotatable log file, how "
            "many entries would be archived and how many would remain -- without writing any "
            "archive file or modifying any active file. Normal (non-dry-run) behaviour must be "
            "unchanged."
        ),
        base_ref="a7decf34e306662d16adeebbf73547067af9a0e0",
        requirements={
            "required_files": ["nova_log_rotation.py"],
            "forbidden_files": [],
            "narrow_scope_files": ["nova_log_rotation.py"],
            "deliverables": ["--dry-run"],
        },
        source="authored",
        added="2026-08-31",
        note=(
            "'Make a destructive-in-place operation previewable' is a real production task shape "
            "(cf. the dev set's own start_nova.ps1 idempotency task). nova_log_rotation.py: 0 "
            "appearances anywhere in agent_log.jsonl / coding_review_log.jsonl / "
            "ground_truth_gate_log.jsonl -- the cleanest of the four."
        ),
    ),
]

# Not yet usable until real tasks exist -- 3 matches the "3-5 tasks" figure
# already agreed as the target for the first authoring pass. This is a
# floor, not a ceiling: the pool is meant to grow via organic merges too,
# unlike nova_coding_eval.py's dev set, which is frozen forever at exactly 6.
EXPECTED_MIN_HELD_OUT_COUNT = 3


def add_organic_merge_task(
    task_id: str, task_description: str, base_ref: str, requirements: dict, note: str = ""
) -> None:
    """
    Appends a real, organically-merged task to the held-out pool
    (logs/held_out_pool.jsonl). Call this once, by hand, right after a task
    is merged via nova_orchestrator.record_task_outcome(branch, "merged",
    pool="held_out") -- that call and this one are deliberately separate
    records. record_task_outcome()'s `pool` field only tracks which bucket
    a branch's *outcome* belongs to (for nova_coding_dataset_curator.py's
    leakage filter); it doesn't carry the base_ref/requirements a held-out
    task actually needs to be re-tested against later, which is what this
    function records.
    """
    entry = HeldOutTask(
        task_id=task_id,
        task_description=task_description,
        base_ref=base_ref,
        requirements=requirements,
        source="organic_merge",
        added=datetime.now().strftime("%Y-%m-%d"),
        note=note,
    )
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(HELD_OUT_POOL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


def _load_organic_merges() -> list[HeldOutTask]:
    """Real organic merges added over time via add_organic_merge_task(). Empty if the pool file doesn't exist yet."""
    if not os.path.exists(HELD_OUT_POOL_LOG_PATH):
        return []
    tasks = []
    with open(HELD_OUT_POOL_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(HeldOutTask(**json.loads(line)))
    return tasks


def select_held_out_tasks() -> list[HeldOutTask]:
    """
    Combines hand-authored tasks (AUTHORED_HELD_OUT_TASKS above) with real
    organic merges (logs/held_out_pool.jsonl). Asserts a MINIMUM count, not
    an exact one like nova_coding_eval.py's dev set -- this pool is meant
    to grow over time, so a floor is the correct check, a ceiling would be
    wrong. Raising loudly here (rather than silently returning a too-small
    pool) is deliberate: a held-out comparison run on too few tasks would
    look like a real generalization test while actually being close to
    meaningless.
    """
    selected = list(AUTHORED_HELD_OUT_TASKS) + _load_organic_merges()
    if len(selected) < EXPECTED_MIN_HELD_OUT_COUNT:
        raise RuntimeError(
            f"Only {len(selected)} held-out task(s) exist, need at least "
            f"{EXPECTED_MIN_HELD_OUT_COUNT} before this pool is usable for gate-generalization "
            f"testing. Author new tasks following the discipline documented above "
            f"AUTHORED_HELD_OUT_TASKS, or wait for a real organic merge via add_organic_merge_task()."
        )
    return selected
