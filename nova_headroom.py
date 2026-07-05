# nova_headroom.py
# Nova self-monitoring resource headroom calculator (Phase 1.5).
#
# Answers one question: "how much more work can Nova take on right now
# without pushing the machine past nominal load?" Reports live VRAM (RTX
# 5070, via nvidia-smi), RAM + CPU (via psutil), ingestion queue depth, and
# active session count, then translates remaining headroom into a count of
# heavy/medium/light tasks Nova could still start.
#
# Talks directly to nvidia-smi and psutil because it IS the designated
# monitoring script (Section 2 of CLAUDE.md — the Golden Rule restricts
# Chroma/Ollama access, not OS/GPU telemetry). Exposed to the rest of Nova
# only via GET /headroom in nova_api.py.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_headroom.py

import subprocess

import psutil

# ── Config ─────────────────────────────────────────────────────

# nvidia-smi query used to read GPU name + memory. CSV with a header row,
# one data row per GPU. This machine has a single GPU, so we always read
# row 0.
NVIDIA_SMI_QUERY_FIELDS = "name,memory.total,memory.used"
NVIDIA_SMI_COMMAND = [
    "nvidia-smi",
    f"--query-gpu={NVIDIA_SMI_QUERY_FIELDS}",
    "--format=csv,noheader,nounits",
]
NVIDIA_SMI_TIMEOUT_SECONDS = 5

# How long psutil samples CPU load for. A short blocking sample is far more
# accurate than a 0-argument call, which just returns the reading since the
# last call (unreliable on first use).
CPU_SAMPLE_INTERVAL_SECONDS = 0.5

# Nominal thresholds — the load level headroom is measured against, not
# 100% capacity. Leaves a safety margin instead of assuming the machine is
# usable right up to the wall.
NOMINAL_VRAM_THRESHOLD_PERCENT = 80.0
NOMINAL_CPU_THRESHOLD_PERCENT = 80.0
NOMINAL_RAM_THRESHOLD_PERCENT = 80.0

# Task cost profiles — rough resource cost of one task in each class,
# used only to size headroom (not a scheduler or admission controller).
# vram_mb / ram_mb are estimated peak footprints; cpu_percent is estimated
# sustained load while the task runs.
TASK_COST_PROFILES = {
    "heavy": {
        "description": "LLM inference, fine-tune prep, large ingestion",
        "vram_mb": 4096,
        "ram_mb": 2048,
        "cpu_percent": 40.0,
    },
    "medium": {
        "description": "Document ingestion, embedding generation, retrieval",
        "vram_mb": 1024,
        "ram_mb": 1024,
        "cpu_percent": 20.0,
    },
    "light": {
        "description": "Voice transcription, file monitoring, notification dispatch",
        "vram_mb": 256,
        "ram_mb": 256,
        "cpu_percent": 5.0,
    },
}


# ── GPU stats (nvidia-smi) ──────────────────────────────────────

def get_gpu_stats() -> dict:
    """
    Read current VRAM usage from nvidia-smi via CLI subprocess (not pynvml,
    per project decision — nvidia-smi is already confirmed working on this
    machine). Returns total/used/free MB and percent used. On any failure
    (nvidia-smi missing, no GPU, parse error), returns nulls with an error
    message instead of a fabricated reading.
    """
    try:
        result = subprocess.run(
            NVIDIA_SMI_COMMAND,
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
            check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return _empty_gpu_stats(error=f"nvidia-smi call failed: {e}")

    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) != 3:
        return _empty_gpu_stats(error=f"Unexpected nvidia-smi output: '{first_line}'")

    gpu_name, total_str, used_str = parts
    try:
        total_mb = float(total_str)
        used_mb = float(used_str)
    except ValueError as e:
        return _empty_gpu_stats(error=f"Could not parse nvidia-smi memory values: {e}")

    free_mb = total_mb - used_mb
    percent_used = (used_mb / total_mb) * 100.0 if total_mb > 0 else 0.0

    return {
        "gpu_name": gpu_name,
        "vram_total_mb": total_mb,
        "vram_used_mb": used_mb,
        "vram_free_mb": free_mb,
        "vram_percent_used": round(percent_used, 1),
        "error": None,
    }


def _empty_gpu_stats(error: str) -> dict:
    """Shared fallback shape for get_gpu_stats() when nvidia-smi can't be read."""
    return {
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
        "vram_free_mb": None,
        "vram_percent_used": None,
        "error": error,
    }


# ── System stats (psutil) ───────────────────────────────────────

def get_system_stats() -> dict:
    """
    Read current RAM and CPU load via psutil. CPU is sampled over
    CPU_SAMPLE_INTERVAL_SECONDS for an accurate reading rather than an
    instant (and often misleading) snapshot.
    """
    cpu_percent = psutil.cpu_percent(interval=CPU_SAMPLE_INTERVAL_SECONDS)
    memory = psutil.virtual_memory()

    return {
        "cpu_percent": cpu_percent,
        "ram_total_mb": round(memory.total / (1024 * 1024), 1),
        "ram_used_mb": round(memory.used / (1024 * 1024), 1),
        "ram_percent_used": memory.percent,
    }


# ── Pipeline / session signals ──────────────────────────────────

def get_ingestion_queue_depth() -> int | None:
    """
    Report how many files are currently queued for ingestion.

    No real signal exists for this today: nova_watcher.py debounces file
    events with per-file threading.Timer objects (see Debouncer in
    nova_watcher.py), but that state lives inside the watcher process only
    while it's running, isn't persisted, and isn't exposed anywhere nova_api
    or this module can read it — and nova_watcher.py itself is deferred,
    not currently running (per CLAUDE.md Section 1). Returning None rather
    than a fabricated number.
    """
    return None


def get_active_session_count() -> int | None:
    """
    Report how many active conversation sessions Nova currently has.

    No real signal exists for this today: nova_memory_store.py persists a
    single shared history.json (one conversation, not per-session), and
    nova_api.py doesn't track concurrent callers/sessions anywhere. Returning
    None rather than a fabricated number.
    """
    return None


# ── Headroom math ────────────────────────────────────────────────

def _available_before_threshold(used: float, total: float, threshold_percent: float) -> float:
    """
    Return how much of a resource remains before hitting threshold_percent
    of its total capacity. Never negative — if already over threshold,
    headroom is 0, not a negative number.
    """
    threshold_amount = total * (threshold_percent / 100.0)
    remaining = threshold_amount - used
    return max(remaining, 0.0)


def compute_task_headroom(gpu_stats: dict, system_stats: dict) -> dict:
    """
    For each task cost profile (heavy/medium/light), compute the largest
    number of that task type Nova could start right now without pushing
    VRAM, RAM, or CPU past their nominal thresholds. Whichever resource
    runs out first caps the count for that profile.

    If GPU stats are unavailable (nvidia-smi failed), VRAM is excluded from
    the cap for that calculation rather than blocking the whole report.
    """
    available_cpu_percent = _available_before_threshold(
        system_stats["cpu_percent"], 100.0, NOMINAL_CPU_THRESHOLD_PERCENT
    )
    available_ram_mb = _available_before_threshold(
        system_stats["ram_used_mb"], system_stats["ram_total_mb"], NOMINAL_RAM_THRESHOLD_PERCENT
    )

    gpu_available = gpu_stats["error"] is None
    if gpu_available:
        available_vram_mb = _available_before_threshold(
            gpu_stats["vram_used_mb"], gpu_stats["vram_total_mb"], NOMINAL_VRAM_THRESHOLD_PERCENT
        )
    else:
        available_vram_mb = None

    headroom_by_profile = {}
    for profile_name, cost in TASK_COST_PROFILES.items():
        limits = [
            available_cpu_percent // cost["cpu_percent"],
            available_ram_mb // cost["ram_mb"],
        ]
        if gpu_available:
            limits.append(available_vram_mb // cost["vram_mb"])
        headroom_by_profile[profile_name] = int(max(min(limits), 0))

    return headroom_by_profile


# ── Plain-English summary ───────────────────────────────────────

def _describe_pipeline_status(queue_depth: int | None) -> str:
    """Turn ingestion queue depth into a short plain-English phrase."""
    if queue_depth is None:
        return "pipeline status unknown (no queue signal available)"
    if queue_depth == 0:
        return "pipeline idle"
    return f"pipeline processing ({queue_depth} queued)"


def build_headroom_summary(
    gpu_stats: dict, system_stats: dict, headroom_by_profile: dict, queue_depth: int | None
) -> str:
    """
    Compose the one-line plain-English headroom summary Nova reports,
    e.g. '52% VRAM, 34% CPU, pipeline idle. Headroom: 2 heavy or
    4 light tasks before hitting nominal thresholds.'
    """
    vram_percent = gpu_stats["vram_percent_used"]
    vram_phrase = f"{vram_percent:.0f}% VRAM" if vram_percent is not None else "VRAM unknown"
    cpu_phrase = f"{system_stats['cpu_percent']:.0f}% CPU"
    pipeline_phrase = _describe_pipeline_status(queue_depth)

    heavy_count = headroom_by_profile["heavy"]
    light_count = headroom_by_profile["light"]

    return (
        f"{vram_phrase}, {cpu_phrase}, {pipeline_phrase}. "
        f"Headroom: {heavy_count} heavy or {light_count} light tasks "
        f"before hitting nominal thresholds."
    )


# ── Main report ──────────────────────────────────────────────────

def get_headroom_report() -> dict:
    """
    Build the full headroom report: raw GPU/RAM/CPU stats, ingestion queue
    depth, active session count, per-profile task headroom, and the
    plain-English summary. This is the one function nova_api.py's
    GET /headroom route calls.
    """
    gpu_stats = get_gpu_stats()
    system_stats = get_system_stats()
    queue_depth = get_ingestion_queue_depth()
    active_sessions = get_active_session_count()
    headroom_by_profile = compute_task_headroom(gpu_stats, system_stats)
    summary = build_headroom_summary(gpu_stats, system_stats, headroom_by_profile, queue_depth)

    return {
        "gpu": gpu_stats,
        "system": system_stats,
        "ingestion_queue_depth": queue_depth,
        "active_session_count": active_sessions,
        "task_headroom": headroom_by_profile,
        "task_cost_profiles": TASK_COST_PROFILES,
        "nominal_thresholds": {
            "vram_percent": NOMINAL_VRAM_THRESHOLD_PERCENT,
            "cpu_percent": NOMINAL_CPU_THRESHOLD_PERCENT,
            "ram_percent": NOMINAL_RAM_THRESHOLD_PERCENT,
        },
        "summary": summary,
    }


# ── Entry point ────────────────────────────────────────────────

def main():
    """Print the headroom report to the console — used for manual sanity checks."""
    report = get_headroom_report()
    print(report["summary"])
    print()
    print(f"GPU:    {report['gpu']}")
    print(f"System: {report['system']}")
    print(f"Ingestion queue depth: {report['ingestion_queue_depth']}")
    print(f"Active session count:  {report['active_session_count']}")
    print(f"Task headroom: {report['task_headroom']}")


if __name__ == "__main__":
    main()
