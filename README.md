# Marketing Fleet — full scaffold

The complete agent fleet for the local-advertising platform, wired into a
**governed mesh** and runnable end-to-end in dry-run with no API key.

```
python run_campaign.py
```

## What this is (and isn't)
This is the **architecture, complete and runnable** — not a finished
production system. The connective tissue (mesh, contracts, firewall, gates,
shared state, least-privilege) is real code. The parts that need your
accounts/keys are clearly marked **integration seams**.

| Real / runnable now | Stubbed seam (wire in production) |
|---|---|
| Orchestrator routing + trace | LLM reasoning (`agents/base.py::_llm`) |
| Typed contracts + validation | Image/video generation (Creative Studio router) |
| Sentinel firewall (fail-closed) | Ad-platform publish (Launcher) |
| Human/client gates (headless-ready) | Real law scan (Legal-Watch adapter) |
| Least-privilege matrix | Real performance metrics (Optimizer) |
| Registry geo-fence + AI-disclosure logic | |
| Reach math, flywheel writeback, IP guard | |

## Files
- `contracts.py` — Capability enum + AgentRequest/Response + validation.
- `orchestrator.py` — the mediator: validate -> Sentinel -> least-privilege -> gate -> route -> trace.
- `sentinel.py` — content firewall (injection / secrets / bad links), fail-closed.
- `gates.py` — human + client approval, headless-ready (DRY_RUN = pending).
- `state.py` — Registry (compliance) + Flywheel (performance library).
- `agents/` — Planner, Audience, Reach, CreativeStudio, Launcher, Optimizer,
  CapabilityOptimizer, LegalWatch. Each declares the capabilities it serves
  and whether its action is destructive.
- `run_campaign.py` — end-to-end demo + red-team + least-privilege checks.

## How an agent goes from scaffold to production
1. Wire `_llm()` in `agents/base.py` to the Anthropic API (set `ANTHROPIC_API_KEY`).
2. Replace the agent's integration seam (ad-platform / generation / scan).
3. Stamp it through agent-factory: tools, system prompt, budgets, memory,
   >=10 evals, red-team, trace review, security audit. **No agent ships past a
   failed gate.** Only Legal-Watch has passed so far (separate package).
4. Tighten the `ALLOWED` least-privilege matrix in `orchestrator.py`.

## Security posture
Agents never call each other directly. The Orchestrator mediates every hop;
Sentinel screens all content crossing the mesh; agents treat each other's
output as data, not instructions; and human/client gates are bound to the
**action**, so no agent (even a compromised one) can publish, ratify, or use
unapproved creative on its own. The red-team step in `run_campaign.py`
demonstrates a malicious brief being contained.

## Build order (unchanged)
Planner (Gate 1 — the bet) -> Sentinel (mediates the mesh) -> Audience /
Reach / Launcher / Optimizer -> Creative Studio -> Capability-Optimizer.
Legal-Watch + Registry already run alongside.

## DO NOT
- Flip `gates.DRY_RUN` off without a real async approval path.
- Give any agent a raw shell tool or direct cross-agent call.
- Let the Launcher publish into a jurisdiction with no ratified rule.
