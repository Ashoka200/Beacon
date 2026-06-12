"""planner.py — TIER 2. Firm profile -> channel-ready campaign plan.
The make-or-break agent (Gate 1). LLM does the judgment via _llm seam."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse


class Planner(Agent):
    name, tier = "Planner", 2
    serves = {Capability.PLAN_CAMPAIGN}

    def __init__(self, flywheel):
        self.fly = flywheel

    def handle(self, req: AgentRequest) -> AgentResponse:
        firm = req.payload.get("firm", {})
        vertical = firm.get("vertical", "general")
        goal = req.payload.get("goal", "calls")
        hint = self.fly.top_pattern(vertical)
        # everything the client told us (budget, goals, service area, audiences,
        # offer, and their own campaign vision) goes to the strategist
        details = {k: v for k, v in req.payload.items() if k != "firm" and v}
        vision = str(req.payload.get("client_vision", "")).strip()
        system = ("You are a senior media planner crafting a best-in-class, creative "
                  "campaign strategy. Blend ALL stated goals into one coordinated plan. "
                  "Respect the service area (wider areas include narrower markets). "
                  + ("THE CLIENT HAS DESCRIBED THEIR OWN CAMPAIGN VISION — build the "
                     "plan around it faithfully, improving it with your expertise. "
                     if vision else "")
                  + "Output a polished campaign plan in clean markdown.")
        plan = self._llm(
            system=system,
            user=f"firm={firm} details={details} flywheel_hint={hint}",
            stub=str({
                "vertical": vertical, "goal": goal,
                "channel": "google_search",
                "audience_brief": f"in-market for {vertical} services; local intent",
                "keywords": [f"{vertical} near me", f"best {vertical} {firm.get('city','')}"],
                "creative_brief": f"trust + local + clear CTA for a {vertical}",
                "flywheel_hint": hint,
            }))
        return AgentResponse(True, data={"plan": plan})
