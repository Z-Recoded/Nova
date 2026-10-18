# scripts/verify_trade_proposals.py
# Real-behavior check for nova_trade_proposals.py, in the same spirit as
# scripts/verify_tutor_phase1.py: runs the actual router against a throwaway
# SQLite file (never nova_state.db) with a pinned clock and a recorded fake
# push. Exits non-zero on the first failed check.
#
#   nova-env\Scripts\python scripts\verify_trade_proposals.py

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import nova_state
import nova_trade_proposals as ntp

TRADE_TOKEN = "test-trade-token"  # nosec B105 - throwaway test value
CONTROLLER_TOKEN = "test-controller-token"  # nosec B105 - throwaway test value
TRADE_HEADERS = {"X-Nova-Trade-Token": TRADE_TOKEN}
CONTROLLER_HEADERS = {"X-Nova-Escalation-Token": CONTROLLER_TOKEN}

START_TIME = datetime(2026, 10, 18, 12, 0, 0)
SHORT_TTL_SECONDS = 60
SECONDS_PAST_EXPIRY = 5

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    """Record and print one pass/fail line."""
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures.append(label)


def make_payload(proposal_id: str, **overrides) -> dict:
    """A valid register body; overrides replace individual fields."""
    payload = {
        "proposal_id": proposal_id,
        "ticker": "KXHIGHNY-26OCT19-T60",
        "title": "NYC high temp above 60F on Oct 19",
        "side": "yes",
        "price_cents": 42,
        "size_contracts": 10,
        "probability_estimate": 0.55,
        "ev_after_fees_usd": 1.05,
        "confidence_score": 0.7,
        "evidence": ["NWS forecast 63F", "model ensemble mean 62F"],
        "ttl_seconds": SHORT_TTL_SECONDS,
    }
    payload.update(overrides)
    return payload


def main() -> None:
    temp_dir = tempfile.mkdtemp()
    nova_state.DB_PATH = str(Path(temp_dir) / "verify_state.db")
    os.environ[ntp.TRADE_TOKEN_ENV_VAR] = TRADE_TOKEN
    os.environ[ntp.ESCALATION_TOKEN_ENV_VAR] = CONTROLLER_TOKEN

    clock = {"now": START_TIME}
    pushes: list[dict] = []
    flag = {"enabled": True}
    ntp._now = lambda: clock["now"]
    ntp.is_trade_proposals_enabled = lambda: flag["enabled"]
    ntp.send_notification = lambda **kwargs: pushes.append(kwargs) or True

    app = FastAPI()
    app.include_router(ntp.router)
    client = TestClient(app)

    # ── Auth ──
    body = make_payload("auth-check-0001")
    check("register without token -> 401", client.post("/trade-proposals", json=body).status_code == 401)
    check(
        "register with controller token -> 401",
        client.post("/trade-proposals", json=body, headers=CONTROLLER_HEADERS).status_code == 401,
    )
    check(
        "register with wrong token -> 401",
        client.post("/trade-proposals", json=body, headers={"X-Nova-Trade-Token": "nope"}).status_code == 401,
    )
    check(
        "list with trade token -> 401 (kta cannot list)",
        client.get("/trade-proposals", headers=TRADE_HEADERS).status_code == 401,
    )
    check("list without token -> 401", client.get("/trade-proposals").status_code == 401)

    # ── Register ──
    response = client.post("/trade-proposals", json=body, headers=TRADE_HEADERS)
    record = response.json()
    check("register with trade token -> 200", response.status_code == 200)
    check("new record is pending", record.get("status") == "pending")
    check("notional computed server-side (42c x 10 = $4.20)", record.get("notional_usd") == 4.2)
    check(
        "expires_at = created_at + ttl",
        datetime.fromisoformat(record["expires_at"]) == START_TIME + timedelta(seconds=SHORT_TTL_SECONDS),
    )
    check("one push sent on create", len(pushes) == 1)
    check(
        "push carries no title/evidence/EV",
        "NYC" not in pushes[0]["message"] and "NWS" not in pushes[0]["message"] and "1.05" not in pushes[0]["message"],
    )
    check("short ttl (<=120s) gets high push priority", pushes[0].get("priority") == "high")

    # ── Idempotency ──
    again = client.post("/trade-proposals", json=body, headers=TRADE_HEADERS)
    check(
        "identical re-register -> 200 same record",
        again.status_code == 200 and again.json()["created_at"] == record["created_at"],
    )
    check("identical re-register sends no second push", len(pushes) == 1)
    clash = client.post("/trade-proposals", json=make_payload("auth-check-0001", price_cents=43), headers=TRADE_HEADERS)
    check("re-register with different price -> 409", clash.status_code == 409)

    # ── Validation ──
    check(
        "price 0 -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0001", price_cents=0), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )
    check(
        "price 100 -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0002", price_cents=100), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )
    check(
        "side 'maybe' -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0003", side="maybe"), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )
    check(
        "ttl above max -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0004", ttl_seconds=100000), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )
    check(
        "ttl below min -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0005", ttl_seconds=1), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )
    check(
        "bad proposal_id charset -> 422",
        client.post("/trade-proposals", json=make_payload("../etc/passwd"), headers=TRADE_HEADERS).status_code == 422,
    )
    check(
        "over-long evidence line -> 422",
        client.post(
            "/trade-proposals", json=make_payload("val-check-0006", evidence=["x" * 501]), headers=TRADE_HEADERS
        ).status_code
        == 422,
    )

    # ── Read paths ──
    by_id = client.get("/trade-proposals/auth-check-0001", headers=TRADE_HEADERS)
    check("kta can read its proposal by id", by_id.status_code == 200 and by_id.json()["status"] == "pending")
    check(
        "controller token can read by id",
        client.get("/trade-proposals/auth-check-0001", headers=CONTROLLER_HEADERS).status_code == 200,
    )
    check("read by id without token -> 401", client.get("/trade-proposals/auth-check-0001").status_code == 401)
    check("unknown id -> 404", client.get("/trade-proposals/nope-nope-nope", headers=TRADE_HEADERS).status_code == 404)
    listing = client.get("/trade-proposals", headers=CONTROLLER_HEADERS).json()
    check("controller list contains the proposal", "auth-check-0001" in listing)

    # ── Decide auth ──
    decide_url = "/trade-proposals/auth-check-0001/decide"
    check(
        "kta trade token cannot decide -> 401",
        client.post(decide_url, json={"decision": "approve"}, headers=TRADE_HEADERS).status_code == 401,
    )
    check("decide without token -> 401", client.post(decide_url, json={"decision": "approve"}).status_code == 401)
    check(
        "bad decision value -> 422",
        client.post(decide_url, json={"decision": "maybe"}, headers=CONTROLLER_HEADERS).status_code == 422,
    )

    # ── Approve, then no double-decide ──
    approved = client.post(decide_url, json={"decision": "approve", "comment": "ok"}, headers=CONTROLLER_HEADERS)
    check("controller approve -> 200 approved", approved.status_code == 200 and approved.json()["status"] == "approved")
    check(
        "kta sees approved on next poll",
        client.get("/trade-proposals/auth-check-0001", headers=TRADE_HEADERS).json()["status"] == "approved",
    )
    check(
        "second decide -> 409",
        client.post(decide_url, json={"decision": "deny"}, headers=CONTROLLER_HEADERS).status_code == 409,
    )
    after_timeout = client.post("/trade-proposals/auth-check-0001/timeout", headers=TRADE_HEADERS)
    check(
        "timeout after approval no-ops, stays approved",
        after_timeout.status_code == 200 and after_timeout.json()["status"] == "approved",
    )

    # ── Deny ──
    client.post("/trade-proposals", json=make_payload("deny-check-0001"), headers=TRADE_HEADERS)
    denied = client.post(
        "/trade-proposals/deny-check-0001/decide", json={"decision": "deny"}, headers=CONTROLLER_HEADERS
    )
    check("deny -> denied", denied.status_code == 200 and denied.json()["status"] == "denied")

    # ── Expiry: the stale-quote guarantee ──
    client.post("/trade-proposals", json=make_payload("late-tap-0001"), headers=TRADE_HEADERS)
    clock["now"] = START_TIME + timedelta(seconds=SHORT_TTL_SECONDS + SECONDS_PAST_EXPIRY)
    late = client.post(
        "/trade-proposals/late-tap-0001/decide", json={"decision": "approve"}, headers=CONTROLLER_HEADERS
    )
    check("late approve -> 409", late.status_code == 409)
    check(
        "late-tapped proposal reads timed_out",
        client.get("/trade-proposals/late-tap-0001", headers=TRADE_HEADERS).json()["status"] == "timed_out",
    )

    clock["now"] = START_TIME
    client.post("/trade-proposals", json=make_payload("read-expiry-0001"), headers=TRADE_HEADERS)
    clock["now"] = START_TIME + timedelta(seconds=SHORT_TTL_SECONDS + SECONDS_PAST_EXPIRY)
    check(
        "list read path expires stale pending",
        client.get("/trade-proposals", headers=CONTROLLER_HEADERS).json()["read-expiry-0001"]["status"] == "timed_out",
    )

    # ── kta-reported timeout ──
    clock["now"] = START_TIME
    client.post("/trade-proposals", json=make_payload("kta-timeout-0001", ttl_seconds=900), headers=TRADE_HEADERS)
    check("timeout without token -> 401", client.post("/trade-proposals/kta-timeout-0001/timeout").status_code == 401)
    reported = client.post("/trade-proposals/kta-timeout-0001/timeout", headers=TRADE_HEADERS)
    check("kta-reported timeout -> timed_out", reported.status_code == 200 and reported.json()["status"] == "timed_out")
    check(
        "decide after timeout -> 409",
        client.post(
            "/trade-proposals/kta-timeout-0001/decide", json={"decision": "approve"}, headers=CONTROLLER_HEADERS
        ).status_code
        == 409,
    )

    # ── Pruning ──
    clock["now"] = START_TIME + timedelta(days=30)
    client.post("/trade-proposals", json=make_payload("prune-trigger-0001"), headers=TRADE_HEADERS)
    pruned = client.get("/trade-proposals", headers=CONTROLLER_HEADERS).json()
    check("old decided proposals pruned", "auth-check-0001" not in pruned and "deny-check-0001" not in pruned)
    check("fresh pending proposal survives prune", "prune-trigger-0001" in pruned)

    # ── Flag off ──
    flag["enabled"] = False
    check(
        "flag off: register -> 503",
        client.post("/trade-proposals", json=make_payload("flag-off-0001"), headers=TRADE_HEADERS).status_code == 503,
    )
    check("flag off: list -> 503", client.get("/trade-proposals", headers=CONTROLLER_HEADERS).status_code == 503)
    check(
        "flag off: decide -> 503",
        client.post(
            "/trade-proposals/prune-trigger-0001/decide", json={"decision": "approve"}, headers=CONTROLLER_HEADERS
        ).status_code
        == 503,
    )
    check(
        "flag off: bad token still 401 (auth before flag)",
        client.post("/trade-proposals", json=make_payload("flag-off-0002")).status_code == 401,
    )

    # ── Unconfigured server-side secret fails closed ──
    flag["enabled"] = True
    del os.environ[ntp.TRADE_TOKEN_ENV_VAR]
    check(
        "no server-side trade token configured -> 401 even with a header",
        client.post("/trade-proposals", json=make_payload("no-secret-0001"), headers=TRADE_HEADERS).status_code == 401,
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
