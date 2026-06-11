"""creative_studio.py — TIER 3. Showrunner: concept -> script -> image/video
via a MODEL ROUTER -> variants. Emulates craft, never a named creator.
Attaches AI disclosure; runs IP/brand-safety. Output needs client approval."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse

# model router: pick engine per job; survives model churn (Sora 2 deprecation).
VIDEO_ENGINES = ["veo-3.1", "kling-3.0", "runway-gen-4.5", "wan-2.7(self-host)"]
IMAGE_ENGINES = ["nano-banana-pro", "<image-engine-2>"]
NAMED_CREATORS = ["mrbeast", "mr beast", "logan paul", "kardashian"]  # illustrative


class CreativeStudio(Agent):
    name, tier = "CreativeStudio", 3
    serves = {Capability.MAKE_CREATIVE}

    def __init__(self, flywheel, registry):
        self.fly, self.reg = flywheel, registry

    def _route_video(self, brief):  # INTEGRATION SEAM -> call the chosen engine
        return {"engine": VIDEO_ENGINES[0], "status": "stubbed_generation", "brief": brief}

    def handle(self, req: AgentRequest) -> AgentResponse:
        brief = str(req.payload.get("brief", ""))
        vertical = req.payload.get("vertical", "general")
        jurisdiction = req.payload.get("jurisdiction", "US")

        # IP boundary: refuse to clone a named creator/brand
        if any(c in brief.lower() for c in NAMED_CREATORS):
            return AgentResponse(False, blocked_reason=(
                "cannot imitate a named creator/brand (IP/right-of-publicity). "
                "Proposing an original treatment using the same proven techniques."))

        concept = self._llm(
            system="You are a showrunner. Apply proven hooks/retention/thumbnail craft to ORIGINAL content.",
            user=f"brief={brief} vertical={vertical} patterns={self.fly.top_pattern(vertical)}",
            stub=f"Original concept for {vertical}: 3s payoff hook, 1 clear claim, strong CTA")

        variants = [
            {"id": "A", "concept": concept, "image": IMAGE_ENGINES[0],
             "video": self._route_video(concept)},
            {"id": "B", "concept": concept + " (alt hook)", "image": IMAGE_ENGINES[0],
             "video": self._route_video(concept)},
        ]
        # AI disclosure required where the registry says so
        disclosure = self.reg.requires(jurisdiction, "ai_content_disclosure")
        for v in variants:
            v["ai_disclosure"] = disclosure
        notes = ["AI-disclosure attached" if disclosure else "no disclosure rule for jurisdiction",
                 "all claims must map to a substantiation record before publish"]
        return AgentResponse(True, data={"variants": variants},
                             gate_required="client", notes=notes)
