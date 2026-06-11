"""
contracts.py — the typed message contract for the inter-agent mesh.

Agents never call each other directly. They emit an AgentRequest for a
CAPABILITY; the Orchestrator validates it, screens it, gates it if needed,
and routes it. Every interaction is schema-checked at this boundary.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    PLAN_CAMPAIGN = "plan_campaign"
    DEFINE_AUDIENCE = "define_audience"
    SET_REACH = "set_reach"
    MAKE_CREATIVE = "make_creative"
    PUBLISH = "publish"                 # destructive — gated
    MEASURE = "measure"
    SCAN_LAW = "scan_law"               # drafts only
    IMPROVE_CAPABILITY = "improve_capability"
    SCREEN_CONTENT = "screen_content"   # Sentinel


# Actions that may NEVER run without an accountable human/client decision,
# no matter which agent requests them. Authority is bound to the ACTION.
HUMAN_GATED = {Capability.PUBLISH}
CLIENT_GATED = {Capability.MAKE_CREATIVE}   # creative must be client-accepted before use


@dataclass
class AgentRequest:
    capability: Capability
    payload: dict[str, Any]
    requester: str                       # which agent/role is asking
    budget_usd: float = 0.0
    trace_id: str = ""

    def validate(self) -> list[str]:
        errs = []
        if not isinstance(self.capability, Capability):
            errs.append("capability must be a Capability enum")
        if not isinstance(self.payload, dict):
            errs.append("payload must be a dict")
        if not self.requester:
            errs.append("requester is required (no anonymous requests)")
        if self.budget_usd < 0:
            errs.append("budget_usd cannot be negative")
        return errs


@dataclass
class AgentResponse:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    gate_required: str = ""              # "" | "human" | "client"
    blocked_reason: str = ""
    notes: list[str] = field(default_factory=list)
