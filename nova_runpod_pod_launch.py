# nova_runpod_pod_launch.py
# Launch/stop helper for a rented RunPod A100 pod (ClickUp 86baf4e70, Pattern 1 --
# "validate on Colab -> production pass on RunPod/Vast.ai A100"). Provisions the
# raw compute Nova's Qwen2.5-Coder-32B fine-tune scripts
# (nova_finetune_qwen_coder_sft.py / nova_finetune_qwen_coder.py) need but can't
# provision themselves -- neither of those scripts rents anything, they just
# assume they're already running on the right box.
#
# Deliberately does NOT bootstrap or run the training itself. 86baf4e70 is
# tagged tier-manual-only, and a rented pod starts billing the second it's
# RUNNING -- the "start spending" decision stays a human running this script
# by hand each time, not something any other script calls automatically. See
# runpod_finetune_runbook.md for the manual steps once a pod is up (git clone,
# pip install, run the finetune scripts, stop the pod).
#
# Uses raw requests against RunPod's REST API (base URL, auth header, and every
# endpoint path below confirmed against docs.runpod.io, not guessed) rather than
# the runpod SDK package -- requests is already a dependency (see
# nova_remote_inference.py's identical pattern for the serverless endpoint),
# and this avoids adding a new one for four straightforward HTTP calls.
#
# Usage:
#   python nova_runpod_pod_launch.py launch --name qwen-coder-run
#   python nova_runpod_pod_launch.py status <pod_id>
#   python nova_runpod_pod_launch.py stop <pod_id>
#   python nova_runpod_pod_launch.py terminate <pod_id>

import argparse
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")
RUNPOD_REST_BASE_URL = "https://rest.runpod.io/v1"

# Confirmed with Marvin, 2026-07-31: SECURE (not COMMUNITY) and non-interruptible
# -- a multi-hour training run getting reclaimed mid-run on spot/community
# pricing risks costing more in wasted GPU time than the cheaper rate saves.
DEFAULT_GPU_TYPE_ID = "NVIDIA A100 80GB PCIe"  # cheaper 80GB variant than SXM;
# 80GB gives real headroom for a 32B model + LoRA + 8192-token sequences
DEFAULT_CLOUD_TYPE = "SECURE"
DEFAULT_INTERRUPTIBLE = False

# RunPod's official PyTorch+CUDA template -- has SSH-on-PUBLIC_KEY already
# built into its startup script (see _load_ssh_public_key() below), no custom
# image needed for this. UNVALIDATED exact tag -- confirm this image tag is
# still current in RunPod's template list before a real run; same
# not-doc-sourced-yet honesty convention as the finetune scripts' own
# UNVALIDATED hardware constants.
DEFAULT_IMAGE_NAME = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"

# Validated against a real run, 2026-08-01: a warm-started DPO run needs the
# base checkpoint cached (~62GB) AND the merged output (~65GB) present on
# /workspace at once -- 100GB hit a real "Disk quota exceeded" mid-merge.
# 200GB covers one warm-started run's cache + merged output with headroom.
DEFAULT_CONTAINER_DISK_GB = 100
DEFAULT_VOLUME_GB = 200
DEFAULT_VOLUME_MOUNT_PATH = "/workspace"

DEFAULT_SSH_PUBLIC_KEY_PATH = str(Path.home() / ".ssh" / "id_ed25519.pub")

POLL_INTERVAL_SECONDS = 15
DEFAULT_WAIT_TIMEOUT_SECONDS = 600  # 10 minutes for a pod to reach RUNNING with a public IP


# ── RunPod API helpers ──────────────────────────────────────────────────────
def _headers() -> dict:
    """
    Build the Authorization header every RunPod REST call needs. Fails loudly
    if RUNPOD_API_KEY is missing rather than sending an unauthenticated
    request and surfacing a confusing 401 -- same fail-loud discipline
    nova_hf_upload.py uses for HF_TOKEN.
    """
    if not RUNPOD_API_KEY:
        raise OSError("RUNPOD_API_KEY environment variable is not set -- required to call RunPod's API.")
    return {"Authorization": f"Bearer {RUNPOD_API_KEY}"}


def _load_ssh_public_key(path: str) -> str:
    """
    Read the local SSH public key that gets baked into the pod's PUBLIC_KEY
    env var. RunPod's official templates run a startup script that appends
    PUBLIC_KEY to ~/.ssh/authorized_keys and starts sshd on port 22 -- this
    is how SSH access gets configured, not a manual post-launch step.
    """
    key_path = Path(path)
    if not key_path.exists():
        raise FileNotFoundError(
            f"SSH public key not found at {path} -- pass --ssh-public-key-path pointing at "
            f"a real public key file, or the launched pod won't be reachable over SSH."
        )
    return key_path.read_text(encoding="utf-8").strip()


def launch_pod(
    name: str,
    gpu_type_id: str,
    cloud_type: str,
    interruptible: bool,
    image_name: str,
    container_disk_gb: int,
    volume_gb: int,
    ssh_public_key_path: str,
) -> dict:
    """
    POST /pods -- provisions a new pod. Real billing starts the moment RunPod
    reports it RUNNING, so this only ever runs when a human invokes the CLI's
    launch subcommand directly, never called from any other script.
    """
    ssh_public_key = _load_ssh_public_key(ssh_public_key_path)

    payload = {
        "name": name,
        "imageName": image_name,
        "cloudType": cloud_type,
        "gpuTypeIds": [gpu_type_id],
        "gpuCount": 1,
        "interruptible": interruptible,
        "containerDiskInGb": container_disk_gb,
        "volumeInGb": volume_gb,
        "volumeMountPath": DEFAULT_VOLUME_MOUNT_PATH,
        "ports": ["22/tcp"],
        "env": {"PUBLIC_KEY": ssh_public_key},
    }

    response = requests.post(f"{RUNPOD_REST_BASE_URL}/pods", headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    pod = response.json()

    print(f"Pod created: id={pod.get('id')} name={pod.get('name')} costPerHr=${pod.get('costPerHr')}")
    return pod


def get_pod(pod_id: str) -> dict:
    """Return one pod's current state -- GET /pods/{podId}."""
    response = requests.get(f"{RUNPOD_REST_BASE_URL}/pods/{pod_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def wait_for_pod_running(pod_id: str, timeout_seconds: int) -> dict:
    """
    Poll GET /pods/{podId} until the pod reports RUNNING with a real public IP
    and an SSH port mapping -- verifying the actual payload, not just trusting
    launch_pod()'s 200 response, same "verify the payload, not just the status
    code" discipline CLAUDE.md documents for the Omen deployment (a pod can
    accept the create request and still take real time to actually come up).
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        pod = get_pod(pod_id)
        port_mappings = pod.get("portMappings") or {}
        if pod.get("desiredStatus") == "RUNNING" and pod.get("publicIp") and "22" in port_mappings:
            ssh_command = f"ssh root@{pod['publicIp']} -p {port_mappings['22']}"
            print(f"Pod {pod_id} is RUNNING. Connect with:\n  {ssh_command}")
            return pod
        print(f"Pod {pod_id} status={pod.get('desiredStatus')} -- waiting...")
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Pod {pod_id} did not reach RUNNING with a public IP and SSH port within "
        f"{timeout_seconds}s -- check its status directly (status subcommand or the "
        f"RunPod web console) before assuming something is wrong."
    )


def stop_pod(pod_id: str) -> None:
    """
    POST /pods/{podId}/stop -- releases the GPU, keeps /workspace (the
    volume disk). Cheaper than terminate but still not free (volume storage
    keeps billing) -- terminate is the real "stop paying" action.
    """
    response = requests.post(f"{RUNPOD_REST_BASE_URL}/pods/{pod_id}/stop", headers=_headers(), timeout=30)
    response.raise_for_status()
    print(f"Pod {pod_id} stopped.")


def terminate_pod(pod_id: str) -> None:
    """DELETE /pods/{podId} -- permanently deletes the pod and its container/volume disks."""
    response = requests.delete(f"{RUNPOD_REST_BASE_URL}/pods/{pod_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    print(f"Pod {pod_id} terminated.")


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cmd_launch(args: argparse.Namespace) -> None:
    pod = launch_pod(
        name=args.name,
        gpu_type_id=args.gpu_type_id,
        cloud_type=args.cloud_type,
        interruptible=args.interruptible,
        image_name=args.image,
        container_disk_gb=args.container_disk_gb,
        volume_gb=args.volume_gb,
        ssh_public_key_path=args.ssh_public_key_path,
    )
    if args.wait:
        wait_for_pod_running(pod["id"], args.timeout_seconds)


def _cmd_status(args: argparse.Namespace) -> None:
    pod = get_pod(args.pod_id)
    print(
        f"id={pod.get('id')} status={pod.get('desiredStatus')} "
        f"publicIp={pod.get('publicIp')} portMappings={pod.get('portMappings')} "
        f"costPerHr=${pod.get('costPerHr')}"
    )


def _cmd_stop(args: argparse.Namespace) -> None:
    stop_pod(args.pod_id)


def _cmd_terminate(args: argparse.Namespace) -> None:
    if not args.yes:
        confirmation = input(f"Permanently terminate pod {args.pod_id}? This deletes its disks too. [y/N] ")
        if confirmation.strip().lower() != "y":
            print("Cancelled.")
            return
    terminate_pod(args.pod_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    launch_parser = subparsers.add_parser("launch", help="Provision a new A100 pod.")
    launch_parser.add_argument("--name", required=True, help="Human-readable pod name.")
    launch_parser.add_argument("--gpu-type-id", default=DEFAULT_GPU_TYPE_ID)
    launch_parser.add_argument("--cloud-type", default=DEFAULT_CLOUD_TYPE, choices=["SECURE", "COMMUNITY"])
    launch_parser.add_argument("--interruptible", action="store_true", default=DEFAULT_INTERRUPTIBLE)
    launch_parser.add_argument("--image", default=DEFAULT_IMAGE_NAME)
    launch_parser.add_argument("--container-disk-gb", type=int, default=DEFAULT_CONTAINER_DISK_GB)
    launch_parser.add_argument("--volume-gb", type=int, default=DEFAULT_VOLUME_GB)
    launch_parser.add_argument("--ssh-public-key-path", default=DEFAULT_SSH_PUBLIC_KEY_PATH)
    launch_parser.add_argument(
        "--no-wait", dest="wait", action="store_false", help="Don't block until the pod reaches RUNNING."
    )
    launch_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    launch_parser.set_defaults(func=_cmd_launch)

    status_parser = subparsers.add_parser("status", help="Show a pod's current state.")
    status_parser.add_argument("pod_id")
    status_parser.set_defaults(func=_cmd_status)

    stop_parser = subparsers.add_parser("stop", help="Stop a pod (keeps its volume disk, still billed).")
    stop_parser.add_argument("pod_id")
    stop_parser.set_defaults(func=_cmd_stop)

    terminate_parser = subparsers.add_parser("terminate", help="Permanently delete a pod and its disks.")
    terminate_parser.add_argument("pod_id")
    terminate_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    terminate_parser.set_defaults(func=_cmd_terminate)

    parsed_args = parser.parse_args()
    parsed_args.func(parsed_args)
