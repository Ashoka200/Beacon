"""optimizer.py — TIER 3. Reads performance, writes the flywheel, proposes
reallocations (recommend-only until it earns autonomy)."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse


class Optimizer(Agent):
    name, tier = "Optimizer", 3
    serves = {Capability.MEASURE}

    def __init__(self, flywheel):
        self.fly = flywheel

    def handle(self, req: AgentRequest) -> AgentResponse:
        # INTEGRATION SEAM: pull real metrics from the ad platforms.
        vertical = req.payload.get("vertical", "general")
        variant = req.payload.get("variant", "A")
        # stubbed result feeding the flywheel
        self.fly.record(vertical, variant, ctr=0.061, watch_time=18.4, conversion=0.039)
        proposal = {"action": "shift 10% budget to best variant", "auto_exec": False,
                    "reason": "recommend-only until track record exists"}
        return AgentResponse(True, data={"measured": True, "proposal": proposal,
                                         "flywheel": self.fly.top_pattern(vertical)})
