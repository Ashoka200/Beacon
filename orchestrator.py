"""
orchestrator.py — the mediator of the mesh.

Nothing in this fleet calls another agent directly. They hand a request to
the Orchestrator, which:
  1. validates the typed contract,
  2. Sentinel-screens any content in the payload (fail-closed),
  3. enforces least privilege (is this requester allowed this capability?),
  4. routes human/client-gated actions to the gate FIRST,
  5. dispatches to the one agent that serves the capability,
  6. records a trace line for every hop.

This is "agents interact whenever required" — with a paper trail and no
escalation path.
"""
from __future__ import annotations
import uuid
import sentinel
import gates
from contracts import (AgentRequest, AgentResponse, Capability,
                       HUMAN_GATED, CLIENT_GATED)

# Least-privilege matrix: who may request what. Tightened per deployment.
ALLOWED = {
    "human":               set(Capability),
    "orchestrator":        set(Capability),
    "planner":             {Capability.DEFINE_AUDIENCE, Capability.MAKE_CREATIVE,
                            Capability.SET_REACH, Capability.PUBLISH, Capability.MEASURE},
    "optimizer":           {Capability.MEASURE, Capability.MAKE_CREATIVE},
    "capability_optimizer":{Capability.IMPROVE_CAPABILITY},
    "legal_watch":         {Capability.SCAN_LAW},
}


class Orchestrator:
    def __init__(self):
        self._agents: dict[Capability, object] = {}
        self.trace: list[str] = []

    def register(self, agent) -> None:
        for cap in agent.serves:
            self._agents[cap] = agent

    def _log(self, msg: str) -> None:
        self.trace.append(msg)

    def route(self, req: AgentRequest) -> AgentResponse:
        tid = req.trace_id or uuid.uuid4().hex[:8]
        req.trace_id = tid

        errs = req.validate()
        if errs:
            self._log(f"[{tid}] REJECT contract: {errs}")
            return AgentResponse(False, blocked_reason=f"contract: {errs}")

        # least privilege
        allowed = ALLOWED.get(req.requester, set())
        if req.capability not in allowed:
            self._log(f"[{tid}] DENY {req.requester} -> {req.capability.value} (least-privilege)")
            return AgentResponse(False, blocked_reason="not permitted for requester")

        # Sentinel screens content crossing the mesh
        blob = " ".join(str(v) for v in req.payload.values())
        v = sentinel.screen(blob)
        if not v.allow:
            self._log(f"[{tid}] SENTINEL BLOCK {req.capability.value}: {v.reasons}")
            return AgentResponse(False, blocked_reason=f"sentinel: {v.reasons}")
        if v.reasons:
            self._log(f"[{tid}] SENTINEL flag (allowed): {v.reasons}")

        # human/client gate FIRST, regardless of requester
        if req.capability in HUMAN_GATED:
            g = gates.request_gate("human", req.capability.value, req.payload)
            if not g.approved:
                self._log(f"[{tid}] GATE human pending for {req.capability.value}")
                return AgentResponse(False, gate_required="human", notes=[g.detail])
        if req.capability in CLIENT_GATED and not req.payload.get("_client_approved"):
            self._log(f"[{tid}] GATE client required for {req.capability.value}")
            # creative is still produced, but flagged as needing client acceptance
            # (handled by the agent returning gate_required); fall through to agent.

        agent = self._agents.get(req.capability)
        if agent is None:
            self._log(f"[{tid}] no agent serves {req.capability.value}")
            return AgentResponse(False, blocked_reason="no agent for capability")

        self._log(f"[{tid}] ROUTE {req.requester} -> {agent.name} ({req.capability.value})")
        resp = agent.handle(req)
        self._log(f"[{tid}] {agent.name} ok={resp.ok} gate={resp.gate_required or '-'}")
        return resp
