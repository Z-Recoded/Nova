# nova_quantize_qwen_coder_awq.py
# AWQ-quantizes the merged Qwen2.5-Coder-32B DPO checkpoint
# (zrecoded/nova-qwen-coder-32b-dpo-merged, full 16-bit safetensors, ~66GB)
# down to a 4-bit AWQ checkpoint for the production RunPod endpoint's
# worker-vLLM deployment (QUANTIZATION=awq), then uploads the result via
# nova_hf_upload.upload_merged_to_hub(). This is the "separate, undesigned
# manual step" both nova_finetune_qwen_coder.py (lines 20-26) and
# nova_finetune_qwen_coder_sft.py (lines 32-33) flag and defer.
#
# HARDWARE: assumes it's already running on a rented A100 80GB pod
# (nova_runpod_pod_launch.py) -- does not provision anything itself, same
# convention as both finetune scripts. The fp16 source checkpoint alone needs
# ~64GB just for weights -- tight on an 80GB card but expected to fit since
# AWQ calibration processes layer-by-layer rather than needing full-model
# activations resident at once. UNVALIDATED: no sourced hard number exists
# for AWQ-quantizing a model this size -- this is reasoned from how the
# algorithm works, not measured. Update this comment with the real wall-clock
# time and peak VRAM after the first real run, same "UNVALIDATED" convention
# as nova_finetune_qwen_coder.py's own hardware comments.
#
# LIBRARY CHOICE -- NOT autoawq: confirmed live 2026-08-01 (against
# casper-hansen/AutoAWQ's own README/PyPI) that AutoAWQ is officially
# deprecated and unmaintained. llm-compressor (the vLLM project's own AWQ
# tooling) is the maintained migration path and is what this script uses.
# Approved as a new dependency by Marvin, 2026-08-01.
#
# CALIBRATION DATA: v1 default only -- llm-compressor's own example
# calibration set (general instruction-style text via the `datasets`
# package), not a Nova-specific calibration set. Deliberately not
# overengineered for v1 -- revisit only if the held-out eval
# (nova_coding_eval.py) shows a real quality regression traceable to
# quantization specifically, not fine-tuning itself.
#
# Usage:
#   python nova_quantize_qwen_coder_awq.py --dry-run   # tiny calibration
#                                                       # subset, mechanical
#                                                       # pipeline check only --
#                                                       # never trust this output
#                                                       # as a real model.
#   python nova_quantize_qwen_coder_awq.py             # real run

import argparse
import os

from nova_hf_upload import upload_merged_to_hub

# ── Constants — AWQ quantization config ─────────────────────────────────────
# Must match nova_finetune_qwen_coder.py's own MERGED_OUTPUT_DIR / HF_HUB_REPO_ID.
SOURCE_MODEL_LOCAL_DIR = "finetune_output/qwen-coder-32b-dpo-merged"
SOURCE_MODEL_HUB_REPO_ID = "zrecoded/nova-qwen-coder-32b-dpo-merged"

QUANTIZED_OUTPUT_DIR = "finetune_output/qwen-coder-32b-awq"

# New repo, distinct from the fp16 merged repo -- the production endpoint's
# MODEL_NAME (nova_remote_inference.py) will eventually point at THIS one,
# not SOURCE_MODEL_HUB_REPO_ID.
AWQ_HUB_REPO_ID = "zrecoded/nova-qwen-coder-32b-awq"

# llm-compressor's own documented example defaults for a general-purpose AWQ
# pass -- v1, not tuned for Nova's specific task distribution.
CALIBRATION_DATASET_NAME = "HuggingFaceH4/ultrachat_200k"
CALIBRATION_DATASET_SPLIT = "train_sft"

# Real gotcha, confirmed live 2026-08-01: the documented 512-sample/2048-token
# defaults hit a real CUDA OOM on an 80GB A100 (`torch.OutOfMemoryError:
# Sequential pipeline ran out of memory`) -- the base fp16 model alone eats
# ~64GB, and AWQ's grid-search/smoothing step caches per-layer activations
# for the WHOLE calibration batch at once (not streamed sample-by-sample), so
# memory scales with num_samples * max_seq_len on top of that fixed cost.
# The 8-sample dry run had no trouble; 512 samples overran by a wide margin.
# 128/1024 is a reasoned smaller step down, not yet proven at this exact
# size -- watch for OOM on the first real run at this setting too and step
# down further (or explore llm-compressor's sequential_targets granularity
# controls) if it recurs.
CALIBRATION_NUM_SAMPLES = 128
CALIBRATION_MAX_SEQ_LEN = 1024
DRY_RUN_CALIBRATION_SAMPLES = 8

AWQ_QUANT_SCHEME = "W4A16_ASYM"  # llm-compressor's standard 4-bit-weight/16-bit-activation AWQ scheme
IGNORE_LAYERS = ["lm_head"]  # never quantize the output head -- llm-compressor's own AWQ example convention


def _resolve_source_model_path() -> str:
    """
    Same fallback pattern as nova_finetune_qwen_coder.py's
    _resolve_base_model_name() -- use a local copy if this pod already has
    one (e.g. quantizing right after training on the same pod), otherwise
    fall back to the DPO stage's HF Hub repo.
    """
    if os.path.isdir(SOURCE_MODEL_LOCAL_DIR):
        print(f"Using local DPO output as quantization source: {SOURCE_MODEL_LOCAL_DIR}")
        return SOURCE_MODEL_LOCAL_DIR
    print(f"No local DPO output found -- downloading from {SOURCE_MODEL_HUB_REPO_ID}")
    return SOURCE_MODEL_HUB_REPO_ID


# ── Model + calibration data loading ────────────────────────────────────────
def load_model_and_tokenizer(source: str):
    """
    Load the merged fp16 checkpoint via plain transformers, not Unsloth --
    this is a one-shot quantization pass, not training, so Unsloth's
    training-time optimizations don't apply here.
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(source)
    model = AutoModelForCausalLM.from_pretrained(source, torch_dtype="auto", device_map="auto")
    return model, tokenizer


def build_calibration_dataset(tokenizer, num_samples: int, max_seq_len: int):
    """
    A general instruction-style calibration sample (llm-compressor's own
    example dataset choice for AWQ), tokenized to max_seq_len. Not
    Nova-specific -- see this file's header comment for why that's an
    accepted v1 tradeoff.
    """
    from datasets import load_dataset

    raw = load_dataset(CALIBRATION_DATASET_NAME, split=f"{CALIBRATION_DATASET_SPLIT}[:{num_samples}]")

    def _format(example):
        text = tokenizer.apply_chat_template(example["messages"], tokenize=False)
        tokenized = tokenizer(text, truncation=True, max_length=max_seq_len, padding=False)
        return tokenized

    return raw.map(_format, remove_columns=raw.column_names)


# ── Quantization ─────────────────────────────────────────────────────────────
def quantize_to_awq(model, tokenizer, calibration_dataset, output_dir: str, max_seq_len: int) -> None:
    """
    Run llm-compressor's AWQModifier + QuantizationModifier one-shot pass (no
    training, no gradient steps) and save the quantized checkpoint plus
    tokenizer files to output_dir.
    """
    from llmcompressor import oneshot
    from llmcompressor.modifiers.awq import AWQModifier
    from llmcompressor.modifiers.quantization import QuantizationModifier

    recipe = [
        AWQModifier(ignore=IGNORE_LAYERS),
        QuantizationModifier(ignore=IGNORE_LAYERS, scheme=AWQ_QUANT_SCHEME, targets=["Linear"]),
    ]

    os.makedirs(output_dir, exist_ok=True)
    oneshot(
        model=model,
        dataset=calibration_dataset,
        recipe=recipe,
        output_dir=output_dir,
        max_seq_length=max_seq_len,
        num_calibration_samples=len(calibration_dataset),
    )
    tokenizer.save_pretrained(output_dir)
    print(f"AWQ-quantized model saved to {output_dir}")


# ── Main ─────────────────────────────────────────────────────────────────────
def run(dry_run: bool) -> None:
    source = _resolve_source_model_path()
    model, tokenizer = load_model_and_tokenizer(source)

    num_samples = DRY_RUN_CALIBRATION_SAMPLES if dry_run else CALIBRATION_NUM_SAMPLES
    calibration_dataset = build_calibration_dataset(tokenizer, num_samples, CALIBRATION_MAX_SEQ_LEN)

    if dry_run:
        print(
            f"Dry run: {num_samples} calibration samples only -- sanity check that "
            f"the pipeline runs end to end. Do NOT upload or trust this output as a "
            f"real quantized model."
        )
        quantize_to_awq(
            model, tokenizer, calibration_dataset, QUANTIZED_OUTPUT_DIR + "-dryrun", CALIBRATION_MAX_SEQ_LEN
        )
        print("Dry run complete -- pipeline validated, nothing uploaded.")
        return

    quantize_to_awq(model, tokenizer, calibration_dataset, QUANTIZED_OUTPUT_DIR, CALIBRATION_MAX_SEQ_LEN)
    upload_merged_to_hub(QUANTIZED_OUTPUT_DIR, AWQ_HUB_REPO_ID)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tiny calibration subset, mechanical pipeline check only -- no upload.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
