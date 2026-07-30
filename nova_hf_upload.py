# nova_hf_upload.py
# Shared helper for pushing an already-locally-merged model checkpoint to a
# private Hugging Face Hub repo, so a rented GPU pod (nova_finetune_qwen_coder.py,
# nova_finetune_qwen_coder_sft.py) can be stopped immediately after training
# instead of waiting on a slow pod-to-home transfer for a ~64GB checkpoint
# (2026-07-29 decision).
#
# Deliberately does NOT use Unsloth's push_to_hub_merged() convenience method --
# it has documented real bugs (can silently upload only a README while
# reporting success; can re-download the full-precision base model into a
# second cache directory, doubling disk use). Uploads the directory the
# caller's own export_merged() already wrote and verified locally instead,
# via huggingface_hub directly, then verifies the upload by comparing real
# remote file sizes against local ones -- same "verify the payload, not just
# the status code" discipline as the Omen deployment lesson (CLAUDE.md
# Section 2), not just trusting a 200 OK.
#
# Requires HF_TOKEN (write-scoped) in .env -- fails loudly if missing rather
# than silently skipping the upload, since a skipped upload defeats the whole
# point of being able to stop the pod.

import os


def upload_merged_to_hub(local_dir: str, repo_id: str) -> None:
    """
    Upload local_dir's contents to a private repo_id on the Hugging Face
    Hub, creating the repo if it doesn't exist yet. Raises if the upload
    can't be verified as complete -- callers should not stop a rented pod
    until this returns successfully.
    """
    if not os.environ.get("HF_TOKEN"):
        raise OSError(
            "HF_TOKEN environment variable is not set (needs write scope). "
            "Set it before running a real (non-dry-run) fine-tune -- without it, "
            "the merged checkpoint would only exist on this pod's disk, defeating "
            "the whole point of being able to stop the pod once training finishes."
        )

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, private=True, exist_ok=True)
    print(f"Uploading {local_dir} to https://huggingface.co/{repo_id} ...")
    api.upload_folder(folder_path=local_dir, repo_id=repo_id)

    local_size = sum(
        os.path.getsize(os.path.join(root, filename))
        for root, _, filenames in os.walk(local_dir)
        for filename in filenames
    )
    info = api.repo_info(repo_id, files_metadata=True)
    remote_size = sum(sibling.size or 0 for sibling in info.siblings)

    # Real bug this guards against: Unsloth's push_to_hub_merged() has been
    # reported to silently "succeed" while uploading only a README -- a
    # remote size far smaller than what was uploaded locally is exactly that
    # failure shape, just caught here instead of trusted blindly.
    if remote_size < local_size * 0.95:
        raise RuntimeError(
            f"Upload to {repo_id} looks incomplete -- local checkpoint is "
            f"{local_size / 1e9:.1f}GB but only {remote_size / 1e9:.1f}GB landed "
            f"remotely. Do NOT stop the pod yet -- investigate before trusting this upload."
        )

    print(
        f"Verified upload: {local_dir} -> https://huggingface.co/{repo_id} "
        f"({remote_size / 1e9:.1f}GB remote, matches local — safe to stop the pod now)"
    )
