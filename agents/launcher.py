"""launcher.py — TIER 2. DESTRUCTIVE publisher. Gated by the Orchestrator.
Refuses jurisdictions with no ratified rule (geo-fence). Ad-platform calls
are integration seams (need your accounts/keys/approvals)."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse


class Launcher(Agent):
    name, tier, destructive = "Launcher", 2, True
    serves = {Capability.PUBLISH}

    def __init__(self, registry):
        self.reg = registry

    def handle(self, req: AgentRequest) -> AgentResponse:
        jur = req.payload.get("jurisdiction", "")
        if not self.reg.geofence_ok(jur):
            return AgentResponse(False, blocked_reason=f"geo-fence: no ratified rules for {jur}")
        if not req.payload.get("creative", {}).get("ai_disclosure", True):
            # if the jurisdiction requires disclosure and it's missing, refuse
            if self.reg.requires(jur, "ai_content_disclosure"):
                return AgentResponse(False, blocked_reason="AI disclosure required but missing")
        # --- INTEGRATION SEAM: Google Ads / Meta / Microsoft API publish -----
        result = {"published": False, "dry_run": True,
                  "would_publish_on": req.payload.get("channel", "google_search"),
                  "jurisdiction": jur}
        return AgentResponse(True, data={"launch": result},
                             notes=["dry-run: real publish needs ad-platform API access"])
