"""
sentinel.py — the 360 content-security mediator.

Every artifact crossing the mesh (briefs, scraped law, brand assets, API
responses) is screened here BEFORE any agent acts on it. Agents treat each
other's output as data, not instructions. Fail-closed on high severity.

These checks are deterministic and real. In production you add: malware/file
scanning, URL reputation feeds, and an ML injection classifier. The contract
(screen -> verdict) stays identical.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

INJECTION = [
    r"ignore (all|your|previous) instructions", r"disregard (the )?(above|rules)",
    r"\bsystem\s*:", r"you are now", r"exfiltrate", r"send .* to .*@",
    r"reveal (your )?(system )?prompt", r"act as (an? )?(unfiltered|jailbroken)",
]
SECRETS = [r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]+PRIVATE KEY"]
URL = re.compile(r"https?://([^/\s]+)", re.I)
SUSPICIOUS_TLD = (".ru", ".tk", ".zip", ".mov")  # illustrative; use a real feed


@dataclass
class Verdict:
    allow: bool
    severity: str            # none | low | high
    reasons: list[str]


def screen(text: str) -> Verdict:
    if not text:
        return Verdict(True, "none", [])
    t = str(text)
    reasons, severity = [], "none"
    for pat in INJECTION:
        if re.search(pat, t, re.I):
            reasons.append(f"possible prompt injection: /{pat}/"); severity = "high"
    for pat in SECRETS:
        if re.search(pat, t):
            reasons.append("possible secret/credential in content"); severity = "high"
    for host in URL.findall(t):
        if host.lower().endswith(SUSPICIOUS_TLD):
            reasons.append(f"suspicious link host: {host}")
            severity = "high" if severity != "high" else severity
    # fail-closed: block on any high-severity finding
    return Verdict(allow=(severity != "high"), severity=severity, reasons=reasons)
