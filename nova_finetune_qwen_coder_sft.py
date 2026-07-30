# nova_finetune_qwen_coder_sft.py
# SFT warm-start stage for Qwen2.5-Coder-32B-Instruct, Nova's RunPod-hosted coding
# sub-agent brain. Trains on KodCode-V1 (Hugging Face) -- 487K real, unit-test-
# verified Python question/solution/test triplets, notably benchmarked directly
# against this exact base model in its own paper -- before the DPO stage
# (nova_finetune_qwen_coder.py) refines on Nova's own much smaller set of real
# corrected-diff pairs. Standard practice: SFT warm-start raises general
# competence, DPO then refines specific preferences -- DPO alone on a barely-
# nudged base model, especially with only a handful of real pairs, is a weaker
# starting point.
#
# License: KodCode-V1 is CC BY-NC 4.0 (non-commercial) -- fine for Nova as a
# personal tool, not for any commercial use of a model trained on it.
#
# Real honesty check: none of KodCode's 12 subsets are about editing an
# existing multi-file codebase -- they're self-contained "write a solution to
# this question" tasks, closer to competitive programming than to Nova's real
# coding-agent job of inserting a route near existing code without duplicating
# it, or respecting explicit scope instructions. This should raise Qwen's
# general Python competence and reduce raw syntax/logic errors, but it does
# NOT directly train the specific defect patterns the 2026-07-29 held-out eval
# found (dead-code duplication, scope violations, incomplete multi-file
# edits) -- the DPO stage is still what targets those.
#
# Same two real differences from nova_finetune_phi4.py as the DPO stage:
# 1. HARDWARE: needs a rented A100, not the Aero's 8GB RTX 5070. Assumes it's
#    already running on the right box, same as every other script here.
# 2. EXPORT FORMAT: exports merged safetensors, not GGUF -- see
#    nova_finetune_qwen_coder.py's own header comment for why.
#
# Usage:
#   python nova_finetune_qwen_coder_sft.py --dry-run   # mechanical pipeline validation
#                                                       # only -- a few real training
#                                                       # steps, output discarded.
#                                                       # Requires real GPU access.
#   python nova_finetune_qwen_coder_sft.py             # real run.

import argparse
import os

from datasets import Dataset, load_dataset

# ── Constants — KodCode-V1 SFT config ──────────────────────────────────────────
KODCODE_DATASET_ID = "KodCode/KodCode-V1"
KODCODE_SPLIT = "train"  # deliberately excludes the dataset's own
# "use_with_caution" split -- its name is the
# dataset authors' own quality flag, not a guess.

# UNVALIDATED, same discipline as nova_finetune_qwen_coder.py's own numbers --
# no doc exists for this model on rented hardware yet. Confirm against real
# VRAM usage on the actual rented A100 before a production run.
BASE_MODEL_NAME = "unsloth/Qwen2.5-Coder-32B-Instruct-bnb-4bit"  # same base as the DPO stage
MAX_SEQ_LENGTH = 8192  # shared with the DPO stage's own placeholder value
LORA_RANK = 16  # shared with the DPO stage
LORA_ALPHA = 16
LEARNING_RATE = 2e-4  # UNVALIDATED -- SFT conventionally tolerates a higher LR than DPO
# refinement; upper end of Phi-4's own 1e-4..2e-4 range, distinct
# from the DPO stage's 1e-4.
BATCH_SIZE = 1
GRAD_ACCUM_STEPS = 8
NUM_EPOCHS = 1  # UNVALIDATED -- one pass over a subset first; multiple epochs over
# hundreds of thousands of real rows on a rented A100 is a real cost
# decision, not assumed here.

# Marvin's call, 2026-07-29: bounded random sample across all 12 subsets (not
# biased toward any category) for a first pass -- simple, keeps a first real
# GPU run's time/cost bounded. Revisit subset composition later if the plain
# sample doesn't move the needle enough to be worth it.
SFT_SUBSET_SIZE = 20000
SFT_SHUFFLE_SEED = 42

DRY_RUN_MAX_STEPS = 3
SFT_ADAPTER_OUTPUT_DIR = "finetune_output/qwen-coder-32b-sft-adapter"
SFT_MERGED_OUTPUT_DIR = "finetune_output/qwen-coder-32b-sft-merged"


# ── Example loading ─────────────────────────────────────────────────────────────
def load_sft_examples() -> Dataset:
    """
    Pull a bounded, shuffled random sample of KodCode-V1's "train" split from
    the Hugging Face Hub and map each row to a {prompt, completion} pair --
    prompt is the real question text, completion is the real (unit-test-
    verified by the dataset's own authors) solution. test/gpt_difficulty/
    subset fields are available in the raw row but unused in this v1 --
    a real fast-follow could filter/stratify by them, not built here.
    """
    raw = load_dataset(KODCODE_DATASET_ID, split=KODCODE_SPLIT)
    raw = raw.shuffle(seed=SFT_SHUFFLE_SEED).select(range(min(SFT_SUBSET_SIZE, len(raw))))
    return raw.map(
        lambda row: {"prompt": row["question"], "completion": row["solution"]},
        remove_columns=raw.column_names,
    )


def build_dataset(examples: Dataset, tokenizer) -> Dataset:
    """
    Apply Qwen2.5-Coder's real chat template to each question so the trainer
    sees exactly the same formatting the RunPod endpoint uses at inference
    time. completion stays as plain solution text -- TRL's SFTTrainer appends
    it directly after the formatted prompt. Same pattern as the DPO stage's
    own build_dataset().
    """
    formatted = []
    for example in examples:
        prompt_text = tokenizer.apply_chat_template(
            [{"role": "user", "content": example["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        formatted.append({"prompt": prompt_text, "completion": example["completion"]})
    return Dataset.from_list(formatted)


# ── Model + trainer setup ──────────────────────────────────────────────────────
def load_model_and_tokenizer():
    """
    Load the pre-quantized 4-bit Qwen2.5-Coder-32B checkpoint via Unsloth and
    wrap it with a LoRA adapter. Duplicated from nova_finetune_qwen_coder.py's
    own version rather than imported -- this codebase's existing precedent
    (nova_corrector.py/nova_finetune_phi4.py) doesn't share a config module
    between related-but-separate scripts either.
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
    Wire up TRL's SFTTrainer with the config above. dry_run caps training at
    a handful of real steps (DRY_RUN_MAX_STEPS) purely to prove the
    mechanical pipeline (data -> tokenize -> forward -> backward -> optimizer
    step) runs cleanly on the rented hardware -- same non-quality-signal
    convention as the DPO stage's own build_trainer().
    """
    from trl import SFTConfig, SFTTrainer

    config = SFTConfig(
        output_dir=SFT_ADAPTER_OUTPUT_DIR,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM_STEPS,
        learning_rate=LEARNING_RATE,
        num_train_epochs=NUM_EPOCHS,
        gradient_checkpointing=True,
        max_length=MAX_SEQ_LENGTH,
        max_steps=DRY_RUN_MAX_STEPS if dry_run else -1,
        logging_steps=1,
        save_strategy="no" if dry_run else "epoch",
        report_to=[],
    )
    return SFTTrainer(model=model, args=config, processing_class=tokenizer, train_dataset=dataset)


# ── Export ─────────────────────────────────────────────────────────────────────
def export_merged(model, tokenizer) -> None:
    """
    Merge the LoRA adapter and save full merged safetensors -- NOT GGUF, same
    reasoning as the DPO stage's own export_merged(): this model's production
    endpoint is a RunPod serverless worker-vLLM deployment serving AWQ, not an
    Ollama/GGUF setup.
    """
    os.makedirs(SFT_MERGED_OUTPUT_DIR, exist_ok=True)
    model.save_pretrained_merged(SFT_MERGED_OUTPUT_DIR, tokenizer, save_method="merged_16bit")
    print(f"Merged model exported to {SFT_MERGED_OUTPUT_DIR} (merged_16bit safetensors, not GGUF)")


# ── Main ─────────────────────────────────────────────────────────────────────────
def run(dry_run: bool) -> None:
    print(f"Loading up to {SFT_SUBSET_SIZE} example(s) from {KODCODE_DATASET_ID} ({KODCODE_SPLIT} split)...")
    examples = load_sft_examples()
    print(f"Loaded {len(examples)} example(s).")

    if dry_run:
        print(
            f"Dry run: {DRY_RUN_MAX_STEPS} real training steps to validate the "
            f"pipeline mechanically. This is NOT a production fine-tune — the "
            f"resulting adapter will not be saved or exported. Requires real GPU "
            f"access — this will not run on the Aero."
        )

    model, tokenizer = load_model_and_tokenizer()
    dataset = build_dataset(examples, tokenizer)
    trainer = build_trainer(model, tokenizer, dataset, dry_run)

    trainer.train()

    if dry_run:
        print("Dry run complete — pipeline validated, no adapter saved.")
        return

    trainer.save_model(SFT_ADAPTER_OUTPUT_DIR)
    print(f"Adapter saved to {SFT_ADAPTER_OUTPUT_DIR}")
    export_merged(model, tokenizer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a few real training steps to validate the pipeline only — no save/export.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
