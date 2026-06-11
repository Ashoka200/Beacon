"""
server.py — Beacon: the governed AI marketing platform, as an HTTP service.

    pip install -r requirements.txt
    uvicorn server:app --port 8000

Serves the Beacon web UI at "/", the orchestration API at "/route",
the client approval workflow at "/approvals", and transparent
cost-plus pricing at "/pricing".
Gates stay in DRY_RUN, so nothing destructive runs unattended.
"""
import os, sys, time, math, threading, uuid
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "agents"))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from contracts import AgentRequest, Capability
from run_campaign import build_fleet
import gates

# --- public-launch guards (configurable via .env) ---------------------------
# ACCESS_CODE: if set, API endpoints require header  X-Beacon-Access: <code>.
#   Leave empty for local dev; ALWAYS set it in production.
# RATE_LIMIT / RATE_WINDOW: max requests per IP per window (seconds).
ACCESS_CODE = os.environ.get("BEACON_ACCESS_CODE", "").strip()
RATE_LIMIT = int(os.environ.get("BEACON_RATE_LIMIT", "20"))
RATE_WINDOW = int(os.environ.get("BEACON_RATE_WINDOW", "600"))  # 10 min

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def _rate_ok(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        bucket = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
        if len(bucket) >= RATE_LIMIT:
            _hits[ip] = bucket
            return False
        bucket.append(now)
        _hits[ip] = bucket
        return True


def _guard(request: Request, rate_limited: bool = False):
    """Access-code + (optional) rate-limit check. Returns an error response or None."""
    if ACCESS_CODE:
        if request.headers.get("x-beacon-access", "") != ACCESS_CODE:
            return JSONResponse(
                {"ok": False, "blocked_reason": "Access code required or incorrect."},
                status_code=401)
    if rate_limited:
        client = request.client.host if request.client else "unknown"
        ip = request.headers.get("x-forwarded-for", client).split(",")[0].strip()
        if not _rate_ok(ip):
            return JSONResponse(
                {"ok": False, "blocked_reason": "Too many requests — please wait a few minutes."},
                status_code=429)
    return None


# --- client approval workflow ------------------------------------------------
# Every client-gated artifact (creative concepts, rendered media) is parked
# here until an accountable human approves or rejects it. In-memory store —
# swap for a database when client accounts land.
APPROVALS: dict[str, dict] = {}
_appr_lock = threading.Lock()


def _create_approval(kind: str, label: str, data: dict) -> str:
    aid = uuid.uuid4().hex[:10]
    with _appr_lock:
        APPROVALS[aid] = {"id": aid, "kind": kind, "label": label, "data": data,
                          "status": "pending", "decided_by": "", "created_at": time.time()}
    return aid


# --- transparent cost-plus pricing -------------------------------------------
# Beacon's promise: you pay our actual AI cost plus a flat markup. Costs below
# are the real per-action compute costs (Claude Opus 4.8 token pricing and
# Gemini image pricing); MARKUP applies on top. Tune via env without a deploy.
MARKUP = float(os.environ.get("BEACON_MARKUP", "1.40"))  # cost + 40%

# (input_tokens, output_tokens) typical per action; Opus 4.8: $5/M in, $25/M out
_OPUS_IN, _OPUS_OUT = 5.0 / 1e6, 25.0 / 1e6
_IMAGE_COST = 0.039  # Gemini 2.5 Flash Image, per image

COST_BASIS = {
    "campaign_plan":  {"label": "Campaign strategy & plan",
                       "cost": round(3000 * _OPUS_IN + 6000 * _OPUS_OUT, 3),
                       "unit": "per campaign",
                       "desc": "Full strategy: audience, channels, budget split, keywords."},
    "ad_creative":    {"label": "Ad creative concepts (2 variants)",
                       "cost": round(2000 * _OPUS_IN + 4000 * _OPUS_OUT, 3),
                       "unit": "per brief",
                       "desc": "Scripts, hooks, and angles for your ads — held for your approval."},
    "ad_image":       {"label": "AI ad image",
                       "cost": _IMAGE_COST,
                       "unit": "per image",
                       "desc": "Photorealistic ad image, AI-disclosure attached."},
}


def _price(cost: float) -> float:
    return math.ceil(cost * MARKUP * 100) / 100  # round up to the cent


app = FastAPI(title="Beacon", description="Get seen. Stay trusted.")
ORCH, REG, FLY = build_fleet()


@app.get("/", response_class=HTMLResponse)
def home():
    with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as f:
        return f.read()


class RouteIn(BaseModel):
    capability: str
    payload: dict = {}
    requester: str = "human"


class DecisionIn(BaseModel):
    decision: str           # "approve" | "reject"
    by: str = "client"


@app.get("/healthz")
def healthz():
    return {"ok": True, "dry_run": gates.DRY_RUN, "auth_required": bool(ACCESS_CODE)}


@app.post("/route")
def route(body: RouteIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err

    try:
        cap = Capability(body.capability)
    except ValueError:
        return {"ok": False, "error": f"unknown capability '{body.capability}'",
                "valid": [c.value for c in Capability]}
    resp = ORCH.route(AgentRequest(cap, body.payload, requester=body.requester))

    # park client-gated artifacts in the approval queue so a human can decide
    approval_id = ""
    if resp.ok and resp.gate_required == "client":
        if cap == Capability.MAKE_CREATIVE:
            label = f"Ad creative — {body.payload.get('vertical', 'general')}"
        elif cap == Capability.GENERATE_MEDIA:
            label = f"AI media — {str(body.payload.get('prompt', ''))[:70]}"
        else:
            label = cap.value
        approval_id = _create_approval(cap.value, label, resp.data)

    return {"ok": resp.ok, "data": resp.data, "gate_required": resp.gate_required,
            "blocked_reason": resp.blocked_reason, "notes": resp.notes,
            "approval_id": approval_id}


@app.get("/approvals")
def list_approvals(request: Request):
    err = _guard(request)
    if err:
        return err
    with _appr_lock:
        items = sorted(APPROVALS.values(), key=lambda a: -a["created_at"])
    return {"ok": True, "approvals": items}


@app.post("/approvals/{aid}")
def decide(aid: str, body: DecisionIn, request: Request):
    err = _guard(request)
    if err:
        return err
    if body.decision not in ("approve", "reject"):
        return JSONResponse({"ok": False, "error": "decision must be 'approve' or 'reject'"},
                            status_code=400)
    with _appr_lock:
        item = APPROVALS.get(aid)
        if not item:
            return JSONResponse({"ok": False, "error": "approval not found"}, status_code=404)
        if item["status"] != "pending":
            return {"ok": True, "approval": item, "note": "already decided"}
        item["status"] = "approved" if body.decision == "approve" else "rejected"
        item["decided_by"] = body.by
        item["decided_at"] = time.time()
    return {"ok": True, "approval": item}


@app.get("/pricing")
def pricing():
    items = []
    for key, c in COST_BASIS.items():
        items.append({"key": key, "label": c["label"], "desc": c["desc"],
                      "unit": c["unit"], "our_cost": c["cost"], "price": _price(c["cost"])})
    bundle_cost = (COST_BASIS["campaign_plan"]["cost"]
                   + COST_BASIS["ad_creative"]["cost"]
                   + 4 * COST_BASIS["ad_image"]["cost"])
    bundle = {"key": "launch_bundle", "label": "Campaign Launch Bundle",
              "desc": "Strategy + creative concepts + 4 ad images. Everything to launch one campaign.",
              "unit": "per campaign", "our_cost": round(bundle_cost, 3),
              "price": _price(bundle_cost)}
    return {"ok": True, "model": "transparent cost-plus",
            "markup_percent": round((MARKUP - 1) * 100),
            "note": ("You pay our actual AI compute cost plus a flat "
                     f"{round((MARKUP - 1) * 100)}% — that's it. Ad spend (Google/Meta) "
                     "is billed by the platforms directly at cost, never marked up."),
            "items": items, "bundle": bundle}


@app.get("/pending")
def pending():
    return {"pending_approvals": gates.pending()}
