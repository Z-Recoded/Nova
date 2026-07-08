# nova_state_system.py
# The one domain adapter that's genuinely buildable today (ClickUp
# 86bara3qe scoped v1) — financial/work/creative/games adapters are
# deferred pending a real data source or a ClickUp API client in Nova's
# own runtime (see CLAUDE.md's "Domain State Layer" section).
#
# Writes system/nova_health and system/pending_alerts to nova_state.db,
# sourced from nova_headroom.py's existing report — no new data source,
# just persisting what /headroom already computes so the alert engine
# (future, 86bara3qu) has something real to read once it exists.

from nova_headroom import get_headroom_report
from nova_state import write_state

DOMAIN = "system"


def refresh_system_state() -> dict:
    """
    Snapshot the current headroom report into nova_state.db's system
    domain. No scheduler exists yet to call this automatically (
    nova_watcher.py itself is deferred, not running) — call it manually,
    or from a future cron/watcher job.
    """
    report = get_headroom_report()
    token_budget = report["token_budget"]

    nova_health = {
        "summary": report["summary"],
        "gpu": report["gpu"],
        "system": report["system"],
        "token_budget_mode": token_budget.get("mode") if token_budget.get("enabled") else None,
    }
    write_state(DOMAIN, "nova_health", nova_health)

    # No alert engine exists yet (86bara3qu) to populate this — an empty
    # list is the honest current state, not a placeholder.
    pending_alerts = {"alerts": []}
    write_state(DOMAIN, "pending_alerts", pending_alerts)

    return {"nova_health": nova_health, "pending_alerts": pending_alerts}


if __name__ == "__main__":
    import json

    print(json.dumps(refresh_system_state(), indent=2))
