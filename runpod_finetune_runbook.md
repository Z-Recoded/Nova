# RunPod A100 Fine-Tune Runbook (`86baf4e70` Pattern 1)

> Manual steps once a pod is `RUNNING` via `nova_runpod_pod_launch.py`. Same "manual once
> provisioned" spirit as `omen_setup_runbook.md`, scoped to one ephemeral training run rather
> than a permanent server.

## 0. Launch the pod (from the Aero)
```
nova-env\Scripts\python nova_runpod_pod_launch.py launch --name qwen-coder-run
```
Prints an SSH command once the pod reaches `RUNNING`. If it times out, check
`nova_runpod_pod_launch.py status <pod_id>` or the RunPod web console before assuming failure —
pods can take a few minutes to come up.

## 1. Clone the repo (on the pod)
Reuses the Omen's existing read-only GitHub deploy key — same posture this ephemeral pod needs
(it only ever pulls, never pushes). Copy that key's **private** half onto the pod (e.g. paste
into `~/.ssh/id_ed25519` via the RunPod web terminal, or `scp` it from wherever it's currently
stored — it is not stored anywhere in this repo or in `.env`), then:

```bash
chmod 600 ~/.ssh/id_ed25519
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no" \
  git clone git@github.com:<org>/<repo>.git /workspace/nova
cd /workspace/nova
```

**Two gotchas already hit doing this on the Omen** (`reference_omen_git_gotchas` memory /
CLAUDE.md "Working Directly on the Omen via SSH"), likely to repeat here since it's the same
non-interactive-SSH, no-`.bashrc`, no-git-identity situation:
- Plain `ssh host "command"` doesn't source `.bashrc` — if any installed tool isn't found, check
  `PATH` explicitly rather than assuming it's missing.
- No git identity is configured by default — if you commit anything here (you shouldn't need
  to; this pod only reads), scope it with `git -c user.name=... -c user.email=... commit ...`.

**Real gotcha, confirmed live 2026-08-01: `logs/coding_review_log.jsonl` is gitignored**, so it
never makes it into the clone. Both scripts silently proceed with zero Nova examples if it's
missing (`load_nova_review_examples()`/`load_dpo_pairs()` just return an empty list — no error,
no warning) — the DPO stage will then hit `MIN_REAL_PAIRS` and refuse outright. Copy it over
from the Aero before running either stage:
```bash
# from the Aero
cat logs/coding_review_log.jsonl | ssh -p <pod_port> root@<pod_ip> "mkdir -p /workspace/nova/logs && cat > /workspace/nova/logs/coding_review_log.jsonl"
```

## 2. Install dependencies
`requirements.txt` is shared with the Aero (Windows) and has three Aero-specific pins that
don't exist on Linux — confirmed live 2026-08-01, filter them out at install time rather than
editing the tracked file:
```bash
grep -v -iE '^(pywin32|torch|torchao|torchaudio|torchvision|triton-windows)==' requirements.txt > /tmp/requirements-linux.txt
pip install -q -r /tmp/requirements-linux.txt --root-user-action=ignore
```
Excluding `torch`/`torchaudio`/`torchvision` is deliberate, not just a workaround for the
`+cu128` local-build-tag install failure: the base image already ships a working CUDA-matched
torch, and letting `unsloth`/`trl`'s own transitive requirements resolve a torch version (rather
than forcing the Aero's exact pin) is what actually worked live — it landed on `2.10.0+cu128`
with CUDA confirmed working on its own. `unsloth`, `trl`, `peft`, `bitsandbytes`,
`huggingface_hub` are otherwise already pinned in `requirements.txt` — nothing extra needed.

## 3. Set `HF_TOKEN` and redirect the HF cache to the volume
`HF_TOKEN` is required by `nova_hf_upload.py` (write-scoped — this is what lets the pod push the
merged checkpoint to the private `zrecoded/nova-qwen-coder-*` Hub repos and then be safely
stopped).

**Real gotcha, confirmed live 2026-08-01:** Hugging Face's cache defaults to
`~/.cache/huggingface`, which lives on the small ephemeral **container disk**, not the large
persistent **volume** mounted at `/workspace` — a full base-model download there can exhaust the
container disk before training even starts. Redirect it explicitly:
```bash
export HF_TOKEN=<write-scoped token>
export HF_HOME=/workspace/hf_cache
```

## 4. Run the SFT warm-start stage first
A real run over the full `SFT_SUBSET_SIZE` is long — measured ~10.2s/step from the DPO stage's
real per-step timing puts a full epoch at ~2,500 steps, roughly **~7 hours**. Checkpoints save
every `SAVE_STEPS` steps (not just at the end) specifically so an interruption doesn't lose the
whole run — if `finetune_output/qwen-coder-32b-sft-adapter` already has a `checkpoint-N`
directory, re-running the same command resumes from it automatically instead of restarting.
```bash
python nova_finetune_qwen_coder_sft.py --dry-run   # mechanical pipeline check, a few real steps, discarded
python nova_finetune_qwen_coder_sft.py             # real run (resumes automatically if interrupted and re-run)
```
(`HF_HOME` from step 3 carries through automatically since it's exported in the shell, not
passed per-command.)

## 5. Run the DPO refinement stage
`nova_finetune_qwen_coder.py`'s `_resolve_base_model_name()` automatically picks up the SFT
stage's output (local dir first, then its Hub repo) — no manual wiring needed between the two
stages.
```bash
python nova_finetune_qwen_coder.py --dry-run
python nova_finetune_qwen_coder.py
```

## 6. Confirm the upload before stopping the pod
Both scripts' `upload_merged_to_hub()` call refuses to report success on a partial upload and
prints an explicit "safe to stop the pod now" line once the remote size is verified against the
local checkpoint. Wait for that line — don't stop the pod on a hunch that training finished.

**Real gotcha, confirmed live 2026-08-01: the merge step needs the base checkpoint AND the
merged output on disk at the same time** (~62GB + ~65GB for this model) — `nova_runpod_pod_launch.py`'s
default volume size was bumped to 200GB after hitting a real `Disk quota exceeded` mid-merge at
100GB. If you launched with an older/smaller volume, redirect the merged output to the
(separate-quota) container disk instead of re-launching:
```bash
mkdir -p /root/finetune_output && ln -s /root/finetune_output /workspace/nova/finetune_output
```

**Real gotcha, confirmed live 2026-08-01: Hugging Face private-repo storage has a real plan
limit.** A second ~65GB private checkpoint (SFT and DPO stages each produce one) can hit `403
Forbidden: Private repository storage limit reached` even though the upload itself works fine.
Options if this happens: delete the now-superseded SFT checkpoint from the Hub (DPO only needs
it as a warm-start source, not permanently), upgrade the HF plan, or make one repo public.

**Do not try to download a merged checkpoint to the Omen or Aero as a workaround for HF
storage/upload issues.** Confirmed live 2026-08-01: home internet throughput for a file this
size measured ~5MB/s regardless of routing (relayed through the Aero, or pulled directly by the
Omen) — a 64GB transfer would take ~3.4 hours, with the pod billing the entire time, versus
Hugging Face's own infrastructure measuring ~670MB/s for the same upload. Fix the Hub-side
blocker instead of routing around it.

## 7. Stop paying
```
nova-env\Scripts\python nova_runpod_pod_launch.py terminate <pod_id>
```
From the Aero, not the pod itself. `terminate` (not `stop`) is the real "stop billing" action —
`stop` alone keeps the volume disk (and its cost) alive.

## Not covered here
Re-quantizing the merged safetensors to AWQ and redeploying onto
`nova_remote_inference.RUNPOD_ENDPOINT_ID` is still a separate, undesigned manual step — see
both finetune scripts' own header comments.
