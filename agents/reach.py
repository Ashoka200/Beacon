"""reach.py — deterministic reach by service category + customer tier."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse

CATEGORY_RADIUS = {"dentist": 8, "salon": 6, "accounting": 25, "legal": 30,
                   "plumber": 15, "general": 20}             # miles
TIER_CHANNELS = {"starter": 1, "growth": 3, "pro": 5}
TIER_CAP = {"starter": 500, "growth": 2000, "pro": 5000}     # monthly $


class Reach(Agent):
    name, tier = "Reach", 2
    serves = {Capability.SET_REACH}

    def handle(self, req: AgentRequest) -> AgentResponse:
        cat = req.payload.get("category", "general")
        tier = req.payload.get("tier", "starter")
        remote = req.payload.get("serves_remote", False)
        out = {
            "radius_miles": CATEGORY_RADIUS.get(cat, 20),
            "channels_allowed": TIER_CHANNELS.get(tier, 1),
            "monthly_spend_cap": TIER_CAP.get(tier, 500),
            "remote_layer": bool(remote),
        }
        return AgentResponse(True, data={"reach": out})
