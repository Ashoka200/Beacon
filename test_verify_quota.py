"""End-to-end test: OTP verification + quota engine + verified-launch gate."""
import json, urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, body, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


passed, failed = 0, 0
def check(name, cond):
    global passed, failed
    print(("PASS" if cond else "FAIL") + " - " + name)
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


# 1. quota starts at free-plan limits
q = get("/quota")
check("quota: free plan", q["ok"] and q["plan"] == "free")
check("quota: has remaining counts", all(k in q["remaining"] for k in ("plan", "creative", "image")))
print("   remaining:", q["remaining"])

# 2. verify/start — manual mode (no SMTP/Twilio locally)
v = post("/verify/start", {"email": "client@example.com", "phone": "7025550100", "name": "Test Client"})
check("verify/start ok", v.get("ok") is True)
check("verify/start manual email", v.get("email_delivery") == "manual")
check("verify/start manual sms", v.get("sms_delivery") == "manual")

# 3. bad email / bad phone rejected
check("verify/start rejects bad email", post("/verify/start", {"email": "nope", "phone": "7025550100"}).get("ok") is False)
check("verify/start rejects bad phone", post("/verify/start", {"email": "x@y.com", "phone": "123"}).get("ok") is False)

# 4. owner reads relay codes from admin console
ad = get("/admin/data")
rec = next(x for x in ad["verifications"] if x["email"] == "client@example.com")
codes = rec["relay"]
check("admin sees relay codes", "email" in codes and "sms" in codes)

# 5. wrong codes fail, right codes verify
bad = post("/verify/check", {"email": "client@example.com", "phone": "7025550100",
                             "email_code": "000000", "sms_code": "000000"})
check("verify/check rejects wrong codes", bad.get("ok") is False)
good = post("/verify/check", {"email": "client@example.com", "phone": "7025550100",
                              "email_code": codes["email"], "sms_code": codes["sms"]})
check("verify/check accepts right codes", good.get("ok") is True and good.get("verify_token"))
token = good.get("verify_token", "")

# 6. generate a strategy and approve it (needed for launch)
plan = post("/route", {"capability": "plan_campaign", "requester": "human",
                       "payload": {"firm": {"vertical": "hospitality", "name": "Test Hotel", "city": "Henderson"},
                                   "goal": "bookings", "budget_monthly_usd": 1000}})
check("strategy generated", plan.get("ok") is True and plan.get("approval_id"))
aid = plan.get("approval_id", "")
appr = post("/approvals/" + aid, {"decision": "approve", "by": "client"})
check("strategy approved", appr.get("ok") is True)

# 7. launch WITHOUT verification token -> blocked
pub_payload = {"jurisdiction": "US", "channel": "google_search", "business": "Test Hotel",
               "contact": {"name": "Test", "email": "client@example.com", "phone": "7025550100"},
               "approval_ids": {"plan": aid}}
blocked = post("/route", {"capability": "publish", "requester": "human", "payload": dict(pub_payload)})
check("launch blocked without verify token", blocked.get("ok") is False and blocked.get("verify_required") is True)

# 8. launch with FAKE token -> blocked
fake = post("/route", {"capability": "publish", "requester": "human",
                       "payload": {**pub_payload, "verify_token": "f" * 40}})
check("launch blocked with fake token", fake.get("ok") is False)

# 9. launch with REAL token -> accepted as launch request
ok = post("/route", {"capability": "publish", "requester": "human",
                     "payload": {**pub_payload, "verify_token": token}})
check("launch accepted with verified contact", bool(ok.get("approval_id")))

# 10. missing phone -> blocked
nophone = post("/route", {"capability": "publish", "requester": "human",
                          "payload": {**pub_payload, "verify_token": token,
                                      "contact": {"name": "T", "email": "client@example.com", "phone": ""}}})
check("launch blocked without phone", nophone.get("ok") is False)

# 11. quota: free plan allows 2 strategies/month; we used 1 — burn 1 more then expect a block
plan2 = post("/route", {"capability": "plan_campaign", "requester": "human",
                        "payload": {"firm": {"vertical": "dentist", "name": "B", "city": "C"}, "goal": "calls"}})
check("second strategy allowed", plan2.get("ok") is True)
plan3 = post("/route", {"capability": "plan_campaign", "requester": "human",
                        "payload": {"firm": {"vertical": "salon", "name": "D", "city": "E"}, "goal": "calls"}})
check("third strategy blocked by quota", plan3.get("ok") is False and plan3.get("quota_exceeded") is True)
print("   block message:", plan3.get("blocked_reason", "")[:90])

# 12. usage ledger shows in admin
ad2 = get("/admin/data")
check("admin shows usage", len(ad2.get("usage", [])) >= 1)
check("admin shows month AI cost", ad2["stats"].get("ai_spend_month", 0) > 0)

# 13. owner grants growth plan -> quota expands
ident = ad2["usage"][0]["identity"]
g = post("/admin/grant", {"identity": ident, "plan": "growth"})
check("grant growth plan", g.get("ok") is True)
q2 = get("/quota")
check("quota expands after grant", q2["plan"] == "growth" and q2["remaining"]["plan"] >= 4)
print("   growth remaining:", q2["remaining"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
