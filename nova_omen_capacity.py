# nova_omen_capacity.py
# Real resource-headroom audit for the Omen (ClickUp 86baxty6d, the self-hosting
# gate task) — CPU/RAM/disk/GPU headroom, what's actually running (always-on vs
# bursty), and a growth-rate log so re-running this over time answers "is disk
# usage creeping up" without needing a live monitoring stack.
#
# SSHes from the Aero, same connection details as nova_agent_log_status.py
# (Tailscale IP — works whether or not the Aero is on the home LAN).
#
# Usage:
#   python nova_omen_capacity.py            # print a report, log a snapshot
#   python nova_omen_capacity.py --no-log   # print only, don't append to the log

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

# Windows' default console codepage (cp1252) can't encode the ─ characters this
# file prints — same recurring class of bug already fixed in nova_benchmark.py
# and nova_orchestrator.py's subprocess calls.
sys.stdout.reconfigure(encoding="utf-8")

OMEN_HOST = "100.114.197.117"
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

CAPACITY_LOG_PATH = "C:/Nova/logs/omen_capacity_log.jsonl"

SSH_TIMEOUT_SECONDS = 20

# One remote script, run over a single SSH connection, that prints simple
# "KEY value" lines — avoids fragile parsing of human-readable units like
# "7.6Gi" from `free -h`, and avoids JSON-escaping a payload through SSH quoting.
_REMOTE_SCRIPT = r'''
echo "CPU_CORES $(nproc)"
echo "LOAD_AVG $(cut -d' ' -f1-3 /proc/loadavg)"
free -b | awk 'NR==2{print "RAM_TOTAL_BYTES", $2; print "RAM_USED_BYTES", $3; print "RAM_AVAILABLE_BYTES", $7}'
free -b | awk 'NR==3{print "SWAP_TOTAL_BYTES", $2; print "SWAP_USED_BYTES", $3}'
df -B1 / | awk 'NR==2{print "DISK_TOTAL_BYTES", $2; print "DISK_USED_BYTES", $3; print "DISK_AVAIL_BYTES", $4}'
echo "GPU_PRESENT $(lspci | grep -qi nvidia && echo yes || echo no)"
echo "GPU_DRIVER_INSTALLED $(dpkg -l 2>/dev/null | grep -qi nvidia-driver && echo yes || echo no)"
echo "CHROMA_DATA_BYTES $(du -sb __REPO__/memory 2>/dev/null | cut -f1)"
echo "GIT_REPO_BYTES $(du -sb __REPO__/.git 2>/dev/null | cut -f1)"
echo "VENV_BYTES $(du -sb __REPO__/nova-env 2>/dev/null | cut -f1)"
echo "DOCKER_INSTALLED $(command -v docker >/dev/null && echo yes || echo no)"
echo "DOCKER_CONTAINER_COUNT $(docker ps -a -q 2>/dev/null | wc -l)"
echo "DOCKER_IMAGE_COUNT $(docker images -q 2>/dev/null | wc -l)"
echo "ALWAYS_ON_SERVICES $(systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' | grep -E 'nova-|docker|tailscale' | paste -sd, -)"
'''.replace("__REPO__", OMEN_REPO_PATH)


def _run_remote(script: str) -> str:
    """Run `script` on the Omen over SSH and return its stdout, raising on failure."""
    result = subprocess.run(
        ["ssh", f"{OMEN_USER}@{OMEN_HOST}", script],
        capture_output=True, text=True, timeout=SSH_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def _parse_kv_lines(output: str) -> dict:
    """Turn the remote script's "KEY value" lines into a dict of raw strings."""
    parsed = {}
    for line in output.strip().splitlines():
        if " " not in line:
            continue
        key, _, value = line.partition(" ")
        parsed[key] = value.strip()
    return parsed


def _gb(byte_str: str) -> float | None:
    """Convert a byte-count string to GB, rounded to 2 decimals. None if missing/empty."""
    if not byte_str:
        return None
    try:
        return round(int(byte_str) / (1024 ** 3), 2)
    except ValueError:
        return None


def get_capacity_report() -> dict:
    """
    Real, live snapshot of the Omen's CPU/RAM/disk/GPU headroom and what's
    actually running — the core deliverable of 86baxty6d's "real resource
    audit" scope item. Raises on SSH failure rather than returning a fake
    zero-headroom report (a wrong "it's full" reading is worse than an
    honest error here).
    """
    raw = _parse_kv_lines(_run_remote(_REMOTE_SCRIPT))

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cpu_cores": int(raw.get("CPU_CORES", 0)),
        "load_avg": raw.get("LOAD_AVG"),
        "ram_total_gb": _gb(raw.get("RAM_TOTAL_BYTES")),
        "ram_used_gb": _gb(raw.get("RAM_USED_BYTES")),
        "ram_available_gb": _gb(raw.get("RAM_AVAILABLE_BYTES")),
        "swap_total_gb": _gb(raw.get("SWAP_TOTAL_BYTES")),
        "swap_used_gb": _gb(raw.get("SWAP_USED_BYTES")),
        "disk_total_gb": _gb(raw.get("DISK_TOTAL_BYTES")),
        "disk_used_gb": _gb(raw.get("DISK_USED_BYTES")),
        "disk_available_gb": _gb(raw.get("DISK_AVAIL_BYTES")),
        "gpu_present": raw.get("GPU_PRESENT") == "yes",
        "gpu_driver_installed": raw.get("GPU_DRIVER_INSTALLED") == "yes",
        "chroma_data_gb": _gb(raw.get("CHROMA_DATA_BYTES")),
        "git_repo_gb": _gb(raw.get("GIT_REPO_BYTES")),
        "venv_gb": _gb(raw.get("VENV_BYTES")),
        "docker_installed": raw.get("DOCKER_INSTALLED") == "yes",
        "docker_container_count": int(raw.get("DOCKER_CONTAINER_COUNT", 0) or 0),
        "docker_image_count": int(raw.get("DOCKER_IMAGE_COUNT", 0) or 0),
        "always_on_services": (raw.get("ALWAYS_ON_SERVICES") or "").split(",") if raw.get("ALWAYS_ON_SERVICES") else [],
    }


def log_capacity_snapshot(report: dict) -> None:
    """
    Append one JSON line to omen_capacity_log.jsonl — mirrors nova_log.py's
    append convention. This is 86baxty6d's "own growth-rate tracking, not
    just a one-time snapshot" requirement: re-running this script over time
    builds a real disk/RAM trend instead of a single point-in-time claim.
    """
    os.makedirs(os.path.dirname(CAPACITY_LOG_PATH), exist_ok=True)
    with open(CAPACITY_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")


def print_report(report: dict) -> None:
    print(f"\nOmen Capacity Audit — {report['timestamp']}")
    print("── Compute ──────────────────────────────────────")
    print(f"  CPU: {report['cpu_cores']} cores, load avg {report['load_avg']}")
    print(f"  RAM: {report['ram_used_gb']}GB used / {report['ram_total_gb']}GB total "
          f"({report['ram_available_gb']}GB available)")
    print(f"  Swap: {report['swap_used_gb']}GB used / {report['swap_total_gb']}GB total")
    print(f"  GPU: present={report['gpu_present']}, driver installed={report['gpu_driver_installed']}"
          + ("" if report["gpu_driver_installed"] else " (unusable for any GPU workload as-is)"))

    print("\n── Disk ─────────────────────────────────────────")
    print(f"  Total: {report['disk_used_gb']}GB used / {report['disk_total_gb']}GB total "
          f"({report['disk_available_gb']}GB available)")
    print(f"  Chroma data: {report['chroma_data_gb']}GB")
    print(f"  Git repo: {report['git_repo_gb']}GB")
    print(f"  Python venv: {report['venv_gb']}GB")

    print("\n── Running services ─────────────────────────────")
    for service in report["always_on_services"]:
        print(f"  {service}")
    print(f"  Docker installed: {report['docker_installed']} "
          f"({report['docker_container_count']} containers, {report['docker_image_count']} images)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-log", action="store_true", help="Print only, don't append to the growth-tracking log")
    args = parser.parse_args()

    try:
        capacity_report = get_capacity_report()
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"Failed to reach the Omen: {e}")
        sys.exit(1)

    print_report(capacity_report)

    if not args.no_log:
        log_capacity_snapshot(capacity_report)
        print(f"\nLogged to {CAPACITY_LOG_PATH}")
