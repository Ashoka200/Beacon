"""
agents/base.py — common chassis for every subagent.

Each agent declares the capabilities it serves and whether its action is
destructive. `_llm()` is the single seam where the real model call lives.
With ANTHROPIC_API_KEY set, every agent reasons with Claude Opus 4.8
(adaptive thinking); without a key — or on any API failure — it degrades
to the deterministic stub so the fleet never goes down with the model.
"""
from __future__ import annotations
import os
from contracts import AgentRequest, AgentResponse, Capability

# Load secrets from the project-root .env (sibling of this agents/ dir) before
# reading any key. Explicit path so it works regardless of cwd or entry point.
try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    load_dotenv(_ENV_PATH)
except ImportError:
    pass  # python-dotenv optional; falls back to the ambient environment

USE_REAL_LLM = bool(os.environ.get("ANTHROPIC_API_KEY"))  # auto on if key present
MODEL = "claude-opus-4-8"

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


class Agent:
    name: str = "agent"
    tier: int = 2
    serves: set[Capability] = set()
    destructive: bool = False

    def _llm(self, system: str, user: str, stub: str) -> str:
        """The ONE place a real model call lives. Stubbed in dry-run."""
        if not USE_REAL_LLM:
            return stub
        import anthropic
        try:
            response = _get_client().messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            return text or stub
        except anthropic.APIError:
            # fail-safe: the mesh keeps running on the deterministic stub
            return stub

    def handle(self, req: AgentRequest) -> AgentResponse:  # pragma: no cover
        raise NotImplementedError
