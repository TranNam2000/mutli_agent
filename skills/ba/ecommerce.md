---
SCOPE: simple, feature, module, full_app
TRIGGERS: ecommerce, e-commerce, shopping, cart, checkout, product, catalog, inventory, order, sku, variant, promotion, promo, coupon, voucher, shipping, fulfillment, refund, return, stripe, vnpay, momo, marketplace, storefront, add to cart
MAX_TOKENS: 5000
---

# BA Skill — E-commerce domain

Specialized BA skill for any request that touches product catalog, cart,
checkout, payment, order fulfillment, or post-purchase flows. Use the
shared task_based output format, but apply the domain rules below.

## Domain-specific metadata defaults

- If task touches checkout/payment → `impact_area` must include both
  `"payment"` and `"auth"`, `risk_level=high`.
- If task touches inventory/stock → add `"database"` to `impact_area`
  (concurrent write safety matters).
- If task touches promotion/coupon → add `"pricing"` to `impact_area`
  (edge cases around stacking / expiry).

## Mandatory AC coverage

For every e-commerce task, the AC list MUST include at least one item
from each of these categories (skip any that genuinely don't apply):

1. **Happy path** — standard buyer flow succeeds.
2. **Edge quantity** — zero stock, partial stock, oversell guard.
3. **Promotion interaction** — task + applicable coupon combo.
4. **Payment failure recovery** — network retry, declined card, 3DS.
5. **Multi-currency / locale** — price format + currency symbol.
6. **Analytics event** — which event name fires, which properties.

Shorten if the task is narrower (e.g. an admin-only edit won't need
analytics), but do not silently drop entire categories.

## Red flags to surface as MISSING_INFO

- Missing refund/return policy for paid products.
- No inventory reservation strategy (can oversell).
- Unspecified tax/VAT behaviour.
- Cart persistence (anonymous → logged-in merge) undefined.
- Checkout timeout / session expiry behaviour.

## Common task breakdown templates

When the request is "add checkout", expand into at minimum:
- Cart validation & reservation
- Address + shipping rate calculation
- Payment intent creation (Stripe/VNPay/MoMo)
- 3DS / OTP challenge handling
- Order persistence + email notification
- Analytics `purchase` event with correct revenue value

Each is its own TASK-XXX with explicit `Dependencies:` wiring.

## Compliance notes

Gently surface — don't block on — these compliance hooks:
- PCI-DSS scope reduction (never store raw card numbers).
- GDPR: buyer consent for marketing opt-in on signup/checkout.
- Local tax rules: VAT for EU, GST for AU/NZ, SST for MY/VN.

If unclear, add `MISSING_INFO: compliance scope for country X`.
