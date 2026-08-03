# nova_runpod_endpoint_deploy.py
# Creates a NEW RunPod serverless worker-vLLM endpoint serving the
# AWQ-quantized checkpoint (zrecoded/nova-qwen-coder-32b-awq, output of
# nova_quantize_qwen_coder_awq.py), for a manual sanity check and a
# nova_coding_eval.py run BEFORE nova_remote_inference.py's MODEL_NAME /
# RUNPOD_ENDPOINT_ID constants are ever pointed at it.
#
# Deliberately does NOT touch the existing production endpoint
# (RUNPOD_ENDPOINT_ID = "2ldulpirwqz1vp" in nova_remote_inference.py) -- that
# keeps serving the stock model uninterrupted the entire time this script
# runs. Rollback from the new endpoint back to stock is always just
# reverting two constants in nova_remote_inference.py, never anything this
# script does.
#
# Two-resource model, confirmed live 2026-08-01 against docs.runpod.io: a
# Template (Docker image + env vars) must exist before an Endpoint can be
# created -- env vars go on the template, not directly on POST /endpoints.
# This script creates both, in that order. Same raw-requests REST pattern as
# nova_runpod_pod_launch.py (requests is already a dependency, no runpod SDK,
# .env-sourced RUNPOD_API_KEY, fail-loud on missing key).
#
# GPU tier: defaults to matching the current production endpoint's tier
# (H100 SXM) for an apples-to-apples eval comparison against Claude's
# baseline -- confirmed with Marvin, 2026-08-01 -- NOT because AWQ needs this
# much VRAM (nova_finetune_qwen_coder.py's own header notes ~18GB is enough
# for INT4 inference of this model). Overridable via --gpu-type-id.
#
# Scale-to-zero: workersMin defaults to 0 -- confirmed live against
# docs.runpod.io that workersMin=0 means zero workers run (and zero cost is
# incurred) while idle; a worker only spins up (with real but bounded
# cold-start latency) once a request arrives.
#
# Usage:
#   python nova_runpod_endpoint_deploy.py deploy --name nova-qwen-coder-awq-v1
#   python nova_runpod_endpoint_deploy.py status <endpoint_id>
#   python nova_runpod_endpoint_deploy.py delete <endpoint_id>

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")
RUNPOD_REST_BASE_URL = "https://rest.runpod.io/v1"

# Confirmed live 2026-08-01 directly against Docker Hub's tag list for
# runpod/worker-v1-vllm -- no "stable"-named tag exists (an earlier guess
# assumed one did); tags are plain semver (v2.X.X). v2.23.0 was the newest
# real tag at deploy time (pushed 2026-07-29). Tags move -- re-check
# hub.docker.com/r/runpod/worker-v1-vllm/tags before trusting this on faith
# for a future deploy, same not-doc-sourced-yet honesty convention as
# nova_runpod_pod_launch.py's own DEFAULT_IMAGE_NAME comment.
DEFAULT_WORKER_IMAGE = "runpod/worker-v1-vllm:v2.23.0"

DEFAULT_MODEL_REPO_ID = "zrecoded/nova-qwen-coder-32b-awq"  # nova_quantize_qwen_coder_awq.py's AWQ_HUB_REPO_ID

# Real bug found live 2026-08-01: worker startup failed with "Quantization
# method specified in the model config (compressed-tensors) does not match
# the quantization method specified in the `quantization` argument (awq)".
# llm-compressor (nova_quantize_qwen_coder_awq.py) saves checkpoints in
# vLLM's native compressed-tensors format, NOT the older AutoAWQ checkpoint
# format the production endpoint's stock model (quantized by a different,
# now-deprecated tool) uses -- "awq" was copied from that endpoint's config
# without checking it actually matched this checkpoint's real format.
DEFAULT_QUANTIZATION = "compressed-tensors"

# Matches CODING_AGENT_CONTEXT_WINDOW_TOKENS in nova_orchestrator_runpod.py --
# the real context window the coding-agent lane already assumes this endpoint
# has. Kept identical here so nova_coding_eval.py's comparison isn't
# confounded by a context-window change alongside the model swap.
DEFAULT_MAX_MODEL_LEN = 32768

DEFAULT_GPU_MEMORY_UTILIZATION = 0.95  # worker-vLLM's own documented default

# Confirmed live 2026-08-01 against the REST API's own OpenAPI schema enum
# (no dedicated GET /gputypes endpoint exists on this API -- checked and
# ruled out, unlike nova_runpod_pod_launch.py's Pod-side equivalent).
DEFAULT_GPU_TYPE_ID = "NVIDIA H100 80GB HBM3"

DEFAULT_WORKERS_MIN = 0  # scale-to-zero -- zero cost while idle
DEFAULT_WORKERS_MAX = 1  # eval endpoint, not a production traffic target
DEFAULT_IDLE_TIMEOUT_SECONDS = 5  # RunPod's own default
DEFAULT_CONTAINER_DISK_GB = 75  # headroom above the ~16-20GB AWQ checkpoint + image layers


# ── RunPod API helpers ──────────────────────────────────────────────────────
def _headers() -> dict:
    """Same fail-loud discipline as nova_runpod_pod_launch.py's own _headers()."""
    if not RUNPOD_API_KEY:
        raise OSError("RUNPOD_API_KEY environment variable is not set -- required to call RunPod's API.")
    return {"Authorization": f"Bearer {RUNPOD_API_KEY}", "Content-Type": "application/json"}


def _resolve_hf_token() -> str:
    """
    Fails loudly if HF_TOKEN is missing -- without it the worker can't pull
    the private AWQ repo and would fail at cold-start with a confusing
    error, same "fail loud, not confusing" discipline as
    nova_hf_upload.upload_merged_to_hub()'s own HF_TOKEN check.
    """
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise OSError(
            "HF_TOKEN environment variable is not set -- required so the worker can pull the private AWQ repo."
        )
    return token


def create_template(
    name: str,
    image_name: str,
    model_repo_id: str,
    quantization: str | None,
    max_model_len: int,
    gpu_memory_utilization: float,
    container_disk_gb: int,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """
    POST /templates -- creates the Docker image + env var bundle an endpoint
    references. isServerless=True marks this as a serverless template (not a
    Pod template) -- confirmed required against docs.runpod.io's template schema.

    quantization=None omits the QUANTIZATION env var entirely rather than
    passing an empty/placeholder string -- needed to deploy an unquantized
    (bf16) checkpoint, e.g. the pre-AWQ merged fine-tune
    (zrecoded/nova-qwen-coder-32b-dpo-merged): vLLM auto-detects "no
    quantization" from the checkpoint's own config.json when the
    `--quantization` flag isn't passed at all, but errors if given a
    placeholder value that doesn't match any real quantization method.

    extra_env passes through arbitrary additional env vars unchanged --
    confirmed via runpod-workers/worker-vllm's own README that any env var
    matching a valid vLLM AsyncEngineArgs field name (uppercased) is applied
    automatically, so this needs no special-casing per model. Needed for a
    model requiring flags this script doesn't otherwise expose, e.g.
    Devstral's Mistral-native checkpoint format
    (TOKENIZER_MODE/CONFIG_FORMAT/LOAD_FORMAT=mistral) and native
    tool-calling (TOOL_CALL_PARSER=mistral, ENABLE_AUTO_TOOL_CHOICE=true).
    Applied after the explicit keys below so an explicit --extra-env can
    override one of them if a future caller ever needs to.
    """
    env = {
        "MODEL_NAME": model_repo_id,
        "HF_TOKEN": _resolve_hf_token(),
        "MAX_MODEL_LEN": str(max_model_len),
        "GPU_MEMORY_UTILIZATION": str(gpu_memory_utilization),
    }
    if quantization:
        env["QUANTIZATION"] = quantization
    if extra_env:
        env.update(extra_env)

    payload = {
        "name": name,
        "imageName": image_name,
        "isServerless": True,
        "containerDiskInGb": container_disk_gb,
        "env": env,
    }
    response = requests.post(f"{RUNPOD_REST_BASE_URL}/templates", headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    template = response.json()
    print(f"Template created: id={template.get('id')} name={template.get('name')}")
    return template


def create_endpoint(
    name: str,
    template_id: str,
    gpu_type_id: str,
    workers_min: int,
    workers_max: int,
    idle_timeout_seconds: int,
) -> dict:
    """
    POST /endpoints -- creates the actual autoscaling serverless endpoint,
    referencing template_id (env vars already live on the template, not
    passed here).
    """
    payload = {
        "name": name,
        "templateId": template_id,
        "computeType": "GPU",
        "gpuTypeIds": [gpu_type_id],
        "gpuCount": 1,
        "workersMin": workers_min,
        "workersMax": workers_max,
        "idleTimeout": idle_timeout_seconds,
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 4,
    }
    response = requests.post(f"{RUNPOD_REST_BASE_URL}/endpoints", headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    endpoint = response.json()
    print(f"Endpoint created: id={endpoint.get('id')} name={endpoint.get('name')}")
    return endpoint


def get_endpoint(endpoint_id: str) -> dict:
    """GET /endpoints/{id} -- verify the real payload, not just a 200 (CLAUDE.md's Omen deployment lesson)."""
    response = requests.get(f"{RUNPOD_REST_BASE_URL}/endpoints/{endpoint_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def delete_endpoint(endpoint_id: str) -> None:
    """DELETE /endpoints/{id} -- permanently deletes the endpoint (not just scales it to zero workers)."""
    response = requests.delete(f"{RUNPOD_REST_BASE_URL}/endpoints/{endpoint_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    print(f"Endpoint {endpoint_id} deleted.")


# ── CLI ──────────────────────────────────────────────────────────────────────
def _parse_extra_env(pairs: list[str] | None) -> dict[str, str]:
    """Turns repeated --extra-env KEY=VALUE args into a dict. Fails loudly on a malformed pair."""
    extra_env = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"--extra-env expects KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        extra_env[key] = value
    return extra_env


def _cmd_deploy(args: argparse.Namespace) -> None:
    quantization = None if args.quantization.lower() == "none" else args.quantization
    template = create_template(
        name=f"{args.name}-template",
        image_name=args.image,
        model_repo_id=args.model_repo_id,
        quantization=quantization,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        container_disk_gb=args.container_disk_gb,
        extra_env=_parse_extra_env(args.extra_env),
    )
    endpoint = create_endpoint(
        name=args.name,
        template_id=template["id"],
        gpu_type_id=args.gpu_type_id,
        workers_min=args.workers_min,
        workers_max=args.workers_max,
        idle_timeout_seconds=args.idle_timeout_seconds,
    )
    print(
        f"\nEndpoint {endpoint['id']} created but NOT wired into nova_remote_inference.py yet. "
        f"Sanity-check it directly first (e.g. a runsync call with a trivial prompt) before "
        f"editing RUNPOD_ENDPOINT_ID/MODEL_NAME there."
    )


def _cmd_status(args: argparse.Namespace) -> None:
    endpoint = get_endpoint(args.endpoint_id)
    print(
        f"id={endpoint.get('id')} name={endpoint.get('name')} "
        f"workersMin={endpoint.get('workersMin')} workersMax={endpoint.get('workersMax')}"
    )


def _cmd_delete(args: argparse.Namespace) -> None:
    if not args.yes:
        confirmation = input(f"Permanently delete endpoint {args.endpoint_id}? [y/N] ")
        if confirmation.strip().lower() != "y":
            print("Cancelled.")
            return
    delete_endpoint(args.endpoint_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy_parser = subparsers.add_parser("deploy", help="Create a new template + serverless endpoint.")
    deploy_parser.add_argument("--name", required=True, help="Human-readable endpoint name.")
    deploy_parser.add_argument("--image", default=DEFAULT_WORKER_IMAGE)
    deploy_parser.add_argument("--model-repo-id", default=DEFAULT_MODEL_REPO_ID)
    deploy_parser.add_argument("--quantization", default=DEFAULT_QUANTIZATION)
    deploy_parser.add_argument("--max-model-len", type=int, default=DEFAULT_MAX_MODEL_LEN)
    deploy_parser.add_argument("--gpu-memory-utilization", type=float, default=DEFAULT_GPU_MEMORY_UTILIZATION)
    deploy_parser.add_argument("--gpu-type-id", default=DEFAULT_GPU_TYPE_ID)
    deploy_parser.add_argument("--workers-min", type=int, default=DEFAULT_WORKERS_MIN)
    deploy_parser.add_argument("--workers-max", type=int, default=DEFAULT_WORKERS_MAX)
    deploy_parser.add_argument("--idle-timeout-seconds", type=int, default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    deploy_parser.add_argument("--container-disk-gb", type=int, default=DEFAULT_CONTAINER_DISK_GB)
    deploy_parser.add_argument(
        "--extra-env",
        action="append",
        metavar="KEY=VALUE",
        help="Additional template env var, repeatable (e.g. --extra-env TOOL_CALL_PARSER=mistral).",
    )
    deploy_parser.set_defaults(func=_cmd_deploy)

    status_parser = subparsers.add_parser("status", help="Show an endpoint's current state.")
    status_parser.add_argument("endpoint_id")
    status_parser.set_defaults(func=_cmd_status)

    delete_parser = subparsers.add_parser("delete", help="Permanently delete an endpoint.")
    delete_parser.add_argument("endpoint_id")
    delete_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    delete_parser.set_defaults(func=_cmd_delete)

    parsed_args = parser.parse_args()
    parsed_args.func(parsed_args)
