# Criteria: QA Test Planner
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.40 format=0.20 quality=0.40

## Completeness (what must exist)
- [ ] Test cases for each user story / requirement
- [ ] Per feature: at least 2 happy path + 2 edge/negative cases
- [ ] Test cases include: Given / When / Then + Priority
- [ ] Exit criteria with concrete pass rate (e.g. P0+P1 ≥ 95%)
- [ ] Performance targets with numbers (P50/P95/P99)
- [ ] Security checklist with ≥ 5 items

## Format (structure)
- [ ] Test cases in table: TC-ID | Title | Given | When | Then | Priority | Type
- [ ] TC-ID format: TC-001, TC-002, …
- [ ] Priority uses P0/P1/P2/P3

## Quality (depth / usefulness)
- [ ] Test cases verify REQUIREMENTS, not implementation details
- [ ] Edge cases cover real boundary values (not just "null input")
- [ ] Negative cases reflect real user mistakes
- [ ] No test case assumes knowledge of the code (test the spec, not the source)
