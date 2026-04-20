# Criteria: Developer
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.30 format=0.20 quality=0.50

## Scale before when chấm
- **Simple** (StatefulWidget): chỉ use Simple checklist
- **Medium** (Cubit): Simple + Medium
- **Full** (Clean Architecture): tất cả

## Completeness (có enough no)
- [ ] UI code đầy enough, chạy is
- [ ] Loading / error / empty states is handle
- [ ] No thiếu file nào đề cập in plan
- [ ] [Medium+] Cubit + states đầy enough
- [ ] [Medium+] Error handling có feedback for user
- [ ] [Full] Đủ layers: entity → repo interface → repo impl → usecase → BLoC → UI
- [ ] [Full] Unit tests for BLoC/Cubit (happy path + error path)
- [ ] [Full] pubspec.yaml dependencies + DI / routes

## Format (đúng cấu trúc no)
- [ ] Mỗi file có comment đầu: `// lib/path/to/file.dart`
- [ ] Code per thứ tự logic: data → logic → UI
- [ ] No có syntax error rõ ràng

## Quality (sâu and hữu ích no)
- [ ] Code production-ready, no phải pseudo-code or TODO placeholder
- [ ] Loading/error/success states handle enough in UI
- [ ] No có hardcoded string / magic number
- [ ] No có memory leak rõ ràng (StreamSubscription / controller is dispose)
