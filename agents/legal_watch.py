"""legal_watch.py — TIER 3. Drafts compliance rules only; NEVER ratifies.
The full built version (with evals/red-team/security PASS) lives in the
standalone legal-watch package; this is its mesh adapter."""
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse


class LegalWatch(Agent):
    name, tier = "LegalWatch", 3
    serves = {Capability.SCAN_LAW}

    def handle(self, req: AgentRequest) -> AgentResponse:
        # INTEGRATION SEAM: real scan + draft into the registry (human ratifies).
        draft = {"id": "US-FTC-AI-DISCLOSURE", "jurisdiction": "US",
                 "topic": "ai_content_disclosure", "status": "draft",
                 "note": "awaiting human ratification — agent cannot ratify"}
        return AgentResponse(True, data={"draft": draft},
                             notes=["filed as DRAFT; ratify via human review CLI"])
