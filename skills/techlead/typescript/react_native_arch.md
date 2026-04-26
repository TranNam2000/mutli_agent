---
SCOPE: feature, module, full_app
TRIGGERS: react native, rn, expo, mobile cross-platform, ios android js, react native app
MAX_TOKENS: 4000
---

# TechLead Skill — React Native Architecture

## Output bắt buộc

### 1. Folder structure
```
src/
  features/<name>/
    components/
    hooks/
    api/
    store/
    screens/
    index.ts
  shared/{ui, hooks, utils, types}/
  navigation/{stacks, tabs}/
  config/
App.tsx
```

### 2. Stack baseline
- Bundler: Expo (managed) | bare RN (chọn lý do rõ)
- Navigation: React Navigation 7 (native-stack)
- State: Zustand cho local, React Query cho server-state
- Storage: MMKV (đồng bộ, nhanh hơn AsyncStorage)
- Forms: react-hook-form + zod

### 3. Native modules
- Liệt kê từng module native cần (camera, biometric, push, ...)
- Thư viện: Expo SDK / community library + version
- iOS/Android setup cần thiết (Info.plist, AndroidManifest.xml entries)

### 4. Performance
- FlatList virtualisation (estimatedItemSize, getItemLayout)
- Reanimated 3 cho animation chạy trên UI thread
- Image cache (expo-image / FastImage)
- Bundle splitting (Hermes engine on)

### 5. Testing
- Jest + React Native Testing Library cho component
- Detox cho E2E (iOS/Android)
- EAS Build + EAS Submit cho CI/CD

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual codebase first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo task assignment + sprint plan in the reply.
