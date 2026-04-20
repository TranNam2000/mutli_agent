---
SCOPE: bug_fix
TRIGGERS: hotfix, incident, production down, p0, sev1, outage, crash loop, data loss, security breach, rollback urgently
MAX_TOKENS: 600
---

# PM Skill — Emergency Hotfix routing

Pick this when the request is a live production incident. Restore service
now; polish later.

## Routing bias

- KIND must be `hotfix` (or `bug_fix` if no live incident language).
- CONFIDENCE ≥ 0.9 (don't waste time clarifying — user is on fire).
- Sub-pipeline forcibly trimmed to **Dev → Test** (skip BA/Design/TL).

## Metadata defaults emitted

- `context.scope`: **hotfix**
- `context.priority`: **P0**
- `context.risk_level`: inherit from impact (if touches auth/payment → high;
  otherwise med minimum — a hotfix is never low-risk).
- `flow_control.skip_critic`: **["PM", "BA", "TechLead"]** — Fast-Track.
- `flow_control.max_revisions`: **1** (get the fix out; followup PR later).
- `flow_control.require_qa`: **true** (non-negotiable, it's production).

## Classification rubric addendum

- Any mention of *users can't*, *prod broken*, *losing money*, *data loss*,
  *can't login*, *checkout failing*, *server 5xx* → auto-classify hotfix.
- Even if complexity looks L/XL, trim the scope to the smallest possible
  fix and record the rest as follow-up `refactor` sub-tasks.

## Emergency Audit expected behaviour

If QA blocks after a hotfix deploy, **don't enter Emergency Audit Mode
recursively** — flag for human review instead. Hotfixes already have
risk=high and skip BA/TL, so Emergency Audit's escalation is redundant.
