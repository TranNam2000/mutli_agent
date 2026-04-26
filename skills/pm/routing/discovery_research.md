---
SCOPE: feature, module, full_app
TRIGGERS: discovery, research, user research, persona, user journey, jtbd, jobs to be done, validate idea, problem statement, customer interview, survey, focus group, market research, competitor analysis, đối thủ, doi thu, nghiên cứu, nghien cuu
STEPS: ba
AGENTS: BA(discovery skill)
RESPONSE_FORMAT: discovery_report
MAX_TOKENS: 1500
---

# PM Skill — Discovery / User Research

User chưa biết user thật sự cần gì → cần discovery TRƯỚC khi viết requirement.

## Routing
- **STEPS: ba**  — chỉ BA, BA sẽ tự pick `discovery` skill
- BA chạy discovery skill: persona, user journey, JTBD, validation questions
- KHÔNG gọi Designer/TechLead/Dev/Test (chưa tới phase build)

## Trả lời user
Format:
1. **Problem statement** — paragraph đúc kết
2. **Personas** — 2-3 persona (giả định, cần verify)
3. **User journey** — bước → friction → goal
4. **JTBD list** — 3-5 jobs ("Khi X, tôi muốn Y, để Z")
5. **Validation questions** — 5-7 câu hỏi cho user phỏng vấn
6. **Next step** — phỏng vấn N user, survey, hoặc data analysis trước khi vào feature build

## KHÔNG
- Đừng đề xuất tech stack
- Đừng list features (chưa tới đó)
- Đừng auto chuyển sang `code_feature` — đợi user xác nhận discovery findings
