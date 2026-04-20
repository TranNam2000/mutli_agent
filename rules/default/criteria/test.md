# Criteria: QA/Tester
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.35 format=0.20 quality=0.45

## Completeness (what must exist)
- [ ] Test strategy with scope + risk areas
- [ ] At least 3 happy path + 3 edge/negative cases per feature
- [ ] Test cases contain: precondition + steps + expected result
- [ ] Performance test scenarios with target numbers (P50/P95/P99)
- [ ] Security checklist with ≥ 5 items tied to this specific feature
- [ ] Exit criteria with concrete numbers (pass rate, bug count)

## Format (structure)
- [ ] Test cases in table: TC-ID | Title | Precondition | Steps | Expected | Priority | Type
- [ ] Priority uses P1/P2/P3/P4
- [ ] Bug report template with full fields
- [ ] Test levels labeled: Unit / Integration / System / UAT

## Quality (depth / usefulness)
- [ ] Edge cases cover real boundary values (not just "null input")
- [ ] Negative cases reflect real user mistakes
- [ ] Performance targets with concrete numbers
- [ ] Security tests tied to this feature, not copy-pasted OWASP generic
- [ ] Automation candidates justified with why each is suitable
