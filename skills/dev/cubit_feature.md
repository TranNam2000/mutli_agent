---
SCOPE: feature, module
TRIGGERS: cubit, bloc, api, feature, flow, state, offline, repository
MAX_TOKENS: 7000
---

# Dev Skill — Cubit/BLoC Feature

Implement 1 feature with Cubit/BLoC + Repository. Follow folder structure TechLead  chỉ định.

## Output bắt buộc

Mỗi file có header:
```dart
// lib/features/<feature>/<layer>/<file>.dart
```

### Thứ tự viết
1. **Entity** — pure Dart, immutable, `copyWith`
2. **Repository interface** — domain layer
3. **Data model + mappers** — `fromJson`, `toEntity`
4. **Remote datasource** — dio calls, retry logic
5. **Local datasource** — Hive/secure storage if có
6. **Repository impl** — glue remote + local + error mapping
7. **Cubit/BLoC + states** — sealed class states
8. **UI pages + widgets** — BlocBuilder/BlocListener
9. **DI registration** — register in `injection_container.dart`

### State definition
```dart
sealed class FeatureState extends Equatable {}
class FeatureInitial extends FeatureState {}
class FeatureLoading extends FeatureState {}
class FeatureLoaded extends FeatureState {
  final List<Entity> items;
  final bool hasMore;
}
class FeatureError extends FeatureState {
  final AppError error;
}
```

### Error handling
- Network layer throws `AppException`
- Repository maps exception → `Either<AppError, T>` (dartz) or custom Result
- Cubit catch + emit error state with user-friendly message

### Widget keys for test
Luôn thêm:
- `Key('feature_<screen>')` for root widget
- `Key('loading_<screen>')`, `Key('error_<screen>')`, `Key('empty_<screen>')`
- `Key('item_<id>')` for list item
- `Key('<action>_btn')` for buttons

### Unit test BLoC
```dart
// test/features/<feature>/presentation/bloc/<feature>_cubit_test.dart
blocTest('loads items on Load event', …);
blocTest('emits error on network failure', …);
```

## KHÔNG
- No trộn logic into widget
- No gọi datasource thẳng from UI
- No catch  skip exception — luôn log
