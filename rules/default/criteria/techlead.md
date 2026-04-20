# Criteria: Tech Lead
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.40 format=0.20 quality=0.40

## Scale (determine before grading)
- **Simple** (1 screen, no API): only use Simple checklist
- **Medium** (complex state or local DB): Simple + Medium
- **Full** (API / multiple features): all levels

## Completeness (what must exist)
- [ ] Folder structure + files to create
- [ ] State management approach with rationale (StatefulWidget / Cubit / BLoC)
- [ ] Technical tasks with hour estimates
- [ ] [Medium+] Data model / local schema
- [ ] [Medium+] Technical risks + mitigation
- [ ] [Full] API endpoints with method + path + request/response
- [ ] [Full] Security strategy (auth, token storage)
- [ ] [Full] Database schema with relationships

## Format (structure)
- [ ] Folder structure is an actual tree (not prose)
- [ ] Technical tasks have ID + hour estimate
- [ ] [Full] API format: METHOD /path + request/response bodies

## Quality (depth / usefulness)
- [ ] Tech stack choices justified, not just listed
- [ ] Estimates broken down — not "2-3 days" blanket
- [ ] After reading, Dev knows which file to start with and what to write
- [ ] No over-engineering (no Clean Architecture for a simple screen with no API)
