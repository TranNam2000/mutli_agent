---
SCOPE: feature, module, full_app
TRIGGERS: enterprise, b2b, saas, multi-tenant, rbac, permission, role, audit, compliance, gdpr, soc2, hipaa, sso, saml, oauth org, internal tool, admin panel
STEPS: ba, design, techlead, test_plan, dev, test
MAX_TOKENS: 3000
---

# PM Skill — Enterprise B2B / SaaS

Sản phẩm cho doanh nghiệp: nhiều user/org, có phân quyền, audit trail, compliance.

## Đặc điểm
- Multi-tenant (org → users → resources)
- RBAC (admin / member / viewer / custom roles)
- Audit log (mọi mutation phải log: who, when, what)
- SSO / OAuth org-level
- Compliance: GDPR/SOC2/HIPAA tuỳ thị trường

## Routing
- **KIND**: `feature`
- **STEPS**: full pipeline `ba, design, techlead, test_plan, dev, test`
- BA bắt buộc: enterprise có nhiều ràng buộc business ngầm (compliance, role hierarchy)
- TechLead bắt buộc: schema multi-tenant + index + permission check phải design trước

## Sub-pipeline gợi ý
```
DYNAMIC_STEPS: ba, design, techlead, test_plan, dev, test
```

## Risk areas (PM phải nhắc)
- Data isolation giữa tenant (nightmare nếu rò)
- Permission check ở mọi endpoint (default-deny, không default-allow)
- PII / encryption at rest + in transit
- Audit log retention period theo compliance
- Migration strategy khi schema đổi (zero-downtime tenant by tenant)

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Read` to check existing docs/config. No code edits.
