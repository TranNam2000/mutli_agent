---
SCOPE: simple, feature, module, full_app, bug_fix
TRIGGERS: làm gì đó, tùy bạn, không biết bắt đầu, ko biet bat dau, help me decide, what should i do, hướng dẫn, huong dan, không rõ, khong ro, mơ hồ, mo ho, vague, ambiguous
STEPS: 
AGENTS: (none — PM hỏi user trực tiếp)
RESPONSE_FORMAT: clarification_questions
MAX_TOKENS: 1200
---

# PM Skill — Clarify First (gate hỏi user)

User request **mơ hồ / thiếu thông tin / có nhiều cách hiểu** → PM hỏi
trước khi dispatch agent. Không tốn LLM call cho BA/Dev/QA cho đến khi
biết rõ user muốn gì.

## Khi PM nên pick mode này

Trigger ngầm:
- Confidence classify < 0.6
- Request quá ngắn (< 5 từ) hoặc quá generic ("làm app", "build website")
- Có ≥ 2 cách hiểu khả dĩ (ví dụ "fix login" — fix UI? fix backend?)
- Cần quyết định chiến lược (Stripe vs VNPay, native vs Flutter, SSR vs SSG)
- Có rủi ro lớn cần user xác nhận (DB migration, breaking change)

## Routing
- **STEPS: (empty)** — không gọi agent nào
- PM tự hỏi user, đợi reply, **re-classify** sau khi có thông tin

## Trả lời user (ASK, not ANSWER)

Format:
```
🤔 PM cần làm rõ trước khi bắt đầu

Tôi hiểu task của bạn là: <paraphrase>
Nhưng có vài chỗ không chắc — chọn giúp:

1. <Question A>
   a) <option>  b) <option>  c) <option>

2. <Question B>
   - <free-text answer>

3. <Question C — Y/N>

Trả lời theo format `1.b 2.<answer> 3.Y`, hoặc viết tự do.
```

### Câu hỏi mẫu cho từng nhóm task

**Code feature mơ hồ:**
- Stack? (Flutter / React Native / Native iOS+Android / Web)
- Audience? (B2C consumer / B2B internal / both)
- Có backend API riêng hay dùng BaaS (Firebase/Supabase)?

**Bug fix mơ hồ:**
- Bug ở UI hay logic hay network?
- Có repro step / log / stack trace không?
- Đã có test case fail chưa?

**UI tweak mơ hồ:**
- Đổi 1 màn hay system-wide (theme)?
- Đã có Figma/Sketch chưa?

**Doc mơ hồ:**
- Audience? (dev / PM / customer / regulator)
- Format? (markdown trong repo / Confluence / PDF)
- Granularity? (overview / API ref / step-by-step tutorial)

## Sau khi user trả lời
- PM nhận thêm context → re-run classify với enriched request
- Nhảy sang skill phù hợp (code_feature, code_bugfix, documentation, ...)
- Nếu user vẫn lửng lơ → hỏi tiếp 1 vòng (max 2 vòng), rồi pick `default` skill và để LLM tự xoay

## KHÔNG
- Đừng hỏi quá 4 câu mỗi vòng (overwhelm user)
- Đừng hỏi thông tin technical mà BA/TechLead có thể tự research (don't outsource thinking)
- Đừng pick mode này nếu request đã rõ → bị thừa, gây delay
