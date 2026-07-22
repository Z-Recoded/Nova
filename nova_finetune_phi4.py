# nova_finetune_phi4.py
# QLoRA DPO fine-tune for Phi-4 Mini, per ClickUp 86bagf51n / the "Nova Reference —
# Fine-Tune Pipeline Re-Scoped for Phi-4 Mini" doc. Reads corrected pairs straight out
# of logs/training_flags.jsonl (same file nova_corrector.py writes to), trains a QLoRA
# adapter with Unsloth, then exports GGUF for a straight Ollama pull-back.
#
# Runs entirely on the Aero's own RTX 5070 (8GB) — the re-scope doc's own VRAM math
# (4-6GB at this config) fits inside that card, so this deliberately does NOT reach for
# a RunPod/Vast.ai rental the way the older Qwen3 8B plan did.
#
# Usage:
#   python nova_finetune_phi4.py --dry-run   # mechanical pipeline validation only —
#                                             # a few real training steps on whatever
#                                             # pairs exist today, output discarded.
#                                             # Never treat this as a production model.
#   python nova_finetune_phi4.py             # real run — refuses below MIN_REAL_PAIRS.

import argparse
import json
import os

from datasets import Dataset

# ── Constants — Phi-4 Mini training config, per the re-scope doc ──────────────
# These are the doc's own numbers, not guesses: LoRA rank 16-32, LR 1e-4 to 2e-4,
# batch 2-4 with gradient accumulation, 8192 training sequence length (the 128K
# inference window doesn't need matching training sequences), gradient checkpointing
# on as a VRAM safety net. Picked the higher-quality end of each range since the
# doc's own 4-6GB VRAM estimate leaves real headroom on this card's 8GB.
BASE_MODEL_NAME = "unsloth/Phi-4-mini-instruct-bnb-4bit"
MAX_SEQ_LENGTH = 8192
LORA_RANK = 32
LORA_ALPHA = 32
LEARNING_RATE = 2e-4
BATCH_SIZE = 2
GRAD_ACCUM_STEPS = 4
NUM_EPOCHS = 3
DPO_BETA = 0.1

# Doc's own stated floor: 100-200 Phi-4-Mini-specific pairs before a real production
# run. Refusing below this isn't a technical limit — it's a guardrail against mistaking
# a mechanical smoke test for a real fine-tune (see the dry-run path below).
MIN_REAL_PAIRS = 100
DRY_RUN_MAX_STEPS = 3

TRAINING_FLAGS_PATH = "logs/training_flags.jsonl"
ADAPTER_OUTPUT_DIR = "finetune_output/phi4-mini-dpo-adapter"
GGUF_OUTPUT_DIR = "finetune_output/phi4-mini-dpo-gguf"
GGUF_QUANT_METHOD = "q4_k_m"  # hardware doc's recommended default on this card


# ── DPO pair loading ───────────────────────────────────────────────────────────
def load_dpo_pairs(path: str) -> list[dict]:
    """
    Read every corrected entry out of training_flags.jsonl and turn it into a
    {prompt, chosen, rejected} triple. "chosen" is nova_corrector.py's Claude-written
    correction; "rejected" is the original blended response nova_logger.py flagged.
    Entries still awaiting correction (correction == "") are skipped, not padded —
    matches nova_corrector.py's own load_entries()/save_entries() read-all convention.
    """
    if not os.path.exists(path):
        return []

    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            correction = entry.get("correction")
            messages = entry.get("messages", [])
            if not correction or not messages:
                continue

            query = messages[0]["content"]
            rejected = messages[-1]["content"]
            pairs.append({"prompt": query, "chosen": correction, "rejected": rejected})

    return pairs


def build_dataset(pairs: list[dict], tokenizer) -> Dataset:
    """
    Apply Phi-4 Mini's real chat template (<|user|>...<|end|><|assistant|>) to each
    prompt so the trainer sees exactly the same formatting Ollama will use at
    inference time. chosen/rejected stay as plain completion text — TRL's DPOTrainer
    appends them directly after the formatted prompt.
    """
    formatted = []
    for pair in pairs:
        prompt_text = tokenizer.apply_chat_template(
            [{"role": "user", "content": pair["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        formatted.append(
            {"prompt": prompt_text, "chosen": pair["chosen"], "rejected": pair["rejected"]}
        )
    return Dataset.from_list(formatted)


# ── Model + trainer setup ──────────────────────────────────────────────────────
def load_model_and_tokenizer():
    """
    Load the pre-quantized 4-bit Phi-4 Mini checkpoint via Unsloth and wrap it with
    a LoRA adapter. Kept as its own function (not inlined into main) so a future
    swap to a different base model only touches this one call, per the eval-wrapper
    precedent in nova_benchmark.py.
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
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
    )
    return model, tokenizer


def build_trainer(model, tokenizer, dataset: Dataset, dry_run: bool):
    """
    Wire up TRL's DPOTrainer with the re-scope doc's training config. dry_run caps
    training at a handful of real steps (DRY_RUN_MAX_STEPS) purely to prove the
    mechanical pipeline (data -> tokenize -> forward -> backward -> optimizer step)
    runs cleanly on this hardware — it is not a quality signal and the resulting
    adapter is never saved.
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


# ── GGUF export ─────────────────────────────────────────────────────────────────
def export_gguf(model, tokenizer) -> None:
    """
    Merge the LoRA adapter and export GGUF at the hardware doc's recommended
    Q4_K_M quantization — a drop-in replacement for Ollama, no other pipeline
    changes needed on the inference side.
    """
    os.makedirs(GGUF_OUTPUT_DIR, exist_ok=True)
    model.save_pretrained_gguf(GGUF_OUTPUT_DIR, tokenizer, quantization_method=GGUF_QUANT_METHOD)
    print(f"GGUF exported to {GGUF_OUTPUT_DIR} ({GGUF_QUANT_METHOD})")


# ── Main ─────────────────────────────────────────────────────────────────────────
def run(dry_run: bool) -> None:
    pairs = load_dpo_pairs(TRAINING_FLAGS_PATH)
    print(f"Loaded {len(pairs)} corrected DPO pair(s) from {TRAINING_FLAGS_PATH}.")

    if not pairs:
        print("No corrected pairs found — nothing to train on.")
        return

    if not dry_run and len(pairs) < MIN_REAL_PAIRS:
        print(
            f"Only {len(pairs)} corrected pairs exist — the re-scope doc's own floor "
            f"for a production run is {MIN_REAL_PAIRS}. Refusing to run a real "
            f"fine-tune on this little data. Use --dry-run to validate the pipeline "
            f"mechanically instead."
        )
        return

    if dry_run:
        print(
            f"Dry run: {DRY_RUN_MAX_STEPS} real training steps to validate the "
            f"pipeline mechanically. This is NOT a production fine-tune — the "
            f"resulting adapter will not be saved or exported."
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
    export_gguf(model, tokenizer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a few real training steps to validate the pipeline only — no save/export.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
