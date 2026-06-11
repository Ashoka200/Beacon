"""
server.py — Beacon: the governed AI marketing platform, as an HTTP service.

    pip install -r requirements.txt
    uvicorn server:app --port 8000

Serves the Beacon web UI at "/" and the orchestration API at "/route".
Gates stay in DRY_RUN, so nothing destructive runs unattended.
"""
import os, sys, time, threading
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
# ACCESS_CODE: if set, /route requires header  X-Beacon-Access: <code>.
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


@app.get("/healthz")
def healthz():
    return {"ok": True, "dry_run": gates.DRY_RUN, "auth_required": bool(ACCESS_CODE)}


@app.post("/route")
def route(body: RouteIn, request: Request):
    # 1. access gate — blocks anonymous use of your paid AI when deployed
    if ACCESS_CODE:
        supplied = request.headers.get("x-beacon-access", "")
        if supplied != ACCESS_CODE:
            return JSONResponse(
                {"ok": False, "blocked_reason": "Access code required or incorrect."},
                status_code=401)

    # 2. rate limit — caps how fast any one visitor can spend tokens
    client = request.client.host if request.client else "unknown"
    ip = request.headers.get("x-forwarded-for", client).split(",")[0].strip()
    if not _rate_ok(ip):
        return JSONResponse(
            {"ok": False, "blocked_reason": "Too many requests — please wait a few minutes."},
            status_code=429)

    try:
        cap = Capability(body.capability)
    except ValueError:
        return {"ok": False, "error": f"unknown capability '{body.capability}'",
                "valid": [c.value for c in Capability]}
    resp = ORCH.route(AgentRequest(cap, body.payload, requester=body.requester))
    return {"ok": resp.ok, "data": resp.data, "gate_required": resp.gate_required,
            "blocked_reason": resp.blocked_reason, "notes": resp.notes}


@app.get("/pending")
def pending():
    return {"pending_approvals": gates.pending()}
