# Criteria: QA/Tester
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.35 format=0.20 quality=0.45

## Completeness (có enough no)
- [ ] Test strategy có scope + risk areas
- [ ] Ít nhất 3 happy path + 3 edge/negative cases mỗi feature
- [ ] Test cases có enough: precondition + steps + expected result
- [ ] Performance test scenarios có target numbers (P50/P95/P99)
- [ ] Security checklist ít nhất 5 items liên quan đến feature cụ can
- [ ] Exit criteria có number liệu cụ can (pass rate, bug count)

## Format (đúng cấu trúc no)
- [ ] Test cases use table: TC-ID | Title | Precondition | Steps | Expected | Priority | Type
- [ ] Priority use P1/P2/P3/P4
- [ ] Bug report template đầy enough các field
- [ ] Test levels phân chia rõ: Unit / Integration / System / UAT

## Quality (sâu and hữu ích no)
- [ ] Edge cases cover boundary values thực sự (no chỉ "null input")
- [ ] Negative cases phản ánh real user mistakes
- [ ] Performance targets có number liệu cụ can
- [ ] Security test liên quan trực tiếp đến feature, no copy-paste OWASP chung chung
- [ ] Automation candidates có lý do cụ can tại why phù hợp
