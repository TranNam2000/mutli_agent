# Criteria: Developer
PASS_THRESHOLD: 8
WEIGHTS: completeness=0.30 format=0.20 quality=0.50

## Scale (determine before grading)
- **Simple** (StatefulWidget): only use Simple checklist
- **Medium** (Cubit): Simple + Medium
- **Full** (Clean Architecture): all levels

## Completeness (what must exist)
- [ ] UI code complete, runs without errors
- [ ] Loading / error / empty states handled
- [ ] No file listed in the plan is missing
- [ ] [Medium+] Cubit + states complete
- [ ] [Medium+] Error handling surfaces feedback to user
- [ ] [Full] All layers: entity → repo interface → repo impl → usecase → BLoC → UI
- [ ] [Full] Unit tests for BLoC/Cubit (happy path + error path)
- [ ] [Full] pubspec.yaml dependencies + DI / routes

## Format (structure)
- [ ] Code ordered: data → logic → UI
- [ ] No obvious syntax errors

## Quality (depth / usefulness)
- [ ] Code production-ready — no pseudo-code or TODO placeholders
- [ ] Loading/error/success states fully handled in UI
- [ ] No hardcoded strings / magic numbers
- [ ] No obvious memory leaks (StreamSubscription / controllers are disposed)
