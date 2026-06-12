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
import hmac, hashlib, secrets as pysecrets, smtplib, base64
import urllib.request, urllib.parse
from email.mime.text import MIMEText
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
# Owner console code — set BEACON_ADMIN_CODE to something different from the
# client access code so clients can never open the owner console.
ADMIN_CODE = os.environ.get("BEACON_ADMIN_CODE", "").strip() or ACCESS_CODE
RATE_LIMIT = int(os.environ.get("BEACON_RATE_LIMIT", "20"))
RATE_WINDOW = int(os.environ.get("BEACON_RATE_WINDOW", "600"))  # 10 min
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()

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


def _is_admin(request: Request) -> bool:
    return bool(ADMIN_CODE) and request.headers.get("x-beacon-admin", "") == ADMIN_CODE


def _guard(request: Request, rate_limited: bool = False):
    """Access-code + (optional) rate-limit check. Returns an error response or None."""
    if _is_admin(request):
        return None  # the owner is never gated or rate-limited
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


# --- contact verification (email + mobile OTP) -------------------------------
# Beacon generates its own one-time codes; delivery is pluggable:
#   EMAIL — set BEACON_SMTP_HOST / USER / PASS (a free Gmail app password works).
#   SMS   — set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM.
# Until a channel is configured, codes appear in the Owner Console so the
# owner can relay them personally — verification still works on day one.
SMTP_HOST = os.environ.get("BEACON_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("BEACON_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("BEACON_SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("BEACON_SMTP_PASS", "").strip()
SMTP_FROM = os.environ.get("BEACON_SMTP_FROM", "").strip() or SMTP_USER
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM", "").strip()

_SECRET_FILE = os.path.join(HERE, ".secret")


def _load_secret() -> bytes:
    env = os.environ.get("BEACON_SECRET", "").strip()
    if env:
        return env.encode()
    try:
        with open(_SECRET_FILE, "rb") as f:
            key = f.read()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = pysecrets.token_bytes(32)
    with open(_SECRET_FILE, "wb") as f:
        f.write(key)
    return key


SECRET = _load_secret()

_VERIF_FILE = os.path.join(HERE, "verifications.json")
VERIF: dict[str, dict] = {}
_verif_lock = threading.Lock()


def _load_verif() -> None:
    try:
        with open(_VERIF_FILE, encoding="utf-8") as f:
            VERIF.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_verif() -> None:
    items = sorted(VERIF.values(), key=lambda v: -v["created_at"])[:200]
    VERIF.clear()
    VERIF.update({v["email"]: v for v in items})
    tmp = _VERIF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(VERIF, f)
    os.replace(tmp, _VERIF_FILE)


_load_verif()


def _digits(p: str) -> str:
    return re.sub(r"\D", "", str(p))


def _phone_ok(p: str) -> bool:
    return 10 <= len(_digits(p)) <= 15


def _vtoken(email: str, phone: str) -> str:
    msg = (email.strip().lower() + "|" + _digits(phone)).encode()
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()[:40]


def _code_hash(code: str) -> str:
    return hashlib.sha256(SECRET + code.encode()).hexdigest()


def _send_email_code(to: str, code: str) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        return False
    try:
        m = MIMEText(f"Your Beacon verification code is {code}.\n"
                     "It expires in 10 minutes. If you didn't request it, ignore this email.")
        m["Subject"] = "Your Beacon verification code"
        m["From"], m["To"] = SMTP_FROM, to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(m)
        return True
    except Exception:
        return False


def _send_sms_code(phone: str, code: str) -> bool:
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        return False
    try:
        to = phone.strip() if phone.strip().startswith("+") else "+1" + _digits(phone)
        data = urllib.parse.urlencode({
            "To": to, "From": TWILIO_FROM,
            "Body": f"Beacon verification code: {code} (expires in 10 min)"}).encode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json", data=data)
        auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        req.add_header("Authorization", "Basic " + auth)
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception:
        return False


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


# --- usage quotas: the margin-protection engine -------------------------------
# Every generation has a real AI cost. This engine guarantees the platform can
# NEVER be generated into a loss:
#   * each client identity gets a monthly AI-cost budget derived from what they
#     pay, NET of card fees and per-client overhead, held to a minimum margin
#     (default 60% over cost — tune with BEACON_MIN_MARGIN);
#   * free users get a fixed acquisition budget (BEACON_FREE_AI_BUDGET);
#   * unit caps per plan stop any single client from draining the budget on
#     one item type. Whichever limit is tighter wins. Resets each calendar month.
MIN_MARGIN = float(os.environ.get("BEACON_MIN_MARGIN", "0.60"))
CARD_PCT = float(os.environ.get("BEACON_CARD_FEE_PCT", "0.029"))      # Stripe 2.9%
CARD_FIXED = float(os.environ.get("BEACON_CARD_FEE_FIXED", "0.30"))  # + 30c
OVERHEAD_PC = float(os.environ.get("BEACON_OVERHEAD_PER_CLIENT", "5.0"))  # software subs etc.
FREE_BUDGET = float(os.environ.get("BEACON_FREE_AI_BUDGET", "0.90"))

PLAN_ANCHORS = {"starter": 29, "growth": 79, "pro": 199}
_PLAN_UNITS = {  # hard monthly unit caps per plan (plan = strategy)
    "free":    {"plan": 2,  "creative": 3,  "image": 10},
    "starter": {"plan": 2,  "creative": 4,  "image": 10},
    "growth":  {"plan": 6,  "creative": 12, "image": 40},
    "pro":     {"plan": 30, "creative": 60, "image": 120},
}
_KIND_LABEL = {"plan": "campaign strategies", "creative": "ad creative sets", "image": "ad images"}


def _ai_budget(plan: str) -> float:
    """Max AI spend per month for this plan that still leaves >= MIN_MARGIN profit."""
    if plan not in PLAN_ANCHORS:
        return FREE_BUDGET
    price = _retail(PLAN_ANCHORS[plan])
    net = price * (1 - CARD_PCT) - CARD_FIXED - OVERHEAD_PC
    return max(net / (1 + MIN_MARGIN), FREE_BUDGET)


_USAGE_FILE = os.path.join(HERE, "usage.json")
USAGE: dict[str, dict] = {}
_usage_lock = threading.Lock()


def _load_usage() -> None:
    try:
        with open(_USAGE_FILE, encoding="utf-8") as f:
            USAGE.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_usage() -> None:
    tmp = _USAGE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(USAGE, f)
    os.replace(tmp, _USAGE_FILE)


_load_usage()


def _identity(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    return request.headers.get("x-forwarded-for", client).split(",")[0].strip()


def _usage_rec(ident: str) -> dict:
    """Get-or-create this identity's record for the current calendar month."""
    month = time.strftime("%Y-%m")
    u = USAGE.get(ident)
    if not u or u.get("month") != month:
        u = {"month": month, "spend": 0.0,
             "units": {"plan": 0, "creative": 0, "image": 0},
             "plan": (u or {}).get("plan", "free")}
        USAGE[ident] = u
    return u


def _quota_left(ident: str) -> dict:
    with _usage_lock:
        u = _usage_rec(ident)
        budget = _ai_budget(u["plan"])
        caps = _PLAN_UNITS.get(u["plan"], _PLAN_UNITS["free"])
        left = {}
        for kind, cost in _COST.items():
            by_budget = int(max(budget - u["spend"], 0.0) // cost)
            left[kind] = max(0, min(caps[kind] - u["units"][kind], by_budget))
        return {"plan": u["plan"], "left": left}


def _quota_charge(ident: str, kind: str, count: int) -> None:
    with _usage_lock:
        u = _usage_rec(ident)
        u["units"][kind] += count
        u["spend"] = round(u["spend"] + _COST[kind] * count, 6)
        _save_usage()


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

    # HARD RULE: nothing launches unless every piece of the campaign the client
    # received has been explicitly APPROVED. The UI sends the approval ids it
    # holds; we verify each one server-side so the gate cannot be bypassed.
    if cap == Capability.PUBLISH:
        ids = [v for v in (body.payload.get("approval_ids") or {}).values() if v]
        with _appr_lock:
            unapproved = [a for a in ids if APPROVALS.get(a, {}).get("status") != "approved"]
        if not ids or unapproved:
            return {"ok": False, "data": {}, "gate_required": "", "notes": [],
                    "approval_id": "",
                    "blocked_reason": ("Your campaign content must be approved before "
                                       "launch. Please review and approve your Blueprint, "
                                       "Storyboard and Gallery first — only approved "
                                       "content can go live.")}
        # HARD RULE 2: launches require a VERIFIED email and mobile number.
        contact = body.payload.get("contact") or {}
        email = str(contact.get("email", "")).strip().lower()
        phone = str(contact.get("phone", ""))
        token = str(body.payload.get("verify_token", ""))
        if not _EMAIL.match(email) or not _phone_ok(phone):
            return {"ok": False, "data": {}, "gate_required": "", "notes": [],
                    "approval_id": "",
                    "blocked_reason": ("A valid email address and mobile number are "
                                       "required to launch your campaign.")}
        if not (token and hmac.compare_digest(token, _vtoken(email, phone))):
            return {"ok": False, "data": {}, "gate_required": "", "notes": [],
                    "approval_id": "", "verify_required": True,
                    "blocked_reason": ("Please verify your email and mobile number "
                                       "before launching — it only takes a minute.")}

    # USAGE QUOTA: every generation costs real money, so each identity gets a
    # monthly allowance that mathematically preserves the platform's margin.
    _BILLABLE = {Capability.PLAN_CAMPAIGN: "plan", Capability.MAKE_CREATIVE: "creative",
                 Capability.GENERATE_MEDIA: "image"}
    kind = _BILLABLE.get(cap)
    qty = 1
    ident = _identity(request)
    if kind and not _is_admin(request):
        if kind == "image":
            qty = max(1, min(4, int(body.payload.get("count", 1) or 1)))
        q = _quota_left(ident)
        if q["left"][kind] < qty:
            return {"ok": False, "data": {}, "gate_required": "", "notes": [],
                    "approval_id": "", "quota_exceeded": True,
                    "blocked_reason": ("You've used this month's included "
                                       + _KIND_LABEL[kind] + " on your current plan. "
                                       "Upgrade your plan for a bigger monthly allowance — "
                                       "or your allowance refreshes on the 1st.")}

    resp = ORCH.route(AgentRequest(cap, body.payload, requester=body.requester))

    # billable usage feeds the demand-based pricing engine and the quota ledger
    if resp.ok and kind:
        _record_demand()
        if not _is_admin(request):
            _quota_charge(ident, kind, qty)

    # park client-gated artifacts in the approval queue so a human can decide
    approval_id = ""
    if resp.ok and resp.gate_required == "client":
        if cap == Capability.PLAN_CAMPAIGN:
            label = f"Campaign strategy — {body.payload.get('firm', {}).get('name', 'campaign')}"
        elif cap == Capability.MAKE_CREATIVE:
            label = f"Ad creative — {body.payload.get('vertical', 'general')}"
        elif cap == Capability.GENERATE_MEDIA:
            label = f"Ad images — {str(body.payload.get('prompt', ''))[:70]}"
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
    phone: str = ""
    business: str = ""
    note: str = ""


@app.post("/leads")
def create_lead(body: LeadIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err
    if not _EMAIL.match(body.email.strip()):
        return JSONResponse({"ok": False, "error": "valid email required"}, status_code=400)
    if not _phone_ok(body.phone):
        return JSONResponse({"ok": False, "error": "valid mobile number required"}, status_code=400)
    lead = {"id": uuid.uuid4().hex[:10], "kind": body.kind, "plan": body.plan,
            "email": body.email.strip(), "name": body.name.strip(),
            "phone": _digits(body.phone), "business": body.business.strip(),
            "note": body.note.strip(), "created_at": time.time()}
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


# --- contact verification endpoints -------------------------------------------
class VerifyStartIn(BaseModel):
    email: str
    phone: str
    name: str = ""


class VerifyCheckIn(BaseModel):
    email: str
    phone: str
    email_code: str = ""
    sms_code: str = ""


@app.post("/verify/start")
def verify_start(body: VerifyStartIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        return JSONResponse({"ok": False, "error": "valid email required"}, status_code=400)
    if not _phone_ok(body.phone):
        return JSONResponse({"ok": False, "error": "valid mobile number required"}, status_code=400)
    now = time.time()
    with _verif_lock:
        prev = VERIF.get(email)
        if prev and not prev.get("verified") and now - prev.get("sent_at", 0) < 60:
            return {"ok": False, "error": "Codes were just sent — please wait a minute "
                                          "before requesting new ones."}
        ecode = f"{pysecrets.randbelow(10**6):06d}"
        scode = f"{pysecrets.randbelow(10**6):06d}"
        e_sent = _send_email_code(email, ecode)
        s_sent = _send_sms_code(body.phone, scode)
        rec = {"email": email, "phone": _digits(body.phone), "name": body.name.strip(),
               "ehash": _code_hash(ecode), "shash": _code_hash(scode),
               "email_delivery": "sent" if e_sent else "manual",
               "sms_delivery": "sent" if s_sent else "manual",
               "expires": now + 600, "attempts": 0, "verified": False,
               "sent_at": now, "created_at": now,
               # plaintext kept ONLY for channels the owner must relay by hand
               "relay": {**({} if e_sent else {"email": ecode}),
                         **({} if s_sent else {"sms": scode})}}
        VERIF[email] = rec
        _save_verif()
    return {"ok": True, "email_delivery": rec["email_delivery"],
            "sms_delivery": rec["sms_delivery"], "expires_in": 600}


@app.post("/verify/check")
def verify_check(body: VerifyCheckIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err
    email = body.email.strip().lower()
    with _verif_lock:
        rec = VERIF.get(email)
        if not rec or rec.get("phone") != _digits(body.phone):
            return {"ok": False, "error": "Please request verification codes first."}
        if rec.get("verified"):
            return {"ok": True, "verified": True, "verify_token": _vtoken(email, body.phone)}
        if time.time() > rec["expires"]:
            return {"ok": False, "error": "Your codes expired — please request new ones."}
        if rec["attempts"] >= 8:
            return {"ok": False, "error": "Too many attempts — please request new codes."}
        rec["attempts"] += 1
        e_ok = hmac.compare_digest(_code_hash(body.email_code.strip()), rec["ehash"])
        s_ok = hmac.compare_digest(_code_hash(body.sms_code.strip()), rec["shash"])
        if not (e_ok and s_ok):
            _save_verif()
            which = [] if e_ok else ["email code"]
            which += [] if s_ok else ["text code"]
            return {"ok": False, "error": "The " + " and ".join(which) + " didn't match — "
                                          "please double-check and try again."}
        rec["verified"] = True
        rec["relay"] = {}
        _save_verif()
    return {"ok": True, "verified": True, "verify_token": _vtoken(email, body.phone)}


@app.get("/quota")
def quota(request: Request):
    err = _guard(request)
    if err:
        return err
    q = _quota_left(_identity(request))
    return {"ok": True, "plan": q["plan"], "remaining": q["left"],
            "note": "Allowances refresh on the 1st of each month."}


@app.get("/pricing")
def pricing():
    """Client-facing price list. Retail numbers only — cost basis, markup, and
    the demand factor are internal. Rates refresh on every call."""
    launch = _retail(12, _BUNDLE_COST)
    plans = [
        {"key": "starter", "label": "Starter", "price": _retail(29),
         "cadence": "per month", "popular": False,
         "tagline": "Get on the map",
         "features": ["2 campaigns per month", "10 custom ad images", "1 advertising channel",
                      "Campaign approval workflow", "Email support"]},
        {"key": "growth", "label": "Growth", "price": _retail(79),
         "cadence": "per month", "popular": True,
         "tagline": "Our most popular plan",
         "features": ["6 campaigns per month", "40 custom ad images", "Video ad concepts",
                      "Up to 3 advertising channels", "A/B creative variants",
                      "Priority support"]},
        {"key": "pro", "label": "Pro", "price": _retail(199),
         "cadence": "per month", "popular": False,
         "tagline": "For multi-location & franchises",
         "features": ["Unlimited campaigns (fair use)", "120 custom ad images",
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


# --- real card / Google Pay / Apple Pay checkout (Stripe) ----------------------
# Activates the moment STRIPE_SECRET_KEY is set; until then the UI falls back
# to the reservation flow. Prices are computed SERVER-side from the plan key —
# never trusted from the client.
class CheckoutIn(BaseModel):
    plan: str
    email: str = ""


@app.post("/checkout")
def checkout(body: CheckoutIn, request: Request):
    err = _guard(request, rate_limited=True)
    if err:
        return err
    if body.plan not in PLAN_ANCHORS:
        return JSONResponse({"ok": False, "error": "unknown plan"}, status_code=400)
    if not STRIPE_KEY:
        return {"ok": False, "error": "payments_not_configured"}
    try:
        import stripe
        stripe.api_key = STRIPE_KEY
        price = _retail(PLAN_ANCHORS[body.plan])
        base = str(request.base_url).rstrip("/")
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"quantity": 1, "price_data": {
                "currency": "usd",
                "product_data": {"name": f"Beacon {body.plan.capitalize()} plan"},
                "unit_amount": int(round(price * 100)),
                "recurring": {"interval": "month"}}}],
            customer_email=body.email or None,
            success_url=base + "/?welcome=1",
            cancel_url=base + "/?canceled=1")
        return {"ok": True, "url": session.url}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# --- owner console -------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    with open(os.path.join(HERE, "admin.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/admin/data")
def admin_data(request: Request):
    if ADMIN_CODE and not _is_admin(request):
        return JSONResponse({"ok": False, "error": "owner code required"}, status_code=401)
    try:
        with open(_LEADS_FILE, encoding="utf-8") as f:
            leads = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        leads = []
    with _appr_lock:
        approvals = sorted(APPROVALS.values(), key=lambda a: -a["created_at"])
    now = time.time()
    with _verif_lock:
        verifs = [{"email": v["email"], "phone": v["phone"], "name": v.get("name", ""),
                   "verified": v["verified"], "relay": v.get("relay", {}),
                   "email_delivery": v.get("email_delivery", ""),
                   "sms_delivery": v.get("sms_delivery", ""),
                   "expired": now > v.get("expires", 0), "created_at": v["created_at"]}
                  for v in sorted(VERIF.values(), key=lambda v: -v["created_at"])[:50]]
    with _usage_lock:
        month = time.strftime("%Y-%m")
        usage = [{"identity": k, "plan": u["plan"], "units": u["units"],
                  "spend": round(u.get("spend", 0.0), 4)}
                 for k, u in USAGE.items() if u.get("month") == month]
        usage.sort(key=lambda u: -u["spend"])
    stats = {
        "demand_factor": round(_demand_factor(), 3),
        "billable_actions_24h": len(_demand_events),
        "pending_approvals": sum(1 for a in approvals if a["status"] == "pending"
                                 and a["kind"] != "launch_request"),
        "launch_requests_pending": sum(1 for a in approvals if a["status"] == "pending"
                                       and a["kind"] == "launch_request"),
        "leads_total": len(leads),
        "payments_configured": bool(STRIPE_KEY),
        "otp_email_configured": bool(SMTP_HOST and SMTP_USER and SMTP_PASS),
        "otp_sms_configured": bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM),
        "ai_spend_month": round(sum(u["spend"] for u in usage), 4),
    }
    return {"ok": True, "stats": stats, "leads": leads, "approvals": approvals,
            "verifications": verifs, "usage": usage[:50]}


class GrantIn(BaseModel):
    identity: str
    plan: str


@app.post("/admin/grant")
def admin_grant(body: GrantIn, request: Request):
    """Owner assigns a plan to a client identity (after they subscribe/pay)."""
    if ADMIN_CODE and not _is_admin(request):
        return JSONResponse({"ok": False, "error": "owner code required"}, status_code=401)
    if body.plan not in ("free", *PLAN_ANCHORS):
        return JSONResponse({"ok": False, "error": "unknown plan"}, status_code=400)
    with _usage_lock:
        u = _usage_rec(body.identity.strip())
        u["plan"] = body.plan
        _save_usage()
    return {"ok": True, "identity": body.identity.strip(), "plan": body.plan}
