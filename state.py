"""
state.py — shared state read by many agents, written narrowly.

  * Registry: ratified compliance rules (the Legal-Watch spine, compact copy).
    Written only via Legal-Watch drafts + human ratification. geofence_ok()
    is what the Launcher checks before publishing anywhere.
  * Flywheel: the Creative Performance Library + best-practice notes, per
    vertical. Written by the Optimizer from real results; read by Planner,
    Audience, and Creative Studio. This is the durable moat.
"""
from __future__ import annotations
from datetime import datetime, timezone


class Registry:
    def __init__(self):
        self._rules: list[dict] = []

    def add_ratified(self, rule_id, jurisdiction, topic, machine_rule, by="counsel"):
        self._rules.append({
            "id": rule_id, "jurisdiction": jurisdiction.upper(), "topic": topic,
            "machine_rule": machine_rule, "status": "ratified",
            "ratified_by": by, "ratified_at": datetime.now(timezone.utc).isoformat(),
        })

    def ratified(self, jurisdiction="") -> list[dict]:
        return [r for r in self._rules if r["status"] == "ratified"
                and (not jurisdiction or r["jurisdiction"] == jurisdiction.upper())]

    def geofence_ok(self, jurisdiction: str) -> bool:
        return len(self.ratified(jurisdiction)) > 0

    def requires(self, jurisdiction: str, topic: str) -> bool:
        return any(r["topic"] == topic for r in self.ratified(jurisdiction))


class Flywheel:
    """Per-vertical performance memory. Real numbers in, better creative out."""
    def __init__(self):
        self.performance: list[dict] = []
        self.best_practices: dict[str, list[str]] = {}

    def record(self, vertical, variant, ctr, watch_time, conversion):
        self.performance.append({"vertical": vertical, "variant": variant,
                                 "ctr": ctr, "watch_time": watch_time,
                                 "conversion": conversion})

    def top_pattern(self, vertical: str) -> str:
        rows = [p for p in self.performance if p["vertical"] == vertical]
        if not rows:
            return "no data yet — explore broadly"
        best = max(rows, key=lambda p: p["conversion"])
        return f"best so far for {vertical}: '{best['variant']}' (conv {best['conversion']:.1%})"

    def seed_best_practices(self):
        self.best_practices = {
            "_global": [
                "hook in <3s; state the payoff immediately",
                "pattern interrupt every 5-8s to hold retention",
                "thumbnail: one clear subject + high contrast + curiosity gap",
                "one claim per asset, each backed by a substantiation record",
            ]
        }
