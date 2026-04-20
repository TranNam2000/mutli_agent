# Criteria: Business Analyst (BA)
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.45 format=0.15 quality=0.40

## Completeness (có enough no)
- [ ] Mô tả rõ CẦN LÀM GÌ (no phải cách do)
- [ ] Có điều kiện hoàn thành đo lường is (no phải "hoạt động tốt")
- [ ] Scope lớn (>1 màn): có danh sách tính năng + rủi ro + user
- [ ] Scope nhỏ (1 màn): có acceptance criteria dạng GIVEN/WHEN/THEN or tương đương
- [ ] MISSING_INFO ghi rõ if still thiếu — or none MISSING_INFO nào

## Format (đúng cấu trúc no)
- [ ] Ngôn ngữ đơn giản, no use jargon (PRD, MoSCoW, KPI)
- [ ] Màn đơn ≤ 1 trang, tính năng lớn ≤ 2 trang
- [ ] Phân cấp rõ (heading → bullet, no flat text)

## Quality (sâu and hữu ích no)
- [ ] Acceptance criteria enough cụ can: Dev đọc done biết "done" nghĩa is gì
- [ ] No có assumption ẩn ("user will tự hiểu", "như thông thường")
- [ ] Rủi ro if có: ghi cả mitigation, no chỉ liệt kê
- [ ] No use câu mơ hồ ("hoạt động bình thường", "per request")
