# Criteria: Project Manager (PM)
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.50 format=0.30 quality=0.20

## Completeness (what must exist)
- [ ] Exactly one top-level `KIND:` line with one of the 5 allowed values
- [ ] `CONFIDENCE:` line with a float in [0.0, 1.0]
- [ ] `REASON:` line citing concrete evidence from the request (not generic)
- [ ] `SUB_TASKS:` section present (either `- [NONE]` or ≥ 2 sub-task entries)

## Format (structure)
- [ ] Output uses the exact key labels `KIND:`, `CONFIDENCE:`, `REASON:`, `SUB_TASKS:`
- [ ] No extra prose, no code fences around the whole answer, no clarifying questions
- [ ] Sub-task lines, if any, follow `- KIND: <label> | <desc>` shape and are parsable

## Quality (depth / usefulness)
- [ ] REASON references specific words/phrases from the request (not vague "sounds like X")
- [ ] CONFIDENCE < 0.6 when evidence is mixed or conflicting; ≥ 0.85 when signal is clean
- [ ] SUB_TASKS only used for genuinely independent intents, never for internal feature steps
