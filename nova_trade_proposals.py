# nova_trade_proposals.py
# Trade-proposal approval channel for the Kalshi trading agent ("kta", a
# separate repo/account on the Omen). kta registers a proposed trade over
# HTTP, Marvin approves or denies it from the Nova Controller, kta polls for
# the decision and places an order ONLY on "approved".
#
# Why this is NOT folded into pending_tool_approvals: that key holds
# coding-agent tool-call approvals. A money-moving approval must never be
# confusable with (or renderable as) one of those, so proposals live in their
# own state key, system/pending_trade_proposals.
#
# Auth split (Marvin's decision, 2026-10-18):
#   - X-Nova-Trade-Token / NOVA_TRADE_TOKEN  -> held by kta. Register, read one
#     proposal by id, self-report a timeout. kta can NOT decide and can NOT
#     list other proposals.
#   - X-Nova-Escalation-Token / NOVA_ESCALATION_TOKEN -> the existing
#     Controller-wide token, held only on Marvin's devices. Decide, and the
#     list view that backs the Controller card. kta never holds it.
# Every check fails closed (401) on a missing header, an unconfigured
# server-side env var, or a mismatch.
#
# Expiry is enforced on the read path and again inside decide, using this
# server's clock only: a pending proposal past expires_at reads as
# "timed_out", so a late tap can never approve a stale quote. No background
# task is needed.
#
# The whole feature is behind the trade_proposals.enabled flag (default off);
# while off every route returns 503.
#
# Routes live in an APIRouter here (included by nova_api.py) instead of in
# nova_api.py itself, so the logic can be exercised without importing
# nova_api.py, whose import-time Chroma connection fails when the Omen is
# unreachable.

import hmac
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from nova_config import (
    get_trade_proposal_default_ttl_seconds,
    get_trade_proposal_max_ttl_seconds,
    get_trade_proposal_min_ttl_seconds,
    get_trade_proposal_retention_days,
    is_trade_proposals_enabled,
)
from nova_notify import send_notification
from nova_state import get_state, write_state

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ── Constants ──────────────────────────────────────────────────

STATE_DOMAIN = "system"
STATE_ENTITY = "pending_trade_proposals"

TRADE_TOKEN_ENV_VAR = "NOVA_TRADE_TOKEN"  # nosec B105 - env var name, not a secret
ESCALATION_TOKEN_ENV_VAR = "NOVA_ESCALATION_TOKEN"  # nosec B105 - env var name, not a secret

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_TIMED_OUT = "timed_out"

# A proposal id is chosen by kta (so a retried register after a lost response
# is idempotent, and kta can use the same id as its client_order_id). Strict
# charset keeps it safe to put in a URL path.
PROPOSAL_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"

MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_ITEM_CHARS = 500
MAX_TITLE_CHARS = 200
MAX_TICKER_CHARS = 64
MAX_COMMENT_CHARS = 500

# Proposals with this little time left get a high-priority push, since the
# human has only a short window to act before the quote goes stale.
NEAR_EXPIRY_PUSH_TTL_SECONDS = 120
NEAR_EXPIRY_PUSH_PRIORITY = "high"

CENTS_PER_DOLLAR = 100
SECONDS_PER_MINUTE = 60

# Fields a retried register must repeat exactly. If a proposal_id already
# exists with different values here, that is a client bug (or a collision),
# never a silent overwrite of something Marvin may be looking at.
IMMUTABLE_FIELDS = (
    "ticker",
    "title",
    "side",
    "price_cents",
    "size_contracts",
    "probability_estimate",
    "ev_after_fees_usd",
    "confidence_score",
    "evidence",
)

# nova-api serves requests from a thread pool, and every mutation below is a
# whole-dict read-modify-write. This lock serializes them so two near-
# simultaneous registers/decides cannot drop each other's update. It assumes
# a single nova-api process (one uvicorn worker), which is how the Omen's
# systemd unit runs it.
_state_lock = threading.Lock()

router = APIRouter()


# ── Request models ─────────────────────────────────────────────


class TradeProposalCreateRequest(BaseModel):
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    ticker: str = Field(min_length=1, max_length=MAX_TICKER_CHARS)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    side: Literal["yes", "no"]
    price_cents: int = Field(ge=1, le=99)
    size_contracts: int = Field(ge=1)
    probability_estimate: float = Field(ge=0.0, le=1.0)
    ev_after_fees_usd: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    ttl_seconds: int | None = None


class TradeProposalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_CHARS)


# ── Auth + flag guards ─────────────────────────────────────────


def _token_matches(env_var_name: str, supplied: str | None) -> bool:
    """
    True only if the server has a non-empty secret configured in env_var_name
    and the supplied header value equals it. hmac.compare_digest avoids a
    timing side-channel on the comparison.
    """
    expected = os.environ.get(env_var_name)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _require_trade_token(supplied: str | None) -> None:
    """401 unless the caller presented kta's trade token."""
    if not _token_matches(TRADE_TOKEN_ENV_VAR, supplied):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Nova-Trade-Token")


def _require_controller_token(supplied: str | None) -> None:
    """401 unless the caller presented the Controller (escalation) token."""
    if not _token_matches(ESCALATION_TOKEN_ENV_VAR, supplied):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Nova-Escalation-Token")


def _require_trade_or_controller_token(trade_token: str | None, controller_token: str | None) -> None:
    """401 unless the caller presented either kta's trade token or the Controller token."""
    if _token_matches(TRADE_TOKEN_ENV_VAR, trade_token):
        return
    if _token_matches(ESCALATION_TOKEN_ENV_VAR, controller_token):
        return
    raise HTTPException(status_code=401, detail="Missing or invalid trade or controller token")


def _require_feature_enabled() -> None:
    """503 while trade_proposals.enabled is off, so a disabled feature is unmistakable to callers."""
    if not is_trade_proposals_enabled():
        raise HTTPException(status_code=503, detail="Trade proposals are disabled (trade_proposals.enabled is off)")


# ── Record helpers ─────────────────────────────────────────────


def _now() -> datetime:
    """Current server time. A function so tests can pin the clock."""
    return datetime.now()


def _load_proposals() -> dict:
    """Read the proposals dict from nova_state.db, minus the bookkeeping _updated_at key."""
    proposals = get_state(STATE_DOMAIN, STATE_ENTITY) or {}
    proposals.pop("_updated_at", None)
    return proposals


def _save_proposals(proposals: dict) -> None:
    """Overwrite the whole proposals dict. Callers must hold _state_lock."""
    write_state(STATE_DOMAIN, STATE_ENTITY, proposals)


def _expire_if_stale(record: dict, now: datetime) -> bool:
    """
    If this record is still pending but past its expires_at, flip it to
    timed_out in place. Returns True if it changed. This is the single place
    that decides "too late", used by both the read path and decide.
    """
    if record["status"] != STATUS_PENDING:
        return False
    if now < datetime.fromisoformat(record["expires_at"]):
        return False
    record["status"] = STATUS_TIMED_OUT
    record["decided_at"] = now.isoformat(timespec="seconds")
    return True


def _expire_all_stale(proposals: dict, now: datetime) -> bool:
    """Apply _expire_if_stale to every record. Returns True if anything changed."""
    changed = False
    for record in proposals.values():
        if _expire_if_stale(record, now):
            changed = True
    return changed


def _with_seconds_left(record: dict, now: datetime) -> dict:
    """
    A copy of the record for the Controller list view, with expires_in_seconds
    added while it is still pending. Stored timestamps are naive server-local
    time, so a browser in another timezone would misread expires_at by hours;
    a plain "seconds left" has no timezone to get wrong. Never stored.
    """
    shown = dict(record)
    if record["status"] == STATUS_PENDING:
        seconds_left = (datetime.fromisoformat(record["expires_at"]) - now).total_seconds()
        shown["expires_in_seconds"] = max(0, int(seconds_left))
    return shown


def _prune_old_decided(proposals: dict, now: datetime) -> None:
    """
    Drop non-pending records older than the retention window so the single
    state dict (rewritten whole on every mutation) cannot grow forever. A
    pending record is never pruned.
    """
    cutoff = now - timedelta(days=get_trade_proposal_retention_days())
    stale_ids = [
        proposal_id
        for proposal_id, record in proposals.items()
        if record["status"] != STATUS_PENDING and datetime.fromisoformat(record["created_at"]) < cutoff
    ]
    for proposal_id in stale_ids:
        del proposals[proposal_id]


def _resolve_ttl_seconds(requested: int | None) -> int:
    """Apply the default TTL when none was asked for, and 422 outside the configured bounds."""
    ttl_seconds = requested if requested is not None else get_trade_proposal_default_ttl_seconds()
    min_ttl = get_trade_proposal_min_ttl_seconds()
    max_ttl = get_trade_proposal_max_ttl_seconds()
    if ttl_seconds < min_ttl or ttl_seconds > max_ttl:
        raise HTTPException(status_code=422, detail=f"ttl_seconds must be between {min_ttl} and {max_ttl}")
    return ttl_seconds


def _clean_evidence(evidence: list[str]) -> list[str]:
    """Reject over-long evidence lines rather than silently truncating what Marvin will rely on."""
    for line in evidence:
        if len(line) > MAX_EVIDENCE_ITEM_CHARS:
            detail = f"each evidence item must be <= {MAX_EVIDENCE_ITEM_CHARS} chars"
            raise HTTPException(status_code=422, detail=detail)
    return evidence


def _build_record(req: TradeProposalCreateRequest, ttl_seconds: int, now: datetime) -> dict:
    """Assemble the stored record for a brand-new proposal."""
    return {
        "proposal_id": req.proposal_id,
        "ticker": req.ticker,
        "title": req.title,
        "side": req.side,
        "price_cents": req.price_cents,
        "size_contracts": req.size_contracts,
        "notional_usd": round(req.price_cents * req.size_contracts / CENTS_PER_DOLLAR, 2),
        "probability_estimate": req.probability_estimate,
        "ev_after_fees_usd": req.ev_after_fees_usd,
        "confidence_score": req.confidence_score,
        "evidence": _clean_evidence(req.evidence),
        "status": STATUS_PENDING,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
        "decided_at": None,
        "comment": None,
    }


def _matches_existing(record: dict, req: TradeProposalCreateRequest) -> bool:
    """True if a retried register repeats every immutable field of the stored record exactly."""
    submitted = req.model_dump()
    return all(record[field] == submitted[field] for field in IMMUTABLE_FIELDS)


def _push_new_proposal(record: dict, ttl_seconds: int) -> None:
    """
    Best-effort phone push on creation. ntfy.sh is a public relay that sees
    message content in plaintext, so this carries only what is needed to
    decide whether to open the Controller: no title, evidence, or EV.
    """
    minutes_left = max(1, round(ttl_seconds / SECONDS_PER_MINUTE))
    message = (
        f"{record['ticker']} {record['side'].upper()} @ {record['price_cents']}c "
        f"x{record['size_contracts']} - expires in {minutes_left} min"
    )
    priority = NEAR_EXPIRY_PUSH_PRIORITY if ttl_seconds <= NEAR_EXPIRY_PUSH_TTL_SECONDS else None
    send_notification(title="Trade proposal awaiting approval", message=message, tags="moneybag", priority=priority)


# ── Routes ─────────────────────────────────────────────────────


@router.post("/trade-proposals")
def create_trade_proposal(
    req: TradeProposalCreateRequest,
    background_tasks: BackgroundTasks,
    x_nova_trade_token: str | None = Header(None),
):
    """
    Register a pending trade proposal. Trade-token gated. Idempotent on
    proposal_id: repeating an identical register returns the stored record
    (no second push); repeating it with different values is a 409.
    """
    _require_trade_token(x_nova_trade_token)
    _require_feature_enabled()
    ttl_seconds = _resolve_ttl_seconds(req.ttl_seconds)

    with _state_lock:
        now = _now()
        proposals = _load_proposals()
        _expire_all_stale(proposals, now)

        existing = proposals.get(req.proposal_id)
        if existing is not None:
            if not _matches_existing(existing, req):
                detail = f"proposal_id '{req.proposal_id}' already exists with different contents"
                raise HTTPException(status_code=409, detail=detail)
            _save_proposals(proposals)
            return existing

        record = _build_record(req, ttl_seconds, now)
        proposals[req.proposal_id] = record
        _prune_old_decided(proposals, now)
        _save_proposals(proposals)

    background_tasks.add_task(_push_new_proposal, record, ttl_seconds)
    return record


@router.get("/trade-proposals")
def list_trade_proposals(x_nova_escalation_token: str | None = Header(None)):
    """
    All proposals, pending and decided, expiry applied. Controller-token
    gated: backs the Controller card. kta does not need (or get) this view.
    """
    _require_controller_token(x_nova_escalation_token)
    _require_feature_enabled()
    with _state_lock:
        now = _now()
        proposals = _load_proposals()
        if _expire_all_stale(proposals, now):
            _save_proposals(proposals)
    return {proposal_id: _with_seconds_left(record, now) for proposal_id, record in proposals.items()}


@router.get("/trade-proposals/{proposal_id}")
def get_trade_proposal(
    proposal_id: str,
    x_nova_trade_token: str | None = Header(None),
    x_nova_escalation_token: str | None = Header(None),
):
    """
    One proposal by id, expiry applied. Accepts kta's trade token or the
    Controller token. This is what kta polls for the decision.
    """
    _require_trade_or_controller_token(x_nova_trade_token, x_nova_escalation_token)
    _require_feature_enabled()
    with _state_lock:
        proposals = _load_proposals()
        record = proposals.get(proposal_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No trade proposal '{proposal_id}'")
        if _expire_if_stale(record, _now()):
            _save_proposals(proposals)
    return record


@router.post("/trade-proposals/{proposal_id}/decide")
def decide_trade_proposal(
    proposal_id: str,
    req: TradeProposalDecisionRequest,
    x_nova_escalation_token: str | None = Header(None),
):
    """
    Approve or deny a pending proposal. Controller-token gated; kta can never
    reach this. Expiry is checked here, under the same lock as the write, so a
    late tap on a stale quote gets a 409 and the record reads timed_out.
    """
    _require_controller_token(x_nova_escalation_token)
    _require_feature_enabled()

    with _state_lock:
        now = _now()
        proposals = _load_proposals()
        record = proposals.get(proposal_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No trade proposal '{proposal_id}'")

        if _expire_if_stale(record, now):
            _save_proposals(proposals)
        if record["status"] != STATUS_PENDING:
            raise HTTPException(status_code=409, detail=f"Proposal '{proposal_id}' is already '{record['status']}'")

        record["status"] = STATUS_APPROVED if req.decision == "approve" else STATUS_DENIED
        record["decided_at"] = now.isoformat(timespec="seconds")
        record["comment"] = req.comment
        _save_proposals(proposals)

    return {"status": record["status"], "proposal_id": proposal_id}


@router.post("/trade-proposals/{proposal_id}/timeout")
def timeout_trade_proposal(proposal_id: str, x_nova_trade_token: str | None = Header(None)):
    """
    kta's self-reported timeout when its own poll loop gives up. Trade-token
    gated. No-ops (200, unchanged record) if the proposal was already
    decided: it never overwrites a real human decision.
    """
    _require_trade_token(x_nova_trade_token)
    _require_feature_enabled()

    with _state_lock:
        now = _now()
        proposals = _load_proposals()
        record = proposals.get(proposal_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No trade proposal '{proposal_id}'")

        _expire_if_stale(record, now)
        if record["status"] == STATUS_PENDING:
            record["status"] = STATUS_TIMED_OUT
            record["decided_at"] = now.isoformat(timespec="seconds")
        _save_proposals(proposals)

    return {"status": record["status"], "proposal_id": proposal_id}
