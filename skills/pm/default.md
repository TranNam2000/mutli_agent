# Skill: PM default routing

Scope: any | any
Triggers: any request

Use this skill for every incoming request. It is the baseline routing skill.

## Focus
- Be decisive. Classification should take <10 seconds of model time.
- Prefer the heuristic layer (keyword match) unless the signal is mixed.
- Do not design, estimate, or ask clarifying questions. The orchestrator will
  run a clarification gate when CONFIDENCE < 0.6.

## Output discipline
- Emit exactly the four keys: KIND, CONFIDENCE, REASON, SUB_TASKS.
- Never wrap the entire output in code fences.
- Never include additional commentary before or after the block.
