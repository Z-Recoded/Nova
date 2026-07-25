# nova_api.py
# Nova FastAPI server
#
# Endpoints:
#   POST /ask                       → RAG query (full pipeline)
#   GET  /graph                     → full node/edge map
#   GET  /neighbors?file=X          → outgoing + incoming edges for X
#   GET  /context-budget?query=X    → ranked file list for a query
#   POST /ingest                    → trigger incremental or full ingest
#   POST /rebuild-node              → rebuild graph node for one file
#   GET  /v1/models                 → OpenAI-compatible model list (Open WebUI)
#   POST /v1/chat/completions       → OpenAI-compatible chat (Open WebUI → RAG pipeline)
#   GET  /nova-log                  → Nova Log Health dashboard (HTML)
#   GET  /nova-log/data             → Nova Log Health dashboard data (JSON)
#   GET  /nova-log/queries          → Nova Log Query view — recent queries (JSON)
#   GET  /nova-log/benchmarks       → Nova Log Benchmark view — recent golden-query runs (JSON)
#   POST /agent/task                → run a coding task via Nova's coding sub-agent
#   GET  /headroom                  → resource headroom report (VRAM/RAM/CPU + task capacity)
#   GET  /embedding-viz             → Embedding-Space Visualization page (HTML)
#   GET  /embedding-viz/data        → Embedding-Space Visualization data (JSON, optional ?query=, ?refresh=)
#   POST /escalations               → register a new pending escalation (86bax0wkj)
#   GET  /escalations                → all escalations, pending and resolved
#   POST /escalations/{id}/answer   → submit Marvin's answer (token-gated), resumes the session in the background
#   GET  /escalations-ui            → redirects to /controller (86baxahn7)
#   GET  /controller                → Nova Controller Feed page (HTML, PWA-installable)
#   GET  /dispatch-log              → merged dispatch/outcome history (JSON)
#   GET  /label-queue               → unlabeled tool-call/blend-flag/dpo-verify entries (JSON)
#   POST /label-queue/{kind}/{id}/decide → patch a label decision (token-gated)
#   GET  /training-data-status      → live DPO pair count, coverage, threshold status
#
# Run:
#   cd C:/Nova
#   nova-env\Scripts\uvicorn nova_api:app --host 0.0.0.0 --port 8000 --reload

import hmac
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from graph_builder import (
    build_graph,
    get_context_budget,
    get_neighbors,
    rebuild_node,
)
from ingest import run_ingestion
from nova_clickup_client import add_comment, add_tag, remove_tag
from nova_embedding_viz import build_embedding_viz_data
from nova_headroom import get_headroom_report
from nova_log import (
    DEFAULT_BENCHMARK_RUNS_LIMIT,
    DEFAULT_RECENT_QUERIES_LIMIT,
    compute_health_summary,
    get_benchmark_runs,
    get_recent_queries,
)
from nova_omen_dispatch import resume_headless_task
from nova_orchestrator import run_coding_task
from nova_query import ask
from nova_scheduled_dispatch import handle_dispatch_outcome, is_dispatch_currently_running
from nova_sources import SOURCES
from nova_state import get_state, write_state
from nova_task_queue import TIER_PENDING_TAG, TIER_TAGS, TIERS

app = FastAPI(title="Nova API", version="0.3")

GRAPH_PATH = os.path.join(os.path.dirname(__file__), "nova_graph.json")

# Model name shown in Open WebUI's model picker. All requests for this model
# run through Nova's full RAG pipeline — never raw Ollama.
OPENAI_MODEL_ID = "nova"


# ── Helpers ────────────────────────────────────────────────────


def _load_graph_json() -> dict:
    if not os.path.exists(GRAPH_PATH):
        return {"nodes": [], "edges": []}
    try:
        with open(GRAPH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nodes": [], "edges": []}


def _resolve_source(filepath: str) -> tuple[str, str]:
    """Find the project + description for a file path from SOURCES config."""
    for src in SOURCES:
        if filepath.startswith(src["path"]):
            return src["project"], src["description"]
    return "Unknown", ""


# ── Request / Response models ──────────────────────────────────


class AskRequest(BaseModel):
    query: str
    history: list[dict] | None = None
    persist: bool = True


class IngestRequest(BaseModel):
    full: bool = False


class RebuildNodeRequest(BaseModel):
    filepath: str


class AgentTaskRequest(BaseModel):
    task: str
    category: str | None = None


class UsageHistoryPushRequest(BaseModel):
    source_machine: str
    daily_usage: dict


class ActivityProfilePushRequest(BaseModel):
    source_machine: str
    activity_profile: dict


class DispatchPauseRequest(BaseModel):
    paused: bool
    reason: str | None = None


class EscalationCreateRequest(BaseModel):
    task_id: str
    task_name: str
    session_id: str | None = None
    worktree_path: str | None = None
    worktree_name: str | None = None
    question: str | None = None
    options_considered: list[str] = []
    context: str | None = None
    fuel_source: str | None = None
    phase: str
    malformed: bool = False


class EscalationAnswerRequest(BaseModel):
    answer_text: str


class TierProposalCreateRequest(BaseModel):
    task_id: str
    task_name: str
    trigger: str
    previous_tier: str | None = None
    proposed_tier: str
    confidence: str
    reasoning: str


class TierDecisionRequest(BaseModel):
    decision: str  # "accept" | "override"
    comment: str | None = None
    final_tier: str | None = None
    reasoning: str | None = None


# ── Routes ─────────────────────────────────────────────────────


@app.get("/")
def root():
    return {"status": "Nova API online", "version": "0.3"}


@app.post("/ask")
def ask_nova(req: AskRequest):
    """
    Full RAG pipeline.
    Returns answer, sources, category, and chunk metadata.
    """
    try:
        result = ask(
            query=req.query,
            history=req.history or [],
            persist=req.persist,
        )
        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "category": result["category"],
            "chunks": [
                {
                    "text": c["text"],
                    "filename": c["metadata"].get("filename", ""),
                    "project": c["metadata"].get("project", ""),
                    "distance": c["distance"],
                }
                for c in result["chunks"]
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/graph")
def get_graph():
    """Return the full node/edge map from nova_graph.json."""
    graph = _load_graph_json()
    if not graph["nodes"]:
        raise HTTPException(
            status_code=404,
            detail="Graph not built yet. POST /ingest or run graph_builder.py first.",
        )
    return graph


@app.get("/neighbors")
def neighbors(file: str = Query(..., description="Filename, e.g. 'My Note.md'")):
    """
    Return all outgoing and incoming edges for a given file.
    """
    result = get_neighbors(file)
    if not result["outgoing"] and not result["incoming"]:
        # Check if the file even exists in the graph
        graph = _load_graph_json()
        known = {n["id"] for n in graph["nodes"]}
        if file not in known:
            raise HTTPException(status_code=404, detail=f"File '{file}' not found in graph.")
    return result


@app.get("/context-budget")
def context_budget(
    query: str = Query(..., description="Natural language query"),
    n_seeds: int = Query(8, ge=1, le=30),
    n_files: int = Query(15, ge=1, le=50),
    max_hops: int = Query(2, ge=0, le=4),
):
    """
    Return a ranked list of filenames most relevant to `query`,
    combining semantic search seeds with graph proximity expansion.
    """
    ranked = get_context_budget(query, n_seeds=n_seeds, n_files=n_files, max_hops=max_hops)
    return {"query": query, "files": ranked, "count": len(ranked)}


@app.post("/ingest")
def trigger_ingest(req: IngestRequest):
    """Trigger incremental (default) or full re-ingest."""
    try:
        run_ingestion(full=req.full)
        # Rebuild the graph after ingest
        graph = build_graph()
        return {
            "status": "ok",
            "mode": "full" if req.full else "incremental",
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── OpenAI-compatible routes (Open WebUI) ──────────────────────


def _split_openai_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    Split an OpenAI-style messages array into (query, history) for ask().

    Drops system messages — Nova builds its own system prompt inside
    nova_query.build_system_prompt(). The last user message becomes the
    query; everything before it becomes the history, which ask() already
    accepts in this same role/content dict format.
    """
    chat_messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not chat_messages or chat_messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="Last message must be from the user.")

    query = chat_messages[-1]["content"]
    history = chat_messages[:-1]
    return query, history


def _append_sources_footer(answer: str, sources: list[str]) -> str:
    """
    Append a markdown footer listing the retrieved source files, so every
    Open WebUI response shows which notes grounded the answer.
    """
    if not sources:
        return answer
    return answer + "\n\n---\n**Sources:** " + ", ".join(sorted(sources))


def _build_completion_response(content: str, model: str) -> dict:
    """Build a non-streaming OpenAI chat.completion response body."""
    return {
        "id": f"nova-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_completion_response(content: str, model: str) -> StreamingResponse:
    """
    Stream the answer as OpenAI-style server-sent events.

    The answer is already fully generated by the time we respond (ask() is
    blocking), so we emit it as a single content delta followed by a finish
    chunk and [DONE] — the simplest shape Open WebUI accepts.
    """
    completion_id = f"nova-{uuid.uuid4()}"
    created = int(time.time())

    def make_chunk(delta: dict, finish_reason: str | None) -> str:
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    def event_stream():
        yield make_chunk({"role": "assistant", "content": content}, finish_reason=None)
        yield make_chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/v1/models")
def list_models():
    """OpenAI-compatible model list. Open WebUI calls this to fill its model picker."""
    return {
        "object": "list",
        "data": [
            {
                "id": OPENAI_MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "nova",
            }
        ],
    }


@app.post("/v1/chat/completions")
def openai_chat_completions(body: dict):
    """
    OpenAI-compatible chat endpoint for Open WebUI.

    Runs the last user message through Nova's full RAG pipeline via ask(),
    with the prior turns passed as history. persist=False because Open WebUI
    resends the whole conversation every turn and keeps its own history —
    persisting here would double-write Nova's memory store.
    """
    try:
        query, history = _split_openai_messages(body.get("messages", []))
        model = body.get("model", OPENAI_MODEL_ID)
        wants_stream = bool(body.get("stream", False))

        result = ask(query=query, history=history, persist=False)
        content = _append_sources_footer(result["answer"], result["sources"])

        if wants_stream:
            return _stream_completion_response(content, model)
        return _build_completion_response(content, model)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/agent/task")
def agent_task(req: AgentTaskRequest):
    """
    Run a coding task through Nova's coding sub-agent (nova_orchestrator.py).

    The task runs in a disposable git worktree — nothing touches the live
    Nova codebase. Returns the branch name and diff for human review; never
    auto-merges. `category` optionally selects a Nova Skills Library file
    (coding/retrieval/financial/orchestration/lore/memory) to prepend to
    the task's context — no effect unless skill_injection is enabled in
    nova_config.json.
    """
    try:
        return run_coding_task(req.task, category=req.category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/rebuild-node")
def trigger_rebuild_node(req: RebuildNodeRequest):
    """Rebuild the graph node for a single file (used by nova_watcher)."""
    try:
        graph = rebuild_node(req.filepath)
        filename = os.path.basename(req.filepath)
        node = next((n for n in graph["nodes"] if n["id"] == filename), None)
        edges_out = [e for e in graph["edges"] if e["source"] == filename]
        return {
            "status": "ok",
            "file": filename,
            "chunk_count": node["chunk_count"] if node else 0,
            "outgoing_edges": len(edges_out),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Resource headroom (Phase 1.5 self-monitoring) ───────────────


@app.get("/headroom")
def headroom():
    """
    Return Nova's current resource headroom report — VRAM (nvidia-smi), RAM
    + CPU (psutil), ingestion queue depth, active session count, and a
    plain-English summary of how many heavy/medium/light tasks Nova could
    still take on before hitting nominal load thresholds.
    """
    try:
        return get_headroom_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Claude Code usage history (86bawx7vj usage-history baseline) ────


@app.post("/usage-history")
def push_usage_history(req: UsageHistoryPushRequest):
    """
    Merge one machine's locally-computed daily Claude Code usage history into
    nova_state.db (system/claude_usage_history), keyed by source machine.
    Called by nova_usage_logger.py's SessionEnd-hook-triggered push — each
    machine running Claude Code (Aero interactive today, the Omen once
    headless runs land there) pushes its own local aggregate; nothing here
    computes usage itself, it only merges what's pushed.
    """
    try:
        merged = get_state("system", "claude_usage_history") or {}
        merged.pop("_updated_at", None)
        merged[req.source_machine] = req.daily_usage
        write_state("system", "claude_usage_history", merged)
        return {"status": "ok", "source_machine": req.source_machine, "days": len(req.daily_usage)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/usage-history")
def get_usage_history():
    """Return the merged Claude Code usage history across every machine that has pushed to it."""
    return get_state("system", "claude_usage_history") or {"_note": "No usage history pushed yet."}


@app.post("/activity-profile")
def push_activity_profile(req: ActivityProfilePushRequest):
    """
    Merge one machine's locally-computed Claude Code activity profile (an
    hour-of-day/day-of-week message-count histogram) into nova_state.db
    (system/claude_activity_profile), keyed by source machine. Called by
    nova_usage_logger.py's SessionEnd-hook-triggered push, alongside
    /usage-history — groundwork for the autonomous-dispatch dual-fuel design
    (86bawpvzz) to find genuine idle windows instead of guessing a fixed
    reserve. Deliberately no cross-machine summing here yet — no real
    consumer needs a merged view until the idle-window scheduler itself
    exists.
    """
    try:
        merged = get_state("system", "claude_activity_profile") or {}
        merged.pop("_updated_at", None)
        merged[req.source_machine] = req.activity_profile
        write_state("system", "claude_activity_profile", merged)
        return {
            "status": "ok",
            "source_machine": req.source_machine,
            "total_messages": req.activity_profile.get("total_messages"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/activity-profile")
def get_activity_profile():
    """Return the merged Claude Code activity profile across every machine that has pushed to it."""
    return get_state("system", "claude_activity_profile") or {"_note": "No activity profile pushed yet."}


# ── Headless-dispatch pause switch (2026-07-16 cross-machine fix) ──


@app.post("/dispatch-pause")
def set_dispatch_pause_route(req: DispatchPauseRequest):
    """
    Set the headless-dispatch pause switch in nova_state.db
    (system/dispatch_pause) — always the Omen's own copy, regardless of
    which machine calls this route. nova_escalation.py's
    is_dispatch_paused()/set_dispatch_pause() call this over HTTP instead
    of importing nova_state.py directly, so the switch is visible
    identically whether checked from the Aero or natively on the Omen.
    Fixes a real bug: nova_state.py's DB_PATH is a hardcoded Windows path
    that silently resolved to a disconnected file when read on Linux,
    making a pause set from the Aero invisible to anything checking it on
    the Omen.
    """
    try:
        data = {
            "paused": req.paused,
            "reason": req.reason,
            "paused_at": datetime.now().isoformat(timespec="seconds") if req.paused else None,
        }
        write_state("system", "dispatch_pause", data)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/dispatch-pause")
def get_dispatch_pause_route():
    """Return the current headless-dispatch pause state, or the honest 'never set' default."""
    state = get_state("system", "dispatch_pause")
    if state is None:
        return {"paused": False}
    return state


# ── Escalation Answer UI (86bax0wkj) ───────────────────────────
# nova_escalations.html itself was retired 2026-07-19 (86baxahn7) — its
# card logic was ported into nova_controller.html, which supersedes it
# as the real Controller entry point (see /escalations-ui below, now a
# redirect).


def _check_escalation_token(x_nova_escalation_token: str | None) -> None:
    """
    Fail-closed token check for the one cost-incurring write route on this
    otherwise-unauthenticated Tailscale-only surface (ahead of 86bawf2z2's
    general auth ticket). 401 on a missing header, a missing/unconfigured
    server-side env var, or a mismatch — never a soft pass.
    hmac.compare_digest avoids a timing side-channel on the comparison.
    """
    expected = os.environ.get("NOVA_ESCALATION_TOKEN")
    if not expected or not x_nova_escalation_token or not hmac.compare_digest(x_nova_escalation_token, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Nova-Escalation-Token")


@app.post("/escalations")
def create_escalation(req: EscalationCreateRequest):
    """
    Register a new pending escalation — called by
    nova_scheduled_dispatch.py's _handle_escalation() when a headless
    dispatch pauses mid-task for a real answer. Not token-gated: this only
    records that a question exists, it doesn't spend anything or resume a
    session. Merge-into-dict keyed by a generated escalation_id, same
    read-modify-write idiom as /usage-history and /activity-profile.
    """
    try:
        escalation_id = str(uuid.uuid4())
        pending = get_state("system", "pending_escalations") or {}
        pending.pop("_updated_at", None)
        pending[escalation_id] = {
            **req.dict(),
            "escalation_id": escalation_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "answered_at": None,
            "answer_text": None,
            "resume_result": None,
        }
        write_state("system", "pending_escalations", pending)
        return pending[escalation_id]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/escalations")
def get_escalations():
    """All escalations, pending and resolved — not token-gated (read-only)."""
    return get_state("system", "pending_escalations") or {}


def _resume_escalated_task(escalation_id: str) -> None:
    """
    Runs in the background after POST /escalations/{id}/answer returns.
    Resumes the exact original session/worktree via
    nova_omen_dispatch.resume_headless_task(), removes the awaiting-answer
    ClickUp tag, and hands the result to
    nova_scheduled_dispatch.handle_dispatch_outcome() so ClickUp comment/log
    behavior is identical to a fresh dispatch's tail — including
    re-escalating uncapped if the resumed run asks another question.
    Wrapped so a failure updates status: "error" rather than vanishing
    silently into an unawaited background task.
    """
    pending = get_state("system", "pending_escalations") or {}
    record = pending.get(escalation_id)
    if record is None:
        return

    try:
        result = resume_headless_task(record["worktree_path"], record["session_id"], record["answer_text"])
    except Exception as e:
        record["status"] = "error"
        record["resume_result"] = {"error": str(e)}
        pending.pop("_updated_at", None)
        pending[escalation_id] = record
        write_state("system", "pending_escalations", pending)
        return

    try:
        remove_tag(record["task_id"], "awaiting-answer")
    except Exception as e:
        print(f"Failed to remove awaiting-answer tag on {record['task_id']}: {e}")

    record["status"] = "resolved"
    record["resume_result"] = result
    pending.pop("_updated_at", None)
    pending[escalation_id] = record
    write_state("system", "pending_escalations", pending)

    handle_dispatch_outcome(record["task_id"], record["task_name"], result, phase="resume")


@app.post("/escalations/{escalation_id}/answer")
def answer_escalation(
    escalation_id: str,
    req: EscalationAnswerRequest,
    background_tasks: BackgroundTasks,
    x_nova_escalation_token: str | None = Header(None),
):
    """
    Accept Marvin's answer immediately (fire-and-forget) and resume the
    exact same session/worktree in the background. Token-gated — the
    first cost-incurring write route on this surface (see
    _check_escalation_token()).
    """
    _check_escalation_token(x_nova_escalation_token)

    pending = get_state("system", "pending_escalations") or {}
    record = pending.get(escalation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No escalation '{escalation_id}'")
    if record["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Escalation '{escalation_id}' is already '{record['status']}'")

    record["status"] = "resuming"
    record["answered_at"] = datetime.now().isoformat(timespec="seconds")
    record["answer_text"] = req.answer_text
    pending.pop("_updated_at", None)
    pending[escalation_id] = record
    write_state("system", "pending_escalations", pending)

    background_tasks.add_task(_resume_escalated_task, escalation_id)
    return {"status": "resuming", "escalation_id": escalation_id}


# /escalations-ui itself now lives below, in the Nova Controller UX
# section, as a redirect to /controller (86baxahn7) — the Feed page
# supersedes this as the real entry point.


# ── Task Tiering (86bb01wur) ────────────────────────────────────
# Reuses the exact propose->register->notify->answer pattern above, applied
# to a different trigger: deciding a task's autonomy tier at creation/
# rescope time instead of a headless run pausing mid-task. See CLAUDE.md's
# Task Tiering subsection for the full design and nova_task_queue.py for
# propose_tier()/detect_tier_candidates() (the polling-based detection —
# no ClickUp webhooks exist anywhere in this codebase, confirmed by grep).


@app.get("/tier-watermarks")
def get_tier_watermarks():
    """
    {task_id: description_hash} -- lets nova_task_queue.detect_tier_candidates()
    tell "new" (id never seen) from "rescoped" (description hash changed)
    from "unchanged" (skip) across polling cycles. Content-hashed rather
    than ClickUp's own date_updated field -- date_updated changes any time
    Nova itself tags a task (confirmed live 2026-07-19: add_tag()/
    remove_tag() alone bump it), which made every proposal registration or
    accept/override decision look like a fresh rescope on the very next
    poll, a self-perpetuating duplicate-proposal loop. Not token-gated
    (read-only).
    """
    return get_state("system", "task_tier_watermarks") or {}


@app.post("/tier-watermarks")
def set_tier_watermarks(watermarks: dict[str, Any]):
    """
    Full overwrite, not merge-by-key -- the caller (detect_tier_candidates())
    already computed the complete merged {task_id: description_hash} map
    itself before posting it back. Not token-gated: this only tracks what
    Nova has already seen, it doesn't change board state or spend anything.
    """
    try:
        write_state("system", "task_tier_watermarks", watermarks)
        return {"status": "ok", "count": len(watermarks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/tier-proposals")
def create_tier_proposal(req: TierProposalCreateRequest):
    """
    Register a new pending tier proposal -- called by
    nova_scheduled_dispatch.py's polling loop once
    nova_task_queue.detect_tier_candidates() finds a new/rescoped task. Not
    token-gated: this only records that a proposal exists, it doesn't
    change board state or spend anything. Same read-modify-write idiom as
    /escalations.
    """
    try:
        proposal_id = str(uuid.uuid4())
        pending = get_state("system", "pending_tier_proposals") or {}
        pending.pop("_updated_at", None)
        pending[proposal_id] = {
            **req.dict(),
            "proposal_id": proposal_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "decided_at": None,
            "final_tier": None,
            "override_reasoning": None,
        }
        write_state("system", "pending_tier_proposals", pending)
        return pending[proposal_id]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/tier-proposals")
def get_tier_proposals():
    """All tier proposals, pending and decided -- not token-gated (read-only)."""
    return get_state("system", "pending_tier_proposals") or {}


@app.post("/tier-proposals/{proposal_id}/decide")
def decide_tier_proposal(
    proposal_id: str,
    req: TierDecisionRequest,
    x_nova_escalation_token: str | None = Header(None),
):
    """
    Accept or override a pending tier proposal. Token-gated, reusing the
    same X-Nova-Escalation-Token/NOVA_ESCALATION_TOKEN as the escalation-
    answer route above -- one Controller-wide auth surface, deliberately
    not a second secret to manage, ahead of 86bawf2z2's general auth
    ticket. Unlike answering an escalation, this doesn't fire a background
    resume (nothing async to do) -- it's a direct ClickUp tag/comment
    update plus a state write, so it completes synchronously.

    "accept": final_tier = the proposed tier, comment optional. "override":
    final_tier + reasoning are required (422 if missing) -- the whole
    point of an override is a real, templated reason, not a silent change.
    """
    _check_escalation_token(x_nova_escalation_token)

    pending = get_state("system", "pending_tier_proposals") or {}
    record = pending.get(proposal_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No tier proposal '{proposal_id}'")
    if record["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Tier proposal '{proposal_id}' is already '{record['status']}'")

    if req.decision == "accept":
        final_tier = record["proposed_tier"]
        override_reasoning = None
        new_status = "accepted"
    elif req.decision == "override":
        if not req.final_tier or req.final_tier not in TIERS:
            raise HTTPException(status_code=422, detail=f"final_tier must be one of {TIERS}")
        if not req.reasoning or not req.reasoning.strip():
            raise HTTPException(status_code=422, detail="reasoning is required for an override")
        final_tier = req.final_tier
        override_reasoning = req.reasoning
        new_status = "overridden"
    else:
        raise HTTPException(status_code=422, detail="decision must be 'accept' or 'override'")

    task_id = record["task_id"]
    for tier_name, tag_name in TIER_TAGS.items():
        if tier_name != final_tier:
            try:
                remove_tag(task_id, tag_name)
            except Exception as e:
                print(f"Failed to remove tier tag '{tag_name}' from {task_id}: {e}")
    try:
        add_tag(task_id, TIER_TAGS[final_tier])
    except Exception as e:
        print(f"Failed to add tier tag '{TIER_TAGS[final_tier]}' to {task_id}: {e}")
    try:
        remove_tag(task_id, TIER_PENDING_TAG)
    except Exception as e:
        print(f"Failed to remove '{TIER_PENDING_TAG}' from {task_id}: {e}")

    record["status"] = new_status
    record["decided_at"] = datetime.now().isoformat(timespec="seconds")
    record["final_tier"] = final_tier
    record["override_reasoning"] = override_reasoning
    pending.pop("_updated_at", None)
    pending[proposal_id] = record
    write_state("system", "pending_tier_proposals", pending)

    comment_lines = [f"**Tier decided — {record['task_name']}**", "", f"- final tier: {final_tier}"]
    if req.decision == "accept":
        comment_lines.append(f"- accepted Nova's suggestion (confidence: {record.get('confidence')})")
        if req.comment:
            comment_lines.append(f"- comment: {req.comment}")
    else:
        comment_lines.append(f"- overridden from Nova's suggestion ({record.get('proposed_tier')})")
        comment_lines.append(f"- reasoning: {override_reasoning}")
    try:
        add_comment(task_id, "\n".join(comment_lines))
    except Exception as e:
        print(f"Failed to post tier-decision comment on {task_id}: {e}")

    return record


# ── Nova Controller UX (86baxahn7) ──────────────────────────────
# The consolidated Feed page superseding /escalations-ui — one scroll
# merging escalations, tier proposals, dispatch outcomes, and tool-call/
# blend-flag labeling prompts, per CLAUDE.md's Nova Controller UX
# subsection. Tutor-prompt and differential-scorer card types are
# deliberately not built — no nova_tutor*.py/nova_differential*.py file
# exists anywhere in this repo (confirmed by grep before scoping this).

CONTROLLER_HTML_PATH = os.path.join(os.path.dirname(__file__), "nova_controller.html")
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "manifest.json")
SERVICE_WORKER_PATH = os.path.join(os.path.dirname(__file__), "sw.js")
ICON_192_PATH = os.path.join(os.path.dirname(__file__), "icon-192.png")
ICON_512_PATH = os.path.join(os.path.dirname(__file__), "icon-512.png")

SCHEDULED_DISPATCH_LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "scheduled_dispatch_log.jsonl")
AGENT_TASK_OUTCOMES_PATH = os.path.join(os.path.dirname(__file__), "logs", "agent_task_outcomes.jsonl")
TOOL_CALL_LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "tool_call_log.jsonl")
TRAINING_FLAGS_PATH = os.path.join(os.path.dirname(__file__), "logs", "training_flags.jsonl")

DEFAULT_LABEL_QUEUE_LIMIT = 50  # keep the page light -- there are 1700+ tool-call entries total


def _read_jsonl_file(path: str) -> list[dict]:
    """Shared JSONL reader — same silently-skip-malformed-lines convention as nova_scheduled_dispatch._read_jsonl()."""
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


@app.get("/controller")
def controller_page():
    return FileResponse(CONTROLLER_HTML_PATH, media_type="text/html")


@app.get("/escalations-ui")
def escalations_ui_redirect():
    """
    /escalations-ui now redirects to /controller -- the Feed page
    supersedes it as the real Controller entry point (86baxahn7). Kept as
    a redirect, not removed outright, so any existing bookmark/link still
    lands somewhere real.
    """
    return RedirectResponse(url="/controller")


@app.get("/manifest.json")
def manifest():
    return FileResponse(MANIFEST_PATH, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(SERVICE_WORKER_PATH, media_type="application/javascript")


@app.get("/icon-192.png")
def icon_192():
    return FileResponse(ICON_192_PATH, media_type="image/png")


@app.get("/icon-512.png")
def icon_512():
    return FileResponse(ICON_512_PATH, media_type="image/png")


@app.get("/dispatch-log")
def get_dispatch_log(limit: int = 50):
    """
    Merged, sorted view of every real headless-dispatch outcome — backs
    the Feed's "completed diffs" cards and the Stories rail. Reads
    scheduled_dispatch_log.jsonl (per-firing outcomes, has phase/
    fuel_source/cost_usd) and agent_task_outcomes.jsonl (human merge/
    discard review decisions, keyed by branch not task_id — kept as a
    separate "kind" rather than force-joined, since they don't share a
    reliable join key). Most-recent-first, capped to `limit`.
    """
    dispatches = [{"kind": "dispatch", **e} for e in _read_jsonl_file(SCHEDULED_DISPATCH_LOG_PATH)]
    outcomes = [{"kind": "outcome", **e} for e in _read_jsonl_file(AGENT_TASK_OUTCOMES_PATH)]
    merged = dispatches + outcomes
    merged.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return {"entries": merged[:limit]}


@app.get("/in-flight-status")
def get_in_flight_status():
    """
    Is a headless dispatch running right now, and if so which task —
    backs the Nova Controller's live status widget (86bb3cey0). Thin
    wrapper: nova_scheduled_dispatch.py owns the actual lock/marker-file
    logic (same layering as every other dispatch route here). Scoped to
    the headless dispatch lane only, not the interactive/native
    nova_orchestrator.py lane — see 86bb3cey0's plan for why (the
    native lane only ever runs on the Aero, which this route can't see
    when served from the Omen).
    """
    return is_dispatch_currently_running()


@app.get("/label-queue")
def get_label_queue(limit: int = DEFAULT_LABEL_QUEUE_LIMIT):
    """
    Unlabeled tool-call and blend-flag entries awaiting a human judge-pass
    — backs the Feed's swipe-labeling cards, the real UX target of
    86baxahn7 (both fields already exist waiting for exactly this:
    tool_call_log.jsonl's was_necessary/was_used start null,
    training_flags.jsonl's correction starts "").

    blend_flag entries have no stable id field in the log itself (unlike
    tool_call_id for tool calls) -- "id" here is a synthetic
    "line:<index>:<timestamp>" token, checked again at decide-time so a
    stale index (the file changed underneath) fails loudly (409) rather
    than silently patching the wrong entry.

    `limit` is applied per kind, not to the merged total -- found live
    while building the dpo_verify kind (86bax4akx): tool_call entries are
    so much more numerous (1700+) and recent than blend_flag/dpo_verify
    that a single merge-then-truncate silently starved out every
    blend_flag and dpo_verify card at the real default limit (50) --
    exactly the rarer, higher-value human judgments this queue exists
    for. Each kind now gets its own `limit`-sized slice before merging,
    so a busy tool-call day can no longer hide every training-data card.
    """
    tool_calls = [
        {"kind": "tool_call", "id": e["tool_call_id"], **e}
        for e in _read_jsonl_file(TOOL_CALL_LOG_PATH)
        if e.get("was_necessary") is None
    ]
    blend_flags = [
        {"kind": "blend_flag", "id": f"line:{i}:{e.get('timestamp')}", **e}
        for i, e in enumerate(_read_jsonl_file(TRAINING_FLAGS_PATH))
        if e.get("correction") == ""
    ]
    # dpo_verify: already-corrected pairs awaiting the "is this correction
    # actually good" judge-pass (86bax4akx's verification-status scope item)
    # -- distinct from blend_flags above, which are pairs that don't have a
    # correction written yet at all. Same synthetic id scheme as blend_flag.
    dpo_verify = [
        {"kind": "dpo_verify", "id": f"line:{i}:{e.get('timestamp')}", **e}
        for i, e in enumerate(_read_jsonl_file(TRAINING_FLAGS_PATH))
        if e.get("correction") and not e.get("verification_status")
    ]

    def _newest(entries: list[dict]) -> list[dict]:
        return sorted(entries, key=lambda e: e.get("timestamp") or "", reverse=True)[:limit]

    merged = _newest(tool_calls) + _newest(blend_flags) + _newest(dpo_verify)
    merged.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return {"entries": merged}


class LabelDecisionRequest(BaseModel):
    was_necessary: bool | None = None  # tool_call kind
    was_used: bool | None = None  # tool_call kind
    correction: str | None = None  # blend_flag kind
    verification_status: str | None = None  # dpo_verify kind: "confirmed_good" | "needs_rework"


@app.post("/label-queue/{kind}/{entry_id:path}/decide")
def decide_label_queue_entry(
    kind: str,
    entry_id: str,
    req: LabelDecisionRequest,
    x_nova_escalation_token: str | None = Header(None),
):
    """
    Patch one tool_call_log.jsonl or training_flags.jsonl entry in place —
    a real judge-pass write, token-gated the same as the tier-decide
    route. Read-all/rewrite-all, same idiom as nova_corrector.py's
    load_entries()/save_entries().

    Known, accepted concurrency limitation, stated plainly rather than
    hidden: tool_call_log.jsonl is actively appended to by this project's
    own tool-call-logging hook during any live Claude Code session
    (confirmed live — this very build's own tool calls are in the file).
    A rewrite-all write here could theoretically race a concurrent append
    and drop it. Accepted for a personal, single-user, low-frequency
    (human-triggered) write against a fast-append-but-rarely-truncated
    log — real file-locking or a move to sqlite is not justified for this
    risk profile. Matched by id, not position, so at worst a lost append
    is dropped once, never silently corrupted or misattributed to the
    wrong entry.
    """
    _check_escalation_token(x_nova_escalation_token)

    if kind == "tool_call":
        entries = _read_jsonl_file(TOOL_CALL_LOG_PATH)
        match_index = next((i for i, e in enumerate(entries) if e.get("tool_call_id") == entry_id), None)
        if match_index is None:
            raise HTTPException(status_code=404, detail=f"No tool_call entry '{entry_id}'")
        entries[match_index]["was_necessary"] = req.was_necessary
        entries[match_index]["was_used"] = req.was_used
        with open(TOOL_CALL_LOG_PATH, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return entries[match_index]

    elif kind == "blend_flag":
        try:
            _, index_str, expected_timestamp = entry_id.split(":", 2)
            index = int(index_str)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Malformed blend_flag id '{entry_id}'") from None
        entries = _read_jsonl_file(TRAINING_FLAGS_PATH)
        if index >= len(entries) or entries[index].get("timestamp") != expected_timestamp:
            raise HTTPException(
                status_code=409,
                detail="This entry's position/timestamp no longer matches -- training_flags.jsonl "
                "changed since this card was loaded. Reload the queue and try again.",
            )
        entries[index]["correction"] = req.correction or ""
        with open(TRAINING_FLAGS_PATH, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return entries[index]

    elif kind == "dpo_verify":
        try:
            _, index_str, expected_timestamp = entry_id.split(":", 2)
            index = int(index_str)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Malformed dpo_verify id '{entry_id}'") from None
        entries = _read_jsonl_file(TRAINING_FLAGS_PATH)
        if index >= len(entries) or entries[index].get("timestamp") != expected_timestamp:
            raise HTTPException(
                status_code=409,
                detail="This entry's position/timestamp no longer matches -- training_flags.jsonl "
                "changed since this card was loaded. Reload the queue and try again.",
            )
        if req.verification_status not in ("confirmed_good", "needs_rework"):
            raise HTTPException(
                status_code=422,
                detail="verification_status must be 'confirmed_good' or 'needs_rework'",
            )
        entries[index]["verification_status"] = req.verification_status
        with open(TRAINING_FLAGS_PATH, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return entries[index]

    else:
        raise HTTPException(status_code=422, detail="kind must be 'tool_call', 'blend_flag', or 'dpo_verify'")


# ── Training-data accumulation oversight (86bax4akx) ────────────

# Mirrors nova_finetune_phi4.MIN_REAL_PAIRS -- duplicated here rather than
# imported, so nova_api.py (the always-running production server) never
# depends on the training stack (datasets/unsloth/torch) being installed.
# If that constant changes, update this one too.
MIN_REAL_PAIRS_FOR_FINETUNE = 100


@app.get("/training-data-status")
def get_training_data_status():
    """
    Live DPO pair count, category coverage, and verification status --
    replaces 86baeyg1h's static "currently 11 pairs, keep accumulating"
    task-description line with a real number computed from
    training_flags.jsonl on every call (86bax4akx's live-count + coverage +
    threshold-alerting scope items). Tutor-domain and coding-domain
    coverage are deliberately not broken out -- neither has a real data
    source yet (Nova Tutor is unbuilt, coding DPO curation is blocked on
    86bara7pn) -- so "by_category" reflects nova_router.py's real
    categories (fiction, technical, etc.), not the task's aspirational
    lore/tutor/coding split.
    """
    entries = _read_jsonl_file(TRAINING_FLAGS_PATH)
    corrected = [e for e in entries if e.get("correction")]
    total_corrected = len(corrected)

    by_category = {}
    for e in corrected:
        category = e.get("category", "uncategorized")
        by_category[category] = by_category.get(category, 0) + 1

    verified_good = sum(1 for e in corrected if e.get("verification_status") == "confirmed_good")
    needs_rework = sum(1 for e in corrected if e.get("verification_status") == "needs_rework")

    return {
        "total_flagged": len(entries),
        "total_corrected": total_corrected,
        "min_pairs_for_finetune": MIN_REAL_PAIRS_FOR_FINETUNE,
        "pairs_remaining": max(0, MIN_REAL_PAIRS_FOR_FINETUNE - total_corrected),
        "progress_pct": round(100 * total_corrected / MIN_REAL_PAIRS_FOR_FINETUNE, 1),
        "threshold_met": total_corrected >= MIN_REAL_PAIRS_FOR_FINETUNE,
        "by_category": by_category,
        "verified_good": verified_good,
        "needs_rework": needs_rework,
        "unverified": total_corrected - verified_good - needs_rework,
    }


# ── Nova Log — Health dashboard ────────────────────────────────

# Health-view fields the spec calls for that have no real data source yet.
# Surfaced honestly with a reason each, rather than faked or silently omitted —
# see the Nova Log v1 plan for why each one is deferred.
NOVA_LOG_UNAVAILABLE_FIELDS = {
    "retrieval_hit_rate": "Not tracked yet — no ground-truth relevance labels to compare against.",
    "active_augments": "No augment/config-flag system exists in current Nova (single model, no toggles).",
    "orchestrator_failures_count": "nova_orchestrator.py exists (coding sub-agent) but doesn't track an aggregate failure count yet.",  # noqa: E501
}

# Resolved relative to this script's own location, not hardcoded — same
# GRAPH_PATH-class fix as ESCALATIONS_HTML_PATH above (2026-07-19): this
# route is served from the Omen (Linux) too, where a literal "C:/Nova/..."
# path never resolved.
NOVA_LOG_HTML_PATH = os.path.join(os.path.dirname(__file__), "nova_log.html")


@app.get("/nova-log/data")
def nova_log_data():
    """JSON data backing the /nova-log Health dashboard."""
    summary = compute_health_summary()
    latest_benchmarks = get_benchmark_runs(limit=1)
    summary["last_benchmark_run"] = latest_benchmarks[0]["timestamp"] if latest_benchmarks else None
    summary["not_yet_available"] = NOVA_LOG_UNAVAILABLE_FIELDS
    return summary


@app.get("/nova-log/queries")
def nova_log_queries(
    limit: int = Query(DEFAULT_RECENT_QUERIES_LIMIT, ge=1, le=1000),
    category: str | None = Query(None, description="Filter to an exact category match"),
    model: str | None = Query(None, description="Filter to an exact model match"),
    blend_detected: bool | None = Query(None, description="Filter to blend_detected true/false"),
):
    """
    Nova Log Query view — the last `limit` real queries (most recent first),
    optionally filtered by category, model, and/or blend_detected.
    """
    try:
        queries = get_recent_queries(
            limit=limit,
            category=category,
            model=model,
            blend_detected=blend_detected,
        )
        return {"queries": queries, "count": len(queries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/nova-log/benchmarks")
def nova_log_benchmarks(
    limit: int = Query(DEFAULT_BENCHMARK_RUNS_LIMIT, ge=1, le=1000),
    model: str | None = Query(None, description="Filter to an exact model match"),
):
    """
    Nova Log Benchmark view — the last `limit` golden-query benchmark runs
    from benchmark_log.jsonl (most recent first), optionally filtered by model.
    """
    try:
        runs = get_benchmark_runs(limit=limit, model=model)
        return {"runs": runs, "count": len(runs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/nova-log")
def nova_log_page():
    """Serve the Nova Log Health dashboard — static HTML/JS, fetches /nova-log/data."""
    return FileResponse(NOVA_LOG_HTML_PATH, media_type="text/html")


# ── Embedding-Space Visualization (86bawjg14) ───────────────────

# Same GRAPH_PATH-class fix as NOVA_LOG_HTML_PATH/ESCALATIONS_HTML_PATH
# above (2026-07-19) — resolved relative to this script's own location.
EMBEDDING_VIZ_HTML_PATH = os.path.join(os.path.dirname(__file__), "nova_embedding_viz.html")


@app.get("/embedding-viz/data")
def embedding_viz_data(
    query: str | None = Query(None, description="Query to highlight retrieval hits for"),
    refresh: bool = Query(False, description="Force a fresh t-SNE projection instead of the cached one"),
):
    """JSON data backing the /embedding-viz page — one point per Chroma chunk."""
    try:
        return build_embedding_viz_data(query=query, refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/embedding-viz")
def embedding_viz_page():
    """Serve the Embedding-Space Visualization page — static HTML/JS, fetches /embedding-viz/data."""
    return FileResponse(EMBEDDING_VIZ_HTML_PATH, media_type="text/html")
