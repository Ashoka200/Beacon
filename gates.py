"""
gates.py — accountable-human / client decision gates.

Per the go-live runbook: a console prompt blocks forever on a server, so in
production these notify a human (Slack/email/web) and resume on their reply.
In DRY_RUN the gate records the request as PENDING and returns NOT approved,
so nothing destructive ever runs unattended. Authority is bound to the action,
not to the agent that asked.
"""
from __future__ import annotations
from dataclasses import dataclass, field

DRY_RUN = True            # flip off only with a real async approval path wired


@dataclass
class GateResult:
    approved: bool
    pending: bool
    by: str = ""
    detail: str = ""


_PENDING: list[dict] = []


def request_gate(kind: str, action: str, detail: dict) -> GateResult:
    """kind = 'human' | 'client'."""
    _PENDING.append({"kind": kind, "action": action, "detail": detail})
    if DRY_RUN:
        return GateResult(approved=False, pending=True,
                          detail=f"{kind} approval PENDING for '{action}' (dry-run)")
    raise NotImplementedError("wire Slack/email/web approval before going live")


def simulate_decision(action: str, approve: bool, by: str) -> GateResult:
    """DEMO ONLY: stands in for a human/client clicking approve/reject."""
    return GateResult(approved=approve, pending=False, by=by,
                      detail=f"{'approved' if approve else 'rejected'} by {by}")


def pending() -> list[dict]:
    return list(_PENDING)
