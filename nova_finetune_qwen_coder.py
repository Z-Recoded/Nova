# nova_finetune_qwen_coder.py
# QLoRA DPO fine-tune for Qwen2.5-Coder-32B-Instruct, Nova's RunPod-hosted coding
# sub-agent brain (nova_orchestrator_runpod.py). Reads (task, chosen_diff, diff)
# triples straight out of logs/coding_review_log.jsonl (the file
# nova_orchestrator._log_coding_review() writes to and nova_coding_corrector.py
# fills in the "chosen" half of), trains a QLoRA adapter with Unsloth, then
# exports merged safetensors.
#
# Structured identically to nova_finetune_phi4.py (pair loading -> dataset
# building -> model+trainer setup -> export -> main/CLI), but two real
# differences from that file, not oversights:
#
# 1. HARDWARE: Qwen2.5-Coder-32B needs ~18GB VRAM just for INT4 inference --
#    this does NOT run on the Aero's 8GB RTX 5070 the way Phi-4 Mini does.
#    This script assumes it is already running on a rented A100 (per ClickUp
#    86baf4e70's existing "validate on Colab -> production pass on
#    RunPod/Vast.ai A100" plan) -- it does not provision or rent anything
#    itself, same as nova_finetune_phi4.py assumes it's already on the Aero.
#
# 2. EXPORT FORMAT: the production endpoint this model serves from
#    (nova_remote_inference.RUNPOD_ENDPOINT_ID) is a RunPod serverless
#    worker-vLLM deployment serving a pre-quantized AWQ checkpoint -- not an
#    Ollama/GGUF setup like Phi-4 Mini. GGUF is the wrong export target here.
#    This script exports merged 16-bit safetensors and stops there --
#    re-quantizing to AWQ and redeploying onto RUNPOD_ENDPOINT_ID is a
#    separate, undesigned manual step, not built here.
#
# Usage:
#   python nova_finetune_qwen_coder.py --dry-run   # mechanical pipeline validation only --
#                                                   # a few real training steps on whatever
#                                                   # pairs exist today, output discarded.
#                                                   # Never treat this as a production model.
#                                                   # Requires real GPU access -- will not run
#                                                   # on the Aero.
#   python nova_finetune_qwen_coder.py             # real run -- refuses below MIN_REAL_PAIRS.

import argparse
import json
import os
from datetime import UTC, datetime

from datasets import Dataset

from nova_hf_upload import upload_merged_to_hub
from nova_orchestrator import CODING_REVIEW_LOG_PATH

# ── Constants — Qwen2.5-Coder-32B training config ──────────────────────────────
# UNVALIDATED, unlike nova_finetune_phi4.py's numbers (sourced from a real
# hardware/re-scope doc for that model). No equivalent doc exists yet for
# Qwen2.5-Coder-32B on rented hardware. These are reasoned starting points
# only -- confirm against real VRAM usage on the actual rented A100 before a
# production run, not just this file's own comments.
RAW_BASE_MODEL_NAME = "unsloth/Qwen2.5-Coder-32B-Instruct-bnb-4bit"  # verified to exist on Hugging Face, 2026-07-29
SFT_MERGED_OUTPUT_DIR = (
    "finetune_output/qwen-coder-32b-sft-merged"  # must match nova_finetune_qwen_coder_sft.py's own constant
)
SFT_HUB_REPO_ID = (
    "zrecoded/nova-qwen-coder-32b-sft-merged"  # must match nova_finetune_qwen_coder_sft.py's own HF_HUB_REPO_ID
)
MAX_SEQ_LENGTH = 8192  # diffs (esp. full-file rewrites) may need more than a single chat turn
LORA_RANK = 16  # deliberately lower than Phi-4 Mini's 32 -- the 32B base already eats more VRAM budget
LORA_ALPHA = 16
LEARNING_RATE = 1e-4  # lower end of Phi-4's 1e-4..2e-4 range -- larger base models often want a lower LR
BATCH_SIZE = 1
GRAD_ACCUM_STEPS = 8
NUM_EPOCHS = 3
DPO_BETA = 0.1

# Marvin's real call, 2026-07-30: start training on whatever real corrected
# pairs exist now (5 as of this writing) and keep building the pair count up
# from there, rather than waiting on a fixed floor before ever running DPO --
# unlike the old 100-pair placeholder (unsourced, structure-parity-only with
# nova_finetune_phi4.py), this number matches real data on hand. Still
# refuses a true zero-pair run.
MIN_REAL_PAIRS = 5
DRY_RUN_MAX_STEPS = 3

ADAPTER_OUTPUT_DIR = "finetune_output/qwen-coder-32b-dpo-adapter"
MERGED_OUTPUT_DIR = "finetune_output/qwen-coder-32b-dpo-merged"

# Marvin's call, 2026-07-29: private HF Hub repo as the hand-off point so a
# rented A100 pod can be stopped right after training instead of waiting on
# a slow pod-to-home transfer for a ~64GB checkpoint. See nova_hf_upload.py.
HF_HUB_REPO_ID = "zrecoded/nova-qwen-coder-32b-dpo-merged"

# 86bb7quga: same reasoning as nova_finetune_qwen_coder_sft.py's own comment --
# no Tailscale access to the Omen's MLflow server from this pod, and no
# `mlflow` import here to avoid repeating the real transformers/datasets
# conflict already hit once installing mlflow's client locally. Small local
# JSON instead, rides along with the existing HF Hub upload.
MLFLOW_METADATA_FILENAME = "mlflow_run_metadata.json"


def _resolve_base_model_name() -> str:
    """
    Use the SFT stage's (nova_finetune_qwen_coder_sft.py) merged checkpoint
    as this DPO stage's starting point if it's available -- standard SFT-
    warm-start-then-DPO-refine practice, better than DPO alone on a barely-
    nudged base model. Checks local disk first (same pod, no download
    needed), then the SFT stage's private HF Hub repo (2026-07-29 decision --
    a fresh DPO pod started after the SFT pod was already stopped won't have
    the local directory at all, only the Hub upload; checking local-only
    here would silently skip the warm-start with no warning in exactly the
    pod-lifecycle this pipeline is now designed around). Falls back to the
    raw Unsloth checkpoint only if neither exists, so this script's original
    behavior is unchanged for anyone who runs DPO without ever running SFT.
    """
    if os.path.isdir(SFT_MERGED_OUTPUT_DIR):
        print(f"Using local SFT output as base model: {SFT_MERGED_OUTPUT_DIR}")
        return SFT_MERGED_OUTPUT_DIR

    from huggingface_hub import HfApi

    # Checking repo_info() alone isn't enough -- nova_hf_upload.py's
    # create_repo(exist_ok=True) means the repo can exist but be empty (real
    # case hit live 2026-07-29: the target repo was pre-created to verify
    # write access before any SFT run ever happened). config.json is always
    # written by save_pretrained_merged(), so its presence is a reliable
    # signal a real checkpoint actually landed here, not just an empty shell.
    try:
        remote_files = HfApi().list_repo_files(SFT_HUB_REPO_ID)
    except Exception:
        remote_files = []

    if "config.json" in remote_files:
        print(f"No local SFT output found -- using the SFT stage's HF Hub repo as base model: {SFT_HUB_REPO_ID}")
        return SFT_HUB_REPO_ID

    print(
        f"No SFT output found locally or on the Hub -- falling back to the raw base checkpoint: {RAW_BASE_MODEL_NAME}"
    )
    return RAW_BASE_MODEL_NAME


BASE_MODEL_NAME = _resolve_base_model_name()


# ── DPO pair loading ───────────────────────────────────────────────────────────
def load_dpo_pairs(path: str) -> list[dict]:
    """
    Read every review-flagged, corrected entry out of coding_review_log.jsonl
    and turn it into a {prompt, chosen, rejected} triple. "chosen" is
    nova_coding_corrector.py's Claude-written corrected diff; "rejected" is
    Qwen2.5-Coder-32B's original flawed diff. Entries the reviewer approved
    (nothing to correct) or that haven't been corrected yet
    (chosen_diff missing) are skipped, not padded -- matches
    nova_finetune_phi4.py's own load_dpo_pairs() convention exactly.
    """
    if not os.path.exists(path):
        return []

    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("approved", True):
                continue
            chosen_diff = entry.get("chosen_diff")
            if not chosen_diff:
                continue
            pairs.append({"prompt": entry["task"], "chosen": chosen_diff, "rejected": entry["diff"]})

    return pairs


def build_dataset(pairs: list[dict], tokenizer) -> Dataset:
    """
    Apply Qwen2.5-Coder's real chat template to each task description so the
    trainer sees exactly the same formatting the RunPod endpoint uses at
    inference time. chosen/rejected stay as plain diff text -- TRL's
    DPOTrainer appends them directly after the formatted prompt.
    """
    formatted = []
    for pair in pairs:
        prompt_text = tokenizer.apply_chat_template(
            [{"role": "user", "content": pair["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        formatted.append({"prompt": prompt_text, "chosen": pair["chosen"], "rejected": pair["rejected"]})
    return Dataset.from_list(formatted)


# ── Model + trainer setup ──────────────────────────────────────────────────────
def load_model_and_tokenizer():
    """
    Load the pre-quantized 4-bit Qwen2.5-Coder-32B checkpoint via Unsloth and
    wrap it with a LoRA adapter. Kept as its own function (not inlined into
    main) so a future base-model swap only touches this one call, same
    precedent as nova_finetune_phi4.py's own load_model_and_tokenizer().
    """
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        lora_alpha=LORA_ALPHA,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
    )
    return model, tokenizer


def build_trainer(model, tokenizer, dataset: Dataset, dry_run: bool):
    """
    Wire up TRL's DPOTrainer with the config above. dry_run caps training at
    a handful of real steps (DRY_RUN_MAX_STEPS) purely to prove the
    mechanical pipeline (data -> tokenize -> forward -> backward -> optimizer
    step) runs cleanly on the rented hardware -- it is not a quality signal
    and the resulting adapter is never saved.
    """
    from trl import DPOConfig, DPOTrainer

    config = DPOConfig(
        output_dir=ADAPTER_OUTPUT_DIR,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM_STEPS,
        learning_rate=LEARNING_RATE,
        num_train_epochs=NUM_EPOCHS,
        beta=DPO_BETA,
        gradient_checkpointing=True,
        max_length=MAX_SEQ_LENGTH,
        max_steps=DRY_RUN_MAX_STEPS if dry_run else -1,
        logging_steps=1,
        save_strategy="no" if dry_run else "epoch",
        report_to=[],
    )
    return DPOTrainer(model=model, args=config, processing_class=tokenizer, train_dataset=dataset)


# ── Export ─────────────────────────────────────────────────────────────────────
def export_merged(model, tokenizer) -> None:
    """
    Merge the LoRA adapter and save full merged safetensors -- NOT GGUF.
    Unlike Phi-4 Mini (served locally via Ollama), the production endpoint
    this model backs (RUNPOD_ENDPOINT_ID in nova_remote_inference.py) is a
    RunPod serverless worker-vLLM deployment serving a pre-quantized AWQ
    checkpoint. GGUF/Ollama is the wrong export target for that serving
    stack. Re-quantizing this merged output to AWQ and redeploying it as the
    model backing RUNPOD_ENDPOINT_ID is a separate, currently-undesigned
    manual step -- flagged as an open question, not assumed here.
    """
    os.makedirs(MERGED_OUTPUT_DIR, exist_ok=True)
    model.save_pretrained_merged(MERGED_OUTPUT_DIR, tokenizer, save_method="merged_16bit")
    print(f"Merged model exported to {MERGED_OUTPUT_DIR} (merged_16bit safetensors, not GGUF)")


# ── MLflow metadata (86bb7quga) ─────────────────────────────────────────────────
def _first_and_last(log_history: list[dict], key: str) -> tuple[float | None, float | None]:
    """
    Scan TRL's own per-step log_history for the first and last entry that
    logged `key` -- DPOTrainer logs 'loss' and 'rewards/accuracies' during
    training via logging_steps, but neither lands in trainer.train()'s own
    aggregated .metrics the way train_loss does for the SFT stage. Returns
    (None, None) if the key never appeared (e.g. a dry run too short to log).
    """
    values = [entry[key] for entry in log_history if key in entry]
    if not values:
        return None, None
    return values[0], values[-1]


def _write_mlflow_metadata(output_dir: str, trainer, num_pairs: int, started_at: datetime) -> None:
    """
    Write a small local JSON summarizing this real run's real hyperparameters
    and metrics, for nova_mlflow_ingest.py to pick up later from the Aero.
    """
    loss_start, loss_end = _first_and_last(trainer.state.log_history, "loss")
    reward_acc_start, reward_acc_end = _first_and_last(trainer.state.log_history, "rewards/accuracies")

    metrics = {}
    if loss_start is not None:
        metrics["loss_start"] = loss_start
        metrics["loss_end"] = loss_end
    if reward_acc_end is not None:
        metrics["reward_accuracy"] = reward_acc_end

    metadata = {
        "run_name": f"dpo_full_{started_at.strftime('%Y-%m-%d')}",
        "stage": "dpo",
        "hf_repo_id": HF_HUB_REPO_ID,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "params": {
            "base_model": BASE_MODEL_NAME,
            "lora_rank": LORA_RANK,
            "lora_alpha": LORA_ALPHA,
            "learning_rate": LEARNING_RATE,
            "batch_size": BATCH_SIZE,
            "grad_accum_steps": GRAD_ACCUM_STEPS,
            "num_epochs": NUM_EPOCHS,
            "dpo_beta": DPO_BETA,
            "num_corrected_pairs": num_pairs,
        },
        "metrics": metrics,
    }
    metadata_path = os.path.join(output_dir, MLFLOW_METADATA_FILENAME)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote MLflow metadata to {metadata_path} (will upload alongside the merged checkpoint)")


# ── Main ─────────────────────────────────────────────────────────────────────────
def run(dry_run: bool) -> None:
    run_started_at = datetime.now(UTC)
    pairs = load_dpo_pairs(CODING_REVIEW_LOG_PATH)
    print(f"Loaded {len(pairs)} corrected DPO pair(s) from {CODING_REVIEW_LOG_PATH}.")

    if not pairs:
        print("No corrected pairs found — nothing to train on. Run nova_coding_corrector.py first.")
        return

    if not dry_run and len(pairs) < MIN_REAL_PAIRS:
        print(
            f"Only {len(pairs)} corrected pairs exist — MIN_REAL_PAIRS is currently {MIN_REAL_PAIRS} "
            f"(a placeholder, not a doc-sourced number for this model yet). Refusing to run a real "
            f"fine-tune on this little data. Use --dry-run to validate the pipeline mechanically instead."
        )
        return

    if dry_run:
        print(
            f"Dry run: {DRY_RUN_MAX_STEPS} real training steps to validate the "
            f"pipeline mechanically. This is NOT a production fine-tune — the "
            f"resulting adapter will not be saved or exported. Requires real GPU "
            f"access — this will not run on the Aero."
        )

    model, tokenizer = load_model_and_tokenizer()
    dataset = build_dataset(pairs, tokenizer)
    trainer = build_trainer(model, tokenizer, dataset, dry_run)

    trainer.train()

    if dry_run:
        print("Dry run complete — pipeline validated, no adapter saved.")
        return

    trainer.save_model(ADAPTER_OUTPUT_DIR)
    print(f"Adapter saved to {ADAPTER_OUTPUT_DIR}")
    export_merged(model, tokenizer)
    _write_mlflow_metadata(MERGED_OUTPUT_DIR, trainer, len(pairs), run_started_at)
    upload_merged_to_hub(MERGED_OUTPUT_DIR, HF_HUB_REPO_ID)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a few real training steps to validate the pipeline only — no save/export.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
