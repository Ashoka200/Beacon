"""
server.py — Beacon: the governed AI marketing platform, as an HTTP service.

    pip install -r requirements.txt
    uvicorn server:app --port 8000

Serves the Beacon web UI at "/", the orchestration API at "/route",
the client approval workflow at "/approvals", and transparent
cost-plus pricing at "/pricing".
Gates stay in DRY_RUN, so nothing destructive runs unattended.
"""
import os, sys, time, math, threading, uuid, json, re
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
# Every client-gated artifact (creative concepts, rendered media) and every
# launch request is parked here until an accountable human decides. Persisted
# to disk so decisions survive restarts; swap for a database at scale.
_APPR_FILE = os.path.join(HERE, "approvals.json")
APPROVALS: dict[str, dict] = {}
_appr_lock = threading.Lock()


def _load_approvals() -> None:
    try:
        with open(_APPR_FILE, encoding="utf-8") as f:
            APPROVALS.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_approvals() -> None:
    # keep the newest 100 to bound file size (image payloads are large)
    items = sorted(APPROVALS.values(), key=lambda a: -a["created_at"])[:100]
    APPROVALS.clear()
    APPROVALS.update({a["id"]: a for a in items})
    tmp = _APPR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(APPROVALS, f)
    os.replace(tmp, _APPR_FILE)


_load_approvals()


def _create_approval(kind: str, label: str, data: dict) -> str:
    aid = uuid.uuid4().hex[:10]
    with _appr_lock:
        APPROVALS[aid] = {"id": aid, "kind": kind, "label": label, "data": data,
                          "status": "pending", "decided_by": "", "created_at": time.time()}
        _save_approvals()
    return aid


# --- plan reservations / leads ------------------------------------------------
_LEADS_FILE = os.path.join(HERE, "leads.json")
_leads_lock = threading.Lock()
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _save_lead(lead: dict) -> None:
    with _leads_lock:
        try:
            with open(_LEADS_FILE, encoding="utf-8") as f:
                leads = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            leads = []
        leads.append(lead)
        tmp = _LEADS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=1)
        os.replace(tmp, _LEADS_FILE)


# --- pricing engine (all internals stay server-side) -------------------------
# Clients only ever see clean retail prices. Internally every price has:
#   1. a COST FLOOR  — real AI compute cost x MARKUP (default +40%), never sell below;
#   2. a MARKET ANCHOR — value-based plan pricing typical for SMB marketing tools;
#   3. a DEMAND FACTOR — live multiplier from the last 24h of billable usage, so
#      rates rise automatically when the platform is busy and ease when quiet.
MARKUP = float(os.environ.get("BEACON_MARKUP", "1.40"))                # internal floor only
DEMAND_BASELINE = float(os.environ.get("BEACON_DEMAND_BASELINE", "40"))  # billable actions/day = normal

_OPUS_IN, _OPUS_OUT = 5.0 / 1e6, 25.0 / 1e6   # Claude Opus 4.8 $/token
_IMG_COST = 0.039                              # Gemini image $/render
_COST = {  # internal AI cost basis — never exposed via the API
    "plan":     3000 * _OPUS_IN + 6000 * _OPUS_OUT,
    "creative": 2000 * _OPUS_IN + 4000 * _OPUS_OUT,
    "image":    _IMG_COST,
}
_BUNDLE_COST = _COST["plan"] + _COST["creative"] + 4 * _COST["image"]

_demand_events: list[float] = []
_demand_lock = threading.Lock()


def _record_demand() -> None:
    with _demand_lock:
        _demand_events.append(time.time())
        if len(_demand_events) > 5000:
            del _demand_events[:2500]


def _demand_factor() -> float:
    """0.90 (quiet) .. 1.40 (very busy), from billable actions in the last 24h."""
    now = time.time()
    with _demand_lock:
        _demand_events[:] = [t for t in _demand_events if now - t < 86400]
        load = len(_demand_events) / max(DEMAND_BASELINE, 1.0)
    return max(0.90, min(1.40, 0.90 + 0.25 * load))


def _pretty(p: float) -> float:
    """Attractive retail endings: $0.95-style under a dollar, $X.99 above."""
    if p < 1:
        return math.ceil(p * 20) / 20
    return math.ceil(p) - 0.01


def _retail(anchor: float, cost: float = 0.0) -> float:
    """Demand-scaled market price, floored at cost x MARKUP."""
    return _pretty(max(anchor * _demand_factor(), cost * MARKUP))


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

    # billable usage feeds the demand-based pricing engine
    if resp.ok and cap in (Capability.PLAN_CAMPAIGN, Capability.MAKE_CREATIVE,
                           Capability.GENERATE_MEDIA):
        _record_demand()

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

    # capture launch requests: publish is human-gated, so record the ask for
    # the launch team and give the client a trackable ticket
    if cap == Capability.PUBLISH and resp.gate_required == "human":
        biz = str(body.payload.get("business", "campaign"))
        approval_id = _create_approval("launch_request",
                                       f"Launch — {biz} ({body.payload.get('channel', 'ads')})",
                                       {k: v for k, v in body.payload.items()
                                        if k != "creative_assets"})

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
        _save_approvals()
    return {"ok": True, "approval": item}


class LeadIn(BaseModel):
    kind: str = "plan_reservation"
    plan: str = ""
    email: str
    name: str = ""
    business: str = ""
    note: str = ""


@app.post("/leads")
def create_lead(body: LeadIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err
    if not _EMAIL.match(body.email.strip()):
        return JSONResponse({"ok": False, "error": "valid email required"}, status_code=400)
    lead = {"id": uuid.uuid4().hex[:10], "kind": body.kind, "plan": body.plan,
            "email": body.email.strip(), "name": body.name.strip(),
            "business": body.business.strip(), "note": body.note.strip(),
            "created_at": time.time()}
    _save_lead(lead)
    return {"ok": True, "lead_id": lead["id"]}


@app.get("/leads")
def list_leads(request: Request):
    err = _guard(request)
    if err:
        return err
    try:
        with open(_LEADS_FILE, encoding="utf-8") as f:
            return {"ok": True, "leads": json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ok": True, "leads": []}


@app.get("/pricing")
def pricing():
    """Client-facing price list. Retail numbers only — cost basis, markup, and
    the demand factor are internal. Rates refresh on every call."""
    launch = _retail(12, _BUNDLE_COST)
    plans = [
        {"key": "starter", "label": "Starter", "price": _retail(29),
         "cadence": "per month", "popular": False,
         "tagline": "Get on the map",
         "features": ["2 AI campaigns per month", "10 ad images", "1 advertising channel",
                      "Campaign approval workflow", "Email support"]},
        {"key": "growth", "label": "Growth", "price": _retail(79),
         "cadence": "per month", "popular": True,
         "tagline": "Our most popular plan",
         "features": ["6 AI campaigns per month", "40 ad images", "Video ad concepts",
                      "Up to 3 advertising channels", "A/B creative variants",
                      "Priority support"]},
        {"key": "pro", "label": "Pro", "price": _retail(199),
         "cadence": "per month", "popular": False,
         "tagline": "For multi-location & franchises",
         "features": ["Unlimited campaigns (fair use)", "120 ad images",
                      "All advertising channels", "Multi-location support",
                      "Quarterly strategy review", "Priority support"]},
    ]
    bundles = [
        {"key": "launch", "label": "Campaign Launch Bundle", "price": launch,
         "unit": "one-time",
         "desc": "Strategy + ad creative + 4 ad images. Everything to launch one campaign."},
        {"key": "content", "label": "Content Pack", "price": _retail(15, 12 * _COST["image"]),
         "unit": "one-time", "desc": "12 fresh ad images for your channels."},
        {"key": "boost", "label": "Brand Boost", "price": _retail(25, _COST["plan"] + 2 * _COST["creative"] + 8 * _COST["image"]),
         "unit": "one-time", "desc": "Strategy + 2 creative briefs + 8 ad images."},
    ]
    intro = {"key": "first_campaign_free", "label": "Your first campaign is free",
             "desc": ("New to Beacon? Your first campaign is on us — "
                      "see the full strategy, ad creative, and images before you spend a dollar."),
             "price": 0.0, "regular": launch}
    # per-item retail so clients can buy exactly what they need
    alacarte = {"strategy": _retail(5, _COST["plan"]),
                "creative": _retail(4, _COST["creative"]),
                "image": _retail(1.5, _COST["image"])}
    return {"ok": True, "currency": "USD",
            "rates_note": "Rates adjust automatically with platform demand.",
            "intro": intro, "plans": plans, "bundles": bundles, "alacarte": alacarte}


@app.get("/pending")
def pending():
    return {"pending_approvals": gates.pending()}
