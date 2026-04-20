---
SCOPE: feature, module
TRIGGERS: react native, rn, expo, reactnative, mobile react
MAX_TOKENS: 7000
---

# Dev Skill — React Native Feature

Implement 1 feature in React Native app with TypeScript + hooks + Zustand/Redux.

## Folder structure
```
src/
  features/<feature>/
    api/<feature>Api.ts          # axios/fetch wrapper
    hooks/use<Feature>.ts        # business logic hook
    store/<feature>Slice.ts      # zustand slice
    components/<Widget>.tsx
    screens/<Screen>.tsx
    types.ts
```

## Convention
- Mỗi file có comment header: `// src/features/<feature>/<file>.ts`
- Hooks return shape: `{ data, loading, error, refetch }`
- API layer trả `Result<T, ApiError>` (neverthrow or tự type)
- State machine rõ ràng: `idle | loading | success | error`

## Testing anforrs (QA will use)
- Mỗi touchable: `testID="<feature>_<action>"`  VD: `testID="login_submit"`
- Mỗi input: `testID="<feature>_<field>_input"`
- Error containers: `testID="<feature>_error"`
- List items: `testID="<feature>_item_<index>"`

## Error handling
- Global error boundary component
- API error → toast + retry button
- Offline → check `NetInfo` before request, show banner

## Performance
- FlatList with `keyExtractor` + `getItemLayout` when list dài
- `useMemo` for derived data
- Image cache with `expo-image` or `react-native-fast-image`
- Avoid inline arrow functions in list renderItem

## KHÔNG
- No use class component (functional + hooks only)
- No direct fetch in component — qua hook
- No commit `console.log` production
