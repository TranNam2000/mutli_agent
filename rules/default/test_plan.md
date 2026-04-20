# Criteria: QA Test Planner
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.40 format=0.20 quality=0.40

## Completeness (có enough no)
- [ ] Có test cases for mỗi user story / requirement
- [ ] Mỗi feature: ít nhất 2 happy path + 2 edge/negative cases
- [ ] Test cases có enough: Given / When / Then + Priority
- [ ] Có exit criteria with pass rate cụ can (e.g.: P0+P1 ≥ 95%)
- [ ] Có performance targets with number liệu (P50/P95/P99)
- [ ] Security checklist ít nhất 5 items

## Format (đúng cấu trúc no)
- [ ] Test cases use table: TC-ID | Title | Given | When | Then | Priority | Type
- [ ] TC-ID per format TC-001, TC-002...
- [ ] Priority use P0/P1/P2/P3

## Quality (sâu and hữu ích no)
- [ ] Test cases verify REQUIREMENTS, no verify implementation
- [ ] Edge cases cover boundary values thực sự (no chỉ "null input")
- [ ] Negative cases phản ánh real user mistakes
- [ ] No có test case nào giả định biết code before
