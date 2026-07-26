# nova_training_flags_patch.py
# Shared, single-file-scoped patch logic for one training_flags.jsonl entry
# (a blend_flag's correction, or a dpo_verify's verification_status).
#
# Used two ways:
#   1. Locally, by nova_api.py's /label-queue/{kind}/{id}/decide route, when
#      the entry being decided lives on THIS machine's own copy of the file.
#   2. Remotely, by nova_patch_training_flags_cli.py's stdin/stdout wrapper,
#      invoked over the command-restricted Omen->Aero SSH write key (see
#      scripts/ssh_patch_training_flags.ps1) when the entry lives on the
#      OTHER machine.
#
# Kept as one function so local and remote writes can never drift -- the
# same index/timestamp safety check and field validation applies either
# way, and this file is identical on both machines (git-tracked, no
# machine-specific branching inside it at all -- see get_combined_training_status()
# in nova_training_data_status.py for where the actual "which machine"
# decision is made, one layer up).

import json
import os

TRAINING_FLAGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "training_flags.jsonl")


class TrainingFlagsPatchError(Exception):
    """
    Carries an HTTP-style status code so a caller can report the same
    failure the same way regardless of whether the write happened locally
    (nova_api.py re-raises this as an HTTPException) or remotely
    (nova_patch_training_flags_cli.py serializes it into a JSON envelope
    that crosses the SSH bridge as plain text).
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def patch_training_flags_entry(
    kind: str,
    index: int,
    expected_timestamp: str,
    correction: str | None = None,
    verification_status: str | None = None,
) -> dict:
    """
    Patch training_flags.jsonl[index]'s correction (kind="blend_flag") or
    verification_status (kind="dpo_verify") in place -- always against
    THIS machine's own TRAINING_FLAGS_PATH, never itself reaching over SSH.
    The caller decides whether "this machine" is actually the right one to
    be patching (see nova_api.py's decide_label_queue_entry(), which routes
    to either this function directly or a remote dispatch based on which
    machine the entry's synthetic id says it came from).

    Raises TrainingFlagsPatchError(409, ...) if the index/timestamp no
    longer matches -- the file changed underneath since the card was
    loaded, same safety check this logic has always used, just no longer
    duplicated between two inline route branches. Raises
    TrainingFlagsPatchError(422, ...) for an invalid verification_status
    value or an unrecognized kind.
    """
    entries = _read_jsonl(TRAINING_FLAGS_PATH)
    if index >= len(entries) or entries[index].get("timestamp") != expected_timestamp:
        raise TrainingFlagsPatchError(
            409,
            "This entry's position/timestamp no longer matches -- training_flags.jsonl "
            "changed since this card was loaded. Reload the queue and try again.",
        )

    if kind == "blend_flag":
        entries[index]["correction"] = correction or ""
    elif kind == "dpo_verify":
        if verification_status not in ("confirmed_good", "needs_rework"):
            raise TrainingFlagsPatchError(422, "verification_status must be 'confirmed_good' or 'needs_rework'")
        entries[index]["verification_status"] = verification_status
    else:
        raise TrainingFlagsPatchError(422, f"Unknown kind '{kind}' for a training_flags.jsonl patch")

    with open(TRAINING_FLAGS_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return entries[index]
