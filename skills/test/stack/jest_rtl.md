---
SCOPE: feature, module
TRIGGERS: jest, react testing library, rtl, unit test, react native test, nextjs test
MAX_TOKENS: 5000
---

# QA Skill — Jest + React Testing Library (RN / Next.js)

Test for React Native or Next.js feature. KHÔNG use Patrol/Flutter test.

## Test stack
- Unit/Component: **Jest + @testing-library/react-native** (RN) or **@testing-library/react** (Next.js)
- Integration: **Playwright** (Next.js) or **Detox / Maestro** (RN)
- Mocking: `jest.mock` for API, `msw` for HTTP

## Output format

### Test Plan
Table TC-ID | Title | Given | When | Then | Priority | Type (unit/component/integration/e2e)

### Component Test (RTL)
```tsx
// __tests__/LoginScreen.test.tsx
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import LoginScreen from '@/features/auth/screens/LoginScreen';

describe('LoginScreen', () => {
  it('TC-001: submits with valid credentials', async () => {
    const { getByTestId, findByText } = render(<LoginScreen />);
    fireEvent.changeText(getByTestId('login_email_input'), 'user@test.com');
    fireEvent.changeText(getByTestId('login_password_input'), 'Pass123!');
    fireEvent.press(getByTestId('login_submit'));
    expect(await findByText('Welcome')).toBeTruthy();
  });

  it('TC-002: shows error on network failure', async () => {
    // mock fetch to fail
    …
  });
});
```

### Server Action Test (Next.js)
```ts
// __tests__/actions/createUser.test.ts
import { createUser } from '@/app/users/actions';

describe('createUser server action', () => {
  it('TC-003: rejects invalid email', async () => {
    const formData = new FormData();
    formData.set('email', 'not-an-email');
    const result = await createUser({}, formData);
    expect(result.error).toContain('invalid email');
  });
});
```

### E2E (Playwright for Next.js)
```ts
// e2e/<feature>.spec.ts
import { test, expect } from '@playwright/test';

test('TC-010: user can checkout', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('product_123').click();
  await page.getByTestId('add_to_cart').click();
  await expect(page.getByTestId('cart_count')).toHaveText('1');
});
```

### E2E (Maestro for RN)
Unchanged như skill test/feature_tests.md, flows YAML in `maestro/`.

## Coverage targets
- P0 features: 80% branch coverage
- P1: 60% statement coverage
- P2: happy path only
- Snapshot tests CHỈ for stable UI components

## Exit criteria
- `npm test` pass 100%
- E2E critical flows pass
- không có test skip/only lẫn in commit

## KHÔNG
- No test implementation detail (test behavior)
- No use `findBy*` when không cần async
- No snapshot component có data động (timestamp, random…)

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Edit` / `Write` to put test code directly into the project (`test/`, `integration_test/`, `__tests__/` — mirror the existing layout). Run the suite via `Bash` once and include pass/fail counts. Reply = summary only.
