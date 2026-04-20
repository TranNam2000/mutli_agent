# Criteria: QA Reviewer
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.35 format=0.20 quality=0.45

## Completeness (what must exist)
- Every TC in the test plan has a verdict (PASSED/FAILED/NOT TESTABLE)
- FAILED cases include Expected + Actual + Severity
- NOT TESTABLE cases explain what is missing
- SUMMARY contains: Total / Passed / Failed / Not Testable / Pass Rate

## Format (structure)
- Exactly 6 sections: PASSED / FAILED / NOT TESTABLE / SUMMARY / FIXES REQUIRED / FLUTTER TEST CODE
- FIXES REQUIRED is concrete, not generic
- Severity uses BLOCKER / CRITICAL / MAJOR / MINOR

## Quality (depth / usefulness)
- Verdict is based on expected vs actual, not guessing
- Severity reflects real business impact
- FIXES REQUIRED specific enough that Dev knows exactly what to do
- No new test cases invented outside the test plan

## Flutter Test Code (counts toward Quality score)
- Includes both widget test and integration test
- Each test has explicit Arrange / Act / Assert
- Uses correct flutter_test API: pumpWidget, tap, pump, expect, find.*
- Covers every P0 + P1 TC in the test plan
- Has comment noting the corresponding TC-ID
- Proposes widget keys to add when implementation is missing any
- Code compiles — no syntax errors, imports correct
