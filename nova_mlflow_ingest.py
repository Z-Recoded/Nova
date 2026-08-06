# nova_mlflow_ingest.py
# Reads a real completed fine-tune run's mlflow_run_metadata.json (written by
# nova_finetune_qwen_coder_sft.py / nova_finetune_qwen_coder.py at the end of
# a real run, uploaded to HF Hub alongside the merged checkpoint) and logs it
# into MLflow, running on the Omen (86bb7quga). The rented RunPod pod those
# scripts run on has no Tailscale access to reach the Omen directly, and
# importing the `mlflow` client package into the training script itself would
# risk repeating the real transformers/datasets dependency conflict already
# hit once installing mlflow locally -- so the metadata rides home via HF Hub
# instead, the same hand-off point nova_hf_upload.py already established for
# the checkpoint itself. Run this from the Aero (or anywhere on the tailnet)
# after a real training run finishes -- never on the rented pod.
#
# Usage:
#   python nova_mlflow_ingest.py --repo zrecoded/nova-qwen-coder-32b-dpo-merged
#   python nova_mlflow_ingest.py --local-path path/to/mlflow_run_metadata.json

import argparse
import json
from datetime import datetime

from mlflow import MlflowClient

MLFLOW_TRACKING_URI = "http://100.114.197.117:5000"
MLFLOW_EXPERIMENT_NAME = "qwen-coder-32b-finetune"  # must match nova_mlflow_backfill's own experiment name
MLFLOW_METADATA_FILENAME = "mlflow_run_metadata.json"  # must match both finetune scripts' own constant


def _fetch_metadata_from_hub(repo_id: str) -> dict:
    """Download the metadata JSON that rode along with the merged checkpoint upload."""
    from huggingface_hub import hf_hub_download

    local_path = hf_hub_download(repo_id=repo_id, filename=MLFLOW_METADATA_FILENAME)
    with open(local_path, encoding="utf-8") as f:
        return json.load(f)


def log_run(metadata: dict) -> str:
    """
    Log one real completed fine-tune run's metadata into MLflow, using the
    real start/end timestamps the training script captured live -- unlike the
    one-time historical backfill, these are genuine wall-clock times, not a
    date-only placeholder.
    """
    client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
    experiment = client.get_experiment_by_name(MLFLOW_EXPERIMENT_NAME)
    experiment_id = experiment.experiment_id if experiment else client.create_experiment(MLFLOW_EXPERIMENT_NAME)

    started_ms = int(datetime.fromisoformat(metadata["started_at"]).timestamp() * 1000)
    finished_ms = int(datetime.fromisoformat(metadata["finished_at"]).timestamp() * 1000)

    tags = {
        "mlflow.runName": metadata["run_name"],
        "hf_repo": metadata["hf_repo_id"],
        "backfilled": "false",
    }
    run = client.create_run(experiment_id, start_time=started_ms, tags=tags)
    run_id = run.info.run_id
    for key, value in metadata["params"].items():
        client.log_param(run_id, key, value)
    for key, value in metadata["metrics"].items():
        if isinstance(value, (int, float)):
            client.log_metric(run_id, key, value)
    client.set_terminated(run_id, status="FINISHED", end_time=finished_ms)

    print(f"Logged '{metadata['run_name']}' -> {MLFLOW_TRACKING_URI}/#/experiments/{experiment_id}/runs/{run_id}")
    return run_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="HF Hub repo ID to pull mlflow_run_metadata.json from")
    source.add_argument("--local-path", help="Local path to an mlflow_run_metadata.json file")
    args = parser.parse_args()

    run_metadata = _fetch_metadata_from_hub(args.repo) if args.repo else None
    if run_metadata is None:
        with open(args.local_path, encoding="utf-8") as f:
            run_metadata = json.load(f)

    log_run(run_metadata)
