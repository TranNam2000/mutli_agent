---
SCOPE: simple, feature, module, bug_fix
TRIGGERS: maintain, existing codebase, legacy, refactor, migrate, cleanup, debt, regression, upgrade, rewrite
MAX_TOKENS: 800
---

# PM Skill — Maintain an existing project

Pick this when the request is to modify / stabilize / evolve an already
shipped codebase. Change-safety > new-feature velocity.

## Routing bias

- Always run TechLead even for small tasks — legacy code has hidden
  coupling; `impact_area` detection is cheap insurance.
- `bug_fix` kind → **still run BA(lite)** to capture acceptance criteria
  for regression prevention (override the default "skip BA" rule).
- `refactor` kind → full techlead → dev → test (default) but add an
  explicit `"legacy_affected": true` hint to metadata.

## Metadata defaults emitted

- `technical_debt.legacy_affected`: **true** unless request clearly targets
  a brand-new module.
- `flow_control.max_revisions`: **2** (default) — maintain work often
  discovers surprises, extra revisions are worth it.
- `flow_control.skip_critic`: NEVER include TechLead (core-file risk is
  high in maintain mode).

## Classification rubric addendum

- Keywords *rollback*, *hotpatch*, *production down* → force `hotfix` kind.
- Keywords *cleanup*, *tidy*, *reduce coupling*, *rename module* → prefer
  `refactor` over `feature` even if request sounds additive.
- Keywords *upgrade SDK*, *migrate*, *deprecated API* → `refactor` with
  `risk_level=high` by default.

## Downstream hints

The IntegrityRules module_blacklist is especially relevant here; review
its top entries before classifying. A new maintain task in a blacklisted
module should never be Low-risk.
