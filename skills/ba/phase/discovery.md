---
SCOPE: feature, module, full_app
TRIGGERS: discovery, research, user research, persona, journey, interview, survey, problem statement, jobs to be done, jtbd, validate idea, pain point, why
MAX_TOKENS: 4000
---

# BA Skill — Discovery / User Research

Khi yêu cầu CHƯA RÕ — user nói "tôi muốn app cho X" mà không biết X cần gì. BA chạy discovery TRƯỚC khi viết requirement.

## Output bắt buộc

### 1. Problem statement (1 paragraph)
- **Who** đau (user persona — nghề nghiệp, context)
- **What** pain (cụ thể, có thể đo)
- **Why** chưa có giải pháp tốt
- **When** pain xảy ra (frequency, severity)

### 2. User personas (2-3 personas)
Mỗi persona:
- Tên fake + 1-line backstory
- Goal khi dùng app
- Frustration hiện tại
- Tech savviness (low/med/high)

### 3. User journey
Bước 1 → Bước 2 → ... → Goal đạt được. Mỗi bước:
- Action user làm
- System response
- Friction point (nếu có)

### 4. Jobs To Be Done (JTBD)
"Khi <situation>, tôi muốn <motivation>, để <outcome>."
List 3-5 JTBD chính.

### 5. Validation questions cho user
5-7 câu hỏi BA cần hỏi user/stakeholder để verify hypothesis. Ví dụ:
- "Anh/chị làm gì khi gặp tình huống X hiện tại?"
- "Tần suất việc này xảy ra?"
- "Nếu giải pháp X tồn tại, anh/chị có đổi quy trình không?"

### 6. NEXT step
- Thông tin còn thiếu
- Recommended: phỏng vấn N user X | survey | data analysis | competitor research

## KHÔNG
- Đừng nhảy thẳng vào features list (đây là discovery, không phải requirement)
- Đừng tự bịa persona — note rõ "giả định, cần verify"
- Đừng đề xuất tech stack (chưa tới phase đó)

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Write` to save requirements into `docs/requirements/<feature>.md` and the task list into `.multi_agent/tasks/<feature>.md`. Echo `## TASK-N | ...` blocks in the reply.
