---
SCOPE: feature, module, full_app
TRIGGERS: dashboard, admin panel, analytics, report, metric, kpi, chart, graph, table, filter, export, csv, excel, bi, report dashboard, internal tool
MAX_TOKENS: 4000
---

# BA Skill — SaaS Dashboard / Admin Panel

Sản phẩm hiển thị data đã có. KHÔNG phải tạo data — chỉ visualise + filter + export.

## Output bắt buộc

### 1. Data sources
- Bảng/API nào cung cấp data
- Refresh frequency: real-time | hourly | daily
- Filter dimension: time range, user, region, ...
- Aggregation: count, sum, avg, percentile

### 2. Widget catalog
Mỗi widget:
- Type: KPI card | line chart | bar chart | table | funnel | heatmap
- Data source + filter
- Drill-down behaviour (click → detail page?)
- Export option (CSV/Excel/PNG)

### 3. Permission
- Ai xem được dashboard nào?
- Filter user-level: nhân viên chỉ xem org của mình
- Admin xem tất cả

### 4. Acceptance criteria
- Time-to-first-paint < 3s với 10k row
- Filter response < 500ms
- Export CSV xử lý đúng row >100k (streaming)
- Empty state có message rõ ràng

## KHÔNG
- Đừng đề xuất real-time WebSocket nếu daily refresh là đủ
- Đừng add filter "vì có thể có người cần" — chỉ những filter có user story rõ

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Write` to save requirements into `docs/requirements/<feature>.md` and the task list into `.multi_agent/tasks/<feature>.md`. Echo `## TASK-N | ...` blocks in the reply.
