"""
nova_gpu_monitor.py — Background GPU telemetry logger for the Aero.

Polls nvidia-smi on an interval and appends structured readings to
logs/gpu_telemetry_log.jsonl, so questions about thermal wear (throttling
frequency, idle-temp drift over months) can be answered from real data
instead of general guidance. Started manually, standalone (no nova_api.py
dependency) — run it in its own terminal before a long training/inference
job, or leave it running continuously to track long-term drift.
"""

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

# ── Constants ──

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_CLEAR_AFTER_READINGS = 20
LOG_PATH = Path(__file__).parent / "logs" / "gpu_telemetry_log.jsonl"

# nvidia-smi --query-gpu field names, in the order we read them back out of the CSV row.
NVIDIA_SMI_QUERY_FIELDS = [
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "clocks.sm",
    "clocks.mem",
    "fan.speed",
    "clocks_throttle_reasons.hw_thermal_slowdown",
    "clocks_throttle_reasons.sw_thermal_slowdown",
    "clocks_throttle_reasons.hw_power_brake_slowdown",
]

# ── Helpers ──


def poll_gpu_once() -> dict | None:
    """
    Runs one nvidia-smi query and returns a parsed reading dict, or None if
    nvidia-smi isn't available or the call fails — a missed poll should never
    crash a long-running background logger.
    """
    query = ",".join(NVIDIA_SMI_QUERY_FIELDS)
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    values = [v.strip() for v in result.stdout.strip().split(",")]
    if len(values) != len(NVIDIA_SMI_QUERY_FIELDS):
        return None

    reading = dict(zip(NVIDIA_SMI_QUERY_FIELDS, values, strict=True))
    reading["timestamp"] = datetime.now(UTC).isoformat()
    return reading


def append_reading(reading: dict) -> None:
    """Appends one telemetry reading as a JSONL line, creating logs/ if needed."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(reading) + "\n")


def load_readings() -> list[dict]:
    """Reads every logged reading back in, skipping any malformed lines."""
    if not LOG_PATH.exists():
        return []

    readings = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                readings.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return readings


def is_throttling(reading: dict) -> bool:
    """True if any real throttle-reason flag was active on this reading."""
    throttle_fields = [
        "clocks_throttle_reasons.hw_thermal_slowdown",
        "clocks_throttle_reasons.sw_thermal_slowdown",
        "clocks_throttle_reasons.hw_power_brake_slowdown",
    ]
    return any(reading.get(field) == "Active" for field in throttle_fields)


def format_reading_line(reading: dict) -> str:
    """One human-readable terminal line for a single reading — the live view, not the log format."""
    local_time = datetime.fromisoformat(reading["timestamp"]).astimezone().strftime("%H:%M:%S")
    throttle_flag = "THROTTLING" if is_throttling(reading) else "ok"
    return (
        f"{local_time}  temp {reading['temperature.gpu']:>3}C  "
        f"power {reading['power.draw']:>6}/{reading['power.limit']}W  "
        f"util gpu {reading['utilization.gpu']:>3}% mem {reading['utilization.memory']:>3}%  "
        f"clocks sm {reading['clocks.sm']:>5}MHz mem {reading['clocks.mem']:>5}MHz  {throttle_flag}"
    )


def clear_terminal() -> None:
    """
    Clears the terminal screen. Uses subprocess.run() with an explicit argument
    list and shell=False rather than os.system() -- cls is a cmd.exe builtin, not
    a standalone binary, so it's invoked via ["cmd", "/c", "cls"] on Windows; no
    string is ever passed through a shell, so there's no injection surface.
    """
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "cls"], shell=False, check=False)
    else:
        subprocess.run(["clear"], shell=False, check=False)


# ── Core ──


def run_monitor_loop(poll_interval_seconds: int, duration_minutes: float | None, clear_after: int) -> None:
    """
    Polls nvidia-smi on a fixed interval until duration_minutes elapses (or
    forever if None), appending every reading to LOG_PATH (the full history,
    never cleared) and printing each one to the terminal. Every clear_after
    prints, the terminal screen is cleared and printing starts fresh from the
    top — keeps a long-running session from scrolling forever without losing
    any logged data.
    """
    start_time = time.monotonic()
    duration_seconds = duration_minutes * 60 if duration_minutes else None
    prints_since_clear = 0

    def print_header() -> None:
        print(f"Logging GPU telemetry to {LOG_PATH} every {poll_interval_seconds}s. Ctrl+C to stop.\n")

    print_header()

    try:
        while True:
            reading = poll_gpu_once()
            if reading is not None:
                append_reading(reading)
                print(format_reading_line(reading))
                prints_since_clear += 1
                if prints_since_clear >= clear_after:
                    clear_terminal()
                    print_header()
                    prints_since_clear = 0
            else:
                print("Warning: nvidia-smi poll failed, skipping this interval.")

            if duration_seconds is not None and (time.monotonic() - start_time) >= duration_seconds:
                break

            time.sleep(poll_interval_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")


def print_summary() -> None:
    """
    Prints min/max/avg temp and power, and a throttle-event count, across
    every reading logged so far — the practical wear-warning signals to
    watch (temp/throttle creep over time at similar workloads).
    """
    readings = load_readings()
    if not readings:
        print(f"No readings logged yet at {LOG_PATH}.")
        return

    temps = [float(r["temperature.gpu"]) for r in readings if r.get("temperature.gpu", "N/A") != "N/A"]
    powers = [float(r["power.draw"]) for r in readings if r.get("power.draw", "N/A") != "N/A"]
    throttle_count = sum(1 for r in readings if is_throttling(r))

    print(f"Readings logged: {len(readings)}")
    print(f"Span: {readings[0]['timestamp']} to {readings[-1]['timestamp']}")
    if temps:
        print(f"Temp (C): min {min(temps):.0f}, max {max(temps):.0f}, avg {sum(temps) / len(temps):.1f}")
    if powers:
        print(f"Power draw (W): min {min(powers):.1f}, max {max(powers):.1f}, avg {sum(powers) / len(powers):.1f}")
    print(f"Throttling active on {throttle_count}/{len(readings)} readings ({throttle_count / len(readings):.1%})")


# ── Main ──


def main() -> None:
    parser = argparse.ArgumentParser(description="Background GPU telemetry logger for the Aero.")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Seconds between polls (default {DEFAULT_POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Minutes to run before stopping automatically (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--clear-after",
        type=int,
        default=DEFAULT_CLEAR_AFTER_READINGS,
        metavar="N",
        help=f"Clear the terminal every N printed readings (default {DEFAULT_CLEAR_AFTER_READINGS})",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary stats from the existing log instead of logging new readings",
    )
    args = parser.parse_args()

    if args.summary:
        print_summary()
    else:
        run_monitor_loop(args.interval, args.duration, args.clear_after)


if __name__ == "__main__":
    main()
