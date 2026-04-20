---
SCOPE: feature, module, full_app
TRIGGERS: react native, rn, expo, react native architecture
MAX_TOKENS: 7000
---

# TechLead Skill — React Native Architecture

Architecture for RN app with Expo or bare workflow.

## Tech stack recommendation
- TypeScript 5+ strict mode
- Expo SDK (unless native-heavy) — `expo-router` for file-based navigation
- State: **Zustand** (simple apps) or **Redux Toolkit + RTK Query** (complex)
- HTTP: **axios** + retry interceptor
- Storage: **react-native-mmkv** (fast) or AsyncStorage (simple)
- Validation: **zod**
- Forms: **react-hook-form**
- UI: **tamagui** or **gluestack-ui** or custom
- i18n: **i18next + react-i18next**

## Folder structure
```
src/
  features/<module>/
    api/, hooks/, store/, components/, screens/, types.ts
  core/
    network/, storage/, theme/, i18n/, errors/, logging/
  shared/
    components/, hooks/, utils/
  navigation/
    AppNavigator.tsx, types.ts
App.tsx / app/_layout.tsx  (expo-router)
```

## Module boundaries
- Mỗi module export qua `<module>/index.ts` barrel
- No import cross-module trừ qua shared event emitter
- Shared types in `shared/types/`

## API layer
```
┌─────────────────┐
│  client.ts      │ axios instance + interceptors
│  errors.ts      │ ApiError class, error mapping
│  retry.ts       │ exponential backoff
└────────┬────────┘
         ↓
┌─────────────────┐
│  <feature>Api.ts│ endpoints with type-safe input/output
└─────────────────┘
```

## Testing strategy
- Unit: 70% coverage for hooks + utils
- Component: snapshot + interaction
- E2E: Maestro YAML flows
- Performance: flipper / perf-monitor

## CI/CD (EAS or custom)
- PR: lint + typecheck + unit test
- Main: build preview + e2e Maestro smoke
- Release: EAS submit to stores or CodePush OTA

## Security
- Secure storage for token (`expo-secure-store` or `react-native-keychain`)
- Biometric gating (`expo-local-authentication`)
- Cert pinning qua `react-native-ssl-pinning` (if backend private)
- No API keys in JS bundle — use env + EAS secrets

## Performance targets
- App start < 3s cold
- Navigation transition < 200ms
- List scroll 60 FPS (check with Flipper)
- Bundle < 25MB compressed

## KHÔNG
- No over-engineer (RN no giống Flutter — tránh copy Clean Arch 1-1)
- No mix Expo + bare unless bắt buộc
- No skip type checking
