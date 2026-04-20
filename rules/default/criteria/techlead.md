# Criteria: Tech Lead
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.40 format=0.20 quality=0.40

## Scale before when chấm
- **Simple** (1 màn, none API): chỉ use Simple checklist
- **Medium** (có state phức tạp or local DB): Simple + Medium
- **Full** (có API / nhiều tính năng): tất cả

## Completeness (có enough no)
- [ ] Folder structure and file need tạo
- [ ] State management approach with lý do (StatefulWidget / Cubit)
- [ ] Technical tasks có estimate hour
- [ ] [Medium+] Data model / local schema
- [ ] [Medium+] Technical risks + mitigation
- [ ] [Full] API endpoints có method + path + request/response
- [ ] [Full] Security strategy (auth, token storage)
- [ ] [Full] Database schema có relationships

## Format (đúng cấu trúc no)
- [ ] Folder structure is tree thật (no mô tả văn xuôi)
- [ ] Technical tasks có ID + estimate hour
- [ ] [Full] API format: METHOD /path + request/response bodies

## Quality (sâu and hữu ích no)
- [ ] Tech stack có lý do forose, no chỉ liệt kê
- [ ] Estimate có breakdown, no ghi chung "2-3 day"
- [ ] Dev đọc done biết bắt đầu file nào, viết gì
- [ ] No over-engineer (no use Clean Architecture for màn đơn giản none API)
