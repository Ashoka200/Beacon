"""audience.py — TIER 2. Decides the AUDIENCE DEFINITION for platform signal
graphs. Never builds its own cross-web tracking. Blocks sensitive targeting."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse

SENSITIVE = {"health", "race", "religion", "sexual_orientation", "financial_status"}


class Audience(Agent):
    name, tier = "Audience", 2
    serves = {Capability.DEFINE_AUDIENCE}

    def handle(self, req: AgentRequest) -> AgentResponse:
        asked = set(req.payload.get("attributes", []))
        bad = asked & SENSITIVE
        if bad:
            return AgentResponse(False, blocked_reason=f"sensitive-category targeting blocked: {bad}")
        vertical = req.payload.get("vertical", "general")
        definition = {
            "mode": "platform_signal_match",   # ride Google/Meta consented graphs
            "include": [f"in_market:{vertical}", "lookalike:first_party_consented_list",
                        "geo:catchment", "intent_keywords"],
            "exclude": ["existing_customers"],
            "note": "audience DEFINITION only; platforms do the consented matching",
        }
        return AgentResponse(True, data={"audience": definition})
