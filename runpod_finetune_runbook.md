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

## 2. Install dependencies
```bash
pip install -r requirements.txt
```
`unsloth`, `trl`, `peft`, `bitsandbytes`, `huggingface_hub` are already pinned in
`requirements.txt` — nothing extra needed for this pod specifically.

## 3. Set `HF_TOKEN`
Required by `nova_hf_upload.py` (write-scoped — this is what lets the pod push the merged
checkpoint to the private `zrecoded/nova-qwen-coder-*` Hub repos and then be safely stopped).
```bash
export HF_TOKEN=<write-scoped token>
```

## 4. Run the SFT warm-start stage first
```bash
python nova_finetune_qwen_coder_sft.py --dry-run   # mechanical pipeline check, a few real steps, discarded
python nova_finetune_qwen_coder_sft.py             # real run
```

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
