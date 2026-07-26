# nova_patch_training_flags_cli.py
# stdin/stdout JSON wrapper around nova_training_flags_patch.patch_training_flags_entry(),
# for the write side of the Omen<->Aero training-data bridge.
#
# On the Omen->Aero leg, this is invoked by scripts/ssh_patch_training_flags.ps1
# -- the forced command for the one WRITE-capable key in the bridge. On the
# Aero->Omen leg (already-privileged direction, no new key needed -- the
# Aero already has full, non-restricted SSH access to the Omen), it's
# invoked directly the same way by nova_training_data_status.dispatch_remote_patch().
#
# Deliberately minimal and narrow even though it writes: reads exactly one
# JSON request object from stdin and never accepts a file path -- the only
# file this can ever touch is nova_training_flags_patch.TRAINING_FLAGS_PATH,
# resolved relative to this script's own location. Writes exactly one JSON
# response object to stdout: {"ok": true, "entry": {...}} on success, or
# {"ok": false, "status": 409|422, "detail": "..."} on a real validation
# failure -- never raises past this point, so a malformed request or a
# stale index/timestamp comes back as data, not a crashed process, letting
# the caller reconstruct the same HTTP status a local decide would have
# returned.

import json
import sys

from nova_training_flags_patch import TrainingFlagsPatchError, patch_training_flags_entry


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8")
    try:
        req = json.loads(raw)
        result = patch_training_flags_entry(
            kind=req["kind"],
            index=req["index"],
            expected_timestamp=req["expected_timestamp"],
            correction=req.get("correction"),
            verification_status=req.get("verification_status"),
        )
        response = {"ok": True, "entry": result}
    except TrainingFlagsPatchError as e:
        response = {"ok": False, "status": e.status_code, "detail": e.detail}
    except (KeyError, json.JSONDecodeError) as e:
        response = {"ok": False, "status": 422, "detail": f"Malformed patch request: {e}"}

    sys.stdout.write(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
