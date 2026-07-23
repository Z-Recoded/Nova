# nova_agentic_dataset_curator.py
# Curates the external-dataset half of Nova's agentic-reasoning training data
# (ClickUp 86bara7zk) for a future Qwen3 8B QLoRA fine-tune. Downloads,
# normalizes, and blends five permissively-licensed public datasets into one
# curated .jsonl of {"messages": [...]} chat-format instruction/response
# pairs -- the same shape ollama_client.chat() and nova_corrector.py's DPO
# entries already use everywhere else in this codebase.
#
# Deliberately does NOT include the "Nova augmentation" slice (10-20 real
# task-decomposition examples from Claude Code sessions) -- that piece needs
# the still-blocked MCP tool-calling integration (86baf72n5), confirmed with
# Marvin as separate, deferred scope. This script produces the curated
# .jsonl only; the actual Unsloth QLoRA fine-tune run is a later step.
#
# Real dataset identifiers were verified live against the Hugging Face Hub
# before writing this script (not assumed from the original ClickUp ticket,
# which had real inaccuracies -- see the plan/commit message for details):
# AgentInstruct is 1,866 real examples with an unconfirmed license (accepted
# knowingly), ToolBench's official HF page 401s so a verified third-party
# mirror is used instead, and TAT-QA is CC-BY-4.0 rather than the ticket's
# claimed MIT/Apache (accepted knowingly for personal, non-redistributed use).
#
# Usage:
#   nova-env\\Scripts\\python nova_agentic_dataset_curator.py --download
#   nova-env\\Scripts\\python nova_agentic_dataset_curator.py --convert
#   nova-env\\Scripts\\python nova_agentic_dataset_curator.py --blend
#   nova-env\\Scripts\\python nova_agentic_dataset_curator.py --report
#   nova-env\\Scripts\\python nova_agentic_dataset_curator.py --all

import argparse
import io
import json
import os
import random
import zipfile

import pyarrow.parquet as pq
import requests
from huggingface_hub import hf_hub_download

# ── Config ─────────────────────────────────────────────────────
RAW_DIR = os.path.join("data", "agentic_training", "raw")
CURATED_DIR = os.path.join("data", "agentic_training", "curated")
CURATED_OUTPUT_PATH = os.path.join(CURATED_DIR, "agentic_reasoning_pairs.jsonl")

# Real, HF-Hub-confirmed repo IDs (see module docstring).
AGENTINSTRUCT_REPO = "THUDM/AgentInstruct"
TOOLBENCH_REPO = "tuandunghcmut/toolbench-v1"
APIBENCH_REPO = "gorilla-llm/APIBench"
TATQA_REPO = "next-tat/TAT-QA"

# dreamerdeo/finqa on HF Hub is a legacy loader script, not hosted data files
# (confirmed live: list_repo_files() returns only finqa.py) -- the script
# itself downloads this exact GitHub archive and reads dataset/{train,dev,test}.json
# from it, so we do the same thing directly rather than depending on the
# `datasets` library just to run that one script.
FINQA_GITHUB_ZIP_URL = "https://github.com/czyssrs/FinQA/archive/refs/heads/main.zip"
FINQA_ZIP_ROOT = "FinQA-main"  # top-level dir inside the extracted zip

# Per-source metadata carried into every curated row, for auditability.
LICENSES = {
    "agentinstruct": "unconfirmed",  # no LICENSE file/HF tag found -- accepted risk
    "toolbench": "Apache-2.0",
    "apibench": "Apache-2.0",
    "finqa": "MIT",  # stated in the source GitHub repo, not the HF card
    "tatqa": "CC-BY-4.0",
}

# Target blend (Phase 3) -- named constants, easy to retune.
TARGET_TOTAL_ROWS = 10_000
TARGET_AGENTIC_ROWS = 6_000  # AgentInstruct + APIBench + ToolBench
TARGET_FINANCIAL_ROWS = 4_000  # FinQA + TAT-QA
RANDOM_SEED = 42

HTTP_TIMEOUT_SECONDS = 120


# ── Phase 1: download ─────────────────────────────────────────


def _dataset_raw_dir(name: str) -> str:
    path = os.path.join(RAW_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


def download_agentinstruct() -> None:
    """Pull all 6 per-task parquet splits (AlfWorld/DB/KG/Mind2Web/OS/WebShop)."""
    dest = _dataset_raw_dir("agentinstruct")
    files = [
        "data/alfworld-00000-of-00001-302ad687bb3817a4.parquet",
        "data/db-00000-of-00001-916a87c4725da8c0.parquet",
        "data/kg-00000-of-00001-9e159f6d0557d229.parquet",
        "data/mind2web-00000-of-00001-fc25d47330eea0fc.parquet",
        "data/os-00000-of-00001-971539c34fcc7500.parquet",
        "data/webshop-00000-of-00001-9f2ae60445e11b4e.parquet",
    ]
    for f in files:
        local_path = hf_hub_download(AGENTINSTRUCT_REPO, f, repo_type="dataset")
        print(f"[agentinstruct] {f} -> {local_path}")
    with open(os.path.join(dest, ".files"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))


def download_toolbench() -> None:
    """Pull the training split's parquet shards (skip benchmark/ -- eval-only)."""
    dest = _dataset_raw_dir("toolbench")
    files = [
        "data/train-00000-of-00004.parquet",
        "data/train-00001-of-00004.parquet",
        "data/train-00002-of-00004.parquet",
        "data/train-00003-of-00004.parquet",
    ]
    for f in files:
        local_path = hf_hub_download(TOOLBENCH_REPO, f, repo_type="dataset")
        print(f"[toolbench] {f} -> {local_path}")
    with open(os.path.join(dest, ".files"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))


def download_apibench() -> None:
    """Pull the three per-source training files (HF/TensorFlow/TorchHub)."""
    dest = _dataset_raw_dir("apibench")
    files = ["huggingface_train.json", "tensorflow_train.json", "torchhub_train.json"]
    for f in files:
        local_path = hf_hub_download(APIBENCH_REPO, f, repo_type="dataset")
        print(f"[apibench] {f} -> {local_path}")
    with open(os.path.join(dest, ".files"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))


def download_finqa() -> None:
    """
    Download the FinQA source zip directly from GitHub (the same archive
    dreamerdeo/finqa's own HF loader script pulls from) and extract
    dataset/{train,dev,test}.json. Bypasses the `datasets` library entirely.
    """
    dest = _dataset_raw_dir("finqa")
    print(f"[finqa] downloading {FINQA_GITHUB_ZIP_URL}")
    response = requests.get(FINQA_GITHUB_ZIP_URL, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        for split in ("train", "dev", "test"):
            member = f"{FINQA_ZIP_ROOT}/dataset/{split}.json"
            target_path = os.path.join(dest, f"{split}.json")
            with zf.open(member) as src, open(target_path, "wb") as out:
                out.write(src.read())
            print(f"[finqa] extracted {member} -> {target_path}")


def download_tatqa() -> None:
    """Pull the train split (dev/test held out -- only train is needed for SFT data)."""
    dest = _dataset_raw_dir("tatqa")
    files = ["tatqa_dataset_train.json"]
    for f in files:
        local_path = hf_hub_download(TATQA_REPO, f, repo_type="dataset")
        print(f"[tatqa] {f} -> {local_path}")
    with open(os.path.join(dest, ".files"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))


def download_all() -> None:
    """Run every downloader, isolating failures so one bad dataset doesn't block the rest."""
    downloaders = [
        ("agentinstruct", download_agentinstruct),
        ("toolbench", download_toolbench),
        ("apibench", download_apibench),
        ("finqa", download_finqa),
        ("tatqa", download_tatqa),
    ]
    for name, fn in downloaders:
        print(f"\n=== Downloading {name} ===")
        try:
            fn()
        except Exception as e:
            print(f"[{name}] DOWNLOAD FAILED: {e}")


# ── Phase 2: convert ───────────────────────────────────────────
# Real schemas confirmed by inspecting one real downloaded row/file per
# dataset before writing any of this (not assumed from research summaries):
#   agentinstruct : row = {id, conversations: [{from, value, loss}, ...]}
#                    (nested list of dicts) -- from in {"human", "gpt"}
#   toolbench     : row = {id, conversations: {from: [...], value: [...]}}
#                    (PARALLEL ARRAYS, not a list of dicts -- a real surprise
#                    caught here, not assumed) -- from in {"system", "user",
#                    "assistant", "function"}
#   apibench      : one JSON object per line, {code, api_call, provider,
#                    api_data} -- "code" is a pre-formatted
#                    "###Instruction: ...\n###Output: ..." string, split on
#                    the "###Output:" marker
#   finqa         : flat list, {pre_text, post_text, table, qa: {question,
#                    answer, explanation, gold_inds}, ...} -- question/answer
#                    live under "qa", not top-level, confirmed against the
#                    dataset's own HF loader script before assuming
#   tatqa         : flat list of contexts, {table: {table: [[...]]}},
#                    paragraphs: [{text,...}], questions: [{question, answer,
#                    derivation,...}]} -- one row per context, needs
#                    flattening to one row per question


def _messages_row(messages: list[dict], source: str) -> dict:
    """Build one curated row in Nova's standard chat-message shape."""
    return {"messages": messages, "source_dataset": source, "license": LICENSES[source]}


def _format_table(rows: list) -> str:
    """Render a 2D table (list of row-lists) as simple pipe-separated text."""
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)


def _convert_agentinstruct():
    files = [
        "data/alfworld-00000-of-00001-302ad687bb3817a4.parquet",
        "data/db-00000-of-00001-916a87c4725da8c0.parquet",
        "data/kg-00000-of-00001-9e159f6d0557d229.parquet",
        "data/mind2web-00000-of-00001-fc25d47330eea0fc.parquet",
        "data/os-00000-of-00001-971539c34fcc7500.parquet",
        "data/webshop-00000-of-00001-9f2ae60445e11b4e.parquet",
    ]
    role_map = {"human": "user", "gpt": "assistant"}
    for f in files:
        local_path = hf_hub_download(AGENTINSTRUCT_REPO, f, repo_type="dataset")
        for row in pq.read_table(local_path).to_pylist():
            messages = [
                {"role": role_map.get(turn["from"], turn["from"]), "content": turn["value"]}
                for turn in row["conversations"]
            ]
            if messages:
                yield _messages_row(messages, "agentinstruct")


def _convert_toolbench():
    files = [
        "data/train-00000-of-00004.parquet",
        "data/train-00001-of-00004.parquet",
        "data/train-00002-of-00004.parquet",
        "data/train-00003-of-00004.parquet",
    ]
    role_map = {"function": "tool"}
    for f in files:
        local_path = hf_hub_download(TOOLBENCH_REPO, f, repo_type="dataset")
        for row in pq.read_table(local_path).to_pylist():
            convo = row["conversations"]
            # Parallel arrays, not a list of dicts -- confirmed live, see module notes above.
            froms, values = convo["from"], convo["value"]
            messages = [
                {"role": role_map.get(role, role), "content": value} for role, value in zip(froms, values, strict=True)
            ]
            if messages:
                yield _messages_row(messages, "toolbench")


def _convert_apibench():
    files = ["huggingface_train.json", "tensorflow_train.json", "torchhub_train.json"]
    for f in files:
        local_path = hf_hub_download(APIBENCH_REPO, f, repo_type="dataset")
        with open(local_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                code = row.get("code", "")
                marker = "###Output:"
                idx = code.find(marker)
                if idx == -1:
                    continue
                instruction = code[:idx].replace("###Instruction:", "").strip()
                output = code[idx + len(marker) :].strip()
                if not instruction or not output:
                    continue
                messages = [
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": output},
                ]
                yield _messages_row(messages, "apibench")


def _convert_finqa():
    # Train split only (6,251 rows) -- plenty for this blend's target size,
    # dev/test held out rather than pulled in unnecessarily.
    path = os.path.join(RAW_DIR, "finqa", "train.json")
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        qa = row.get("qa", {})
        question = qa.get("question")
        answer = qa.get("answer")
        if not question or answer is None:
            continue
        context = (
            "\n".join(row.get("pre_text", []))
            + "\n\n"
            + _format_table(row.get("table", []))
            + "\n\n"
            + "\n".join(row.get("post_text", []))
        )
        user_content = f"{context.strip()}\n\nQuestion: {question}"
        # Prefer the free-text explanation as the reasoning chain; fall back
        # to the gold evidence sentences flagged as most relevant.
        explanation = qa.get("explanation", "").strip()
        gold_inds = qa.get("gold_inds", {})
        reasoning = explanation or " ".join(gold_inds.values()) if gold_inds else explanation
        assistant_content = f"{reasoning}\n\nAnswer: {answer}" if reasoning else f"Answer: {answer}"
        messages = [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
        yield _messages_row(messages, "finqa")


def _convert_tatqa():
    local_path = hf_hub_download(TATQA_REPO, "tatqa_dataset_train.json", repo_type="dataset")
    with open(local_path, encoding="utf-8") as fh:
        contexts = json.load(fh)
    for context in contexts:
        table_text = _format_table(context.get("table", {}).get("table", []))
        paragraph_text = "\n".join(p.get("text", "") for p in context.get("paragraphs", []))
        base_context = f"{table_text}\n\n{paragraph_text}".strip()
        for q in context.get("questions", []):
            question = q.get("question")
            answer = q.get("answer")
            if not question or answer is None:
                continue
            answer_str = ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)
            derivation = (q.get("derivation") or "").strip()
            assistant_content = f"{derivation}\n\nAnswer: {answer_str}" if derivation else f"Answer: {answer_str}"
            messages = [
                {"role": "user", "content": f"{base_context}\n\nQuestion: {question}"},
                {"role": "assistant", "content": assistant_content},
            ]
            yield _messages_row(messages, "tatqa")


CONVERTERS = {
    "agentinstruct": _convert_agentinstruct,
    "toolbench": _convert_toolbench,
    "apibench": _convert_apibench,
    "finqa": _convert_finqa,
    "tatqa": _convert_tatqa,
}


def convert_all() -> None:
    """
    Run every converter, writing each dataset's normalized rows to its own
    intermediate file so --blend can run independently without re-parsing
    every source dataset from scratch.
    """
    for name, converter in CONVERTERS.items():
        out_path = os.path.join(_dataset_raw_dir(name), "converted.jsonl")
        count = 0
        print(f"\n=== Converting {name} ===")
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                for row in converter():
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
                    if count == 1:
                        print(f"[{name}] first converted row: {json.dumps(row, ensure_ascii=False)[:400]}")
            print(f"[{name}] wrote {count} rows -> {out_path}")
        except Exception as e:
            print(f"[{name}] CONVERT FAILED: {e}")


# ── Phase 3: blend ─────────────────────────────────────────────
# Per-dataset sample sizes for the target 60/40 agentic/financial split.
# AgentInstruct's full 1,866 is taken as-is (too small to downsample
# further and still be meaningful); ToolBench gets the largest share of
# the remaining agentic budget since it's the largest and most clearly
# on-topic "tool-calling" source. FinQA gets a larger share than TAT-QA
# on the financial side since its license (MIT) is cleaner than TAT-QA's
# (CC-BY-4.0) -- not a hard rule, just a mild preference where there's
# more than enough of both to hit the target either way.
AGENTIC_SAMPLE_SIZES = {
    "agentinstruct": 1_866,  # all of it
    "apibench": 1_500,
    "toolbench": 2_634,  # fills the remainder of TARGET_AGENTIC_ROWS (6,000)
}
FINANCIAL_SAMPLE_SIZES = {
    "finqa": 2_400,
    "tatqa": 1_600,  # fills the remainder of TARGET_FINANCIAL_ROWS (4,000)
}


def _load_converted(name: str) -> list[dict]:
    path = os.path.join(RAW_DIR, name, "converted.jsonl")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def blend() -> None:
    """
    Sample each converted dataset down to its target size (Phase 3's named
    constants above) and write one shuffled, blended .jsonl. Uses a fixed
    seed so reruns are reproducible.
    """
    # Deterministic dataset shuffling/sampling, not security-sensitive.
    rng = random.Random(RANDOM_SEED)  # nosec B311
    blended: list[dict] = []

    for name, target_size in {**AGENTIC_SAMPLE_SIZES, **FINANCIAL_SAMPLE_SIZES}.items():
        rows = _load_converted(name)
        sample = rows if len(rows) <= target_size else rng.sample(rows, target_size)
        print(f"[blend] {name}: sampled {len(sample)} of {len(rows)} available rows (target {target_size})")
        blended.extend(sample)

    rng.shuffle(blended)

    os.makedirs(CURATED_DIR, exist_ok=True)
    with open(CURATED_OUTPUT_PATH, "w", encoding="utf-8") as fh:
        for row in blended:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n[blend] wrote {len(blended)} total rows -> {CURATED_OUTPUT_PATH}")


# ── Phase 4: report ────────────────────────────────────────────


def report() -> None:
    """Print final counts per source/license and a few random sample rows for a manual sanity check."""
    if not os.path.exists(CURATED_OUTPUT_PATH):
        print(f"No curated file found at {CURATED_OUTPUT_PATH} -- run --blend first.")
        return

    with open(CURATED_OUTPUT_PATH, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]

    print(f"Total curated rows: {len(rows)}\n")

    by_source: dict[str, int] = {}
    by_license: dict[str, int] = {}
    for row in rows:
        by_source[row["source_dataset"]] = by_source.get(row["source_dataset"], 0) + 1
        by_license[row["license"]] = by_license.get(row["license"], 0) + 1

    print("By source dataset:")
    for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {source:15s} {count:6d}  ({100 * count / len(rows):.1f}%)")

    print("\nBy license:")
    for license_name, count in sorted(by_license.items(), key=lambda kv: -kv[1]):
        print(f"  {license_name:15s} {count:6d}  ({100 * count / len(rows):.1f}%)")

    print("\nSample rows:")
    # Deterministic dataset shuffling/sampling, not security-sensitive.
    rng = random.Random(RANDOM_SEED)  # nosec B311
    for row in rng.sample(rows, min(3, len(rows))):
        print(f"\n--- source: {row['source_dataset']} ---")
        for msg in row["messages"]:
            content_preview = msg["content"][:200].replace("\n", " ")
            print(f"  [{msg['role']}] {content_preview}{'...' if len(msg['content']) > 200 else ''}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curate agentic-reasoning training data (86bara7zk)")
    parser.add_argument("--download", action="store_true", help="Phase 1: download raw datasets")
    parser.add_argument("--convert", action="store_true", help="Phase 2: normalize to messages format")
    parser.add_argument("--blend", action="store_true", help="Phase 3: sample + blend to target ratio")
    parser.add_argument("--report", action="store_true", help="Phase 4: print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run all four phases in sequence")
    args = parser.parse_args()

    if args.all or args.download:
        download_all()
    if args.all or args.convert:
        convert_all()
    if args.all or args.blend:
        blend()
    if args.all or args.report:
        report()
