# Criteria: QA Reviewer
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.35 format=0.20 quality=0.45

## Completeness (có enough no)
- Tất cả TC in test plan đều có verdict (PASSED/FAILED/NOT TESTABLE)
- FAILED cases có Expected + Actual + Severity
- NOT TESTABLE cases giải thích rõ thiếu gì
- SUMMARY có enough number liệu: Total/Passed/Failed/Not Testable/Pass Rate

## Format (đúng cấu trúc no)
- Đúng 6 sections: PASSED / FAILED / NOT TESTABLE / SUMMARY / FIXES REQUIRED / FLUTTER TEST CODE
- FIXES REQUIRED liệt kê cụ can, no chung chung
- Severity use BLOCKER/CRITICAL/MAJOR/MINOR

## Quality (sâu and hữu ích no)
- Verdict dựa trên so sánh expected vs actual, no suy đoán
- Severity phản ánh business impact thực sự
- FIXES REQUIRED enough cụ can to Dev biết do gì ngay
- No viết test cases new outside test plan

## Flutter Test Code (tính into Quality score)
- Có cả widget test lẫn integration test
- Mỗi test method có Arrange / Act / Assert tường minh
- Use đúng flutter_test API: pumpWidget, tap, pump, expect, find.*
- Cover tất cả TC P0 + P1 in test plan
- Có comment ghi TC-ID tương ứng
- Đề xuất widget keys need thêm if implementation thiếu
- Code compile is — none bug syntax, import đúng package
