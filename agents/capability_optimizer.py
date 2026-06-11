"""capability_optimizer.py — TIER 3. Proposes platform improvements; ships
them as CANARY with auto-rollback. Reversible changes autonomous; irreversible
ones go to the human gate."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse


class CapabilityOptimizer(Agent):
    name, tier = "CapabilityOptimizer", 3
    serves = {Capability.IMPROVE_CAPABILITY}

    def handle(self, req: AgentRequest) -> AgentResponse:
        change = req.payload.get("change", "swap video engine to newer model")
        reversible = req.payload.get("reversible", True)
        plan = {
            "change": change,
            "rollout": "canary 5% traffic vs baseline",
            "promote_if": "beats baseline on conversion with no regression on "
                          "CPA/latency/error-rate/uptime",
            "auto_rollback": "revert to last-known-good on any degradation",
            "autonomous": reversible,
            "needs_human_gate": (not reversible),
        }
        return AgentResponse(True, data={"deploy_plan": plan},
                             notes=["irreversible infra changes require human ratification"]
                             if not reversible else [])
