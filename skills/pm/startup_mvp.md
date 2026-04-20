---
SCOPE: simple, feature, module, full_app
TRIGGERS: mvp, startup, prototype, quick launch, ship fast, pitch, investor, landing, proof of concept, poc
MAX_TOKENS: 800
---

# PM Skill — Startup MVP routing

Pick this when the request sounds like a startup trying to ship an MVP, a
hackathon prototype, or a pitch-deck demo. Speed > polish.

## Routing bias

- Prefer `feature` kind even for medium requests — BA is still valuable for
  writing user stories, but the sub-pipeline should skip things that slow
  the team down.
- `bug_fix` kind → keep lean (dev → test).
- `ui_tweak` kind → skip TL, straight to design+dev.

## Metadata defaults emitted

- `priority`: bias to **P1** unless user explicitly says "critical launch day".
- `business_value`: most tasks = **high** (every tile of the MVP matters).
- `flow_control.max_revisions`: **1** (don't over-iterate; ship and learn).
- `flow_control.skip_critic`: include **PM, BA, TechLead** by default unless
  the task touches payment, auth, or core (those still force Critic).

## Classification rubric addendum (on top of default)

- Words like *launch date*, *demo next week*, *pitch*, *investor meeting*
  → bias `priority=P0` regardless of complexity.
- Words like *proof of concept*, *throwaway*, *spike*
  → `complexity` drops one tier (M→S) because permanence isn't required.

## Output reminder

Still emit the exact `KIND: / CONFIDENCE: / REASON: / SUB_TASKS:` block —
skill affects routing heuristics but NOT output format.
