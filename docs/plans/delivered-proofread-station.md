# 交付式校对台（打包交付 → 离线校对 → 回导）设计与缺口分析

> 起草：2026-07-22
> 状态：已立项（2026-07-22 决策：交付式前端=方案 B 独立轻量前端；桌面打包本期纳入）
> 背景：kimi + zai 已合并进 KZOCR，不存在外部 zai/kimi 平台。KZOCR 内部有两类校对台：
> ① 集成在系统里的校对台（操作员直接在系统内人工校对）；② 交付出去给校对人员的校对台
> （把书打包成便携校对包，校对人员在 Windows/macOS 环境离线校对，校对回来的数据包再导入系统）。

---

## 1. 现状核实（均经代码/测试验证）

### 1.1 数据层闭环函数（后台逻辑）— 已全部存在且有测试

| 函数 | 位置 | 作用 | 测试 |
|------|------|------|------|
| `push_book_to_zai` | `kzocr/doc/zai.py` | 把 `BookResult` 打包成可移植校对包 `custom.db`（schema 对齐 prisma 子集），并回写 BookDB | `tests/test_doc_import.py` `test_doc_push_loop.py` |
| `freeze_custom_db` | `kzocr/doc/freeze.py` | 冻结旧 `custom.db`（只读 `0440` + `.frozen` 标记），落实"旧库冻结只读" | `tests/test_doc_zai_logic.py` `test_doc_zai_coverage.py` |
| `import_proofread_package` | `kzocr/doc/proofread.py` | 读 `custom.db` 的 `Line.humanFinal` + `Proofread`，按层级键 `(pageNum,paraSeq,seqInPara)→(page_num,para_seq,line_seq)` 映射回写 BookDB（best-effort 归档 Postgres `LineCorrectionArchive`） | `tests/test_doc_import.py` `test_doc_pg_mock.py` |

### 1.2 站内校对台（集成式）— 基本就绪

- 后台：`kzocr/web/app.py` 35 条路由，基于 **BookDB**（系统 of record）。`/book/{code}/anomalies/{id}/resolve` 直接调 `BookDB.resolve_anomaly` → 站内校对**直写 BookDB**，不经过 custom.db。
- 前台/webui：22 个 Jinja2 服务端渲染模板（book / anomalies(+resolve) / divergences / quality / recipes / dashboard / workspace / monitor / pipeline / register 等）。**无独立 SPA**。
- 反馈闭环：`kzocr/scheduler/review_manifest.py` + `kzocr/cli_review.py`。

### 1.3 交付式校对台 — 仅完成数据层，前后台握手缺失

| 维度 | 状态 | 证据 |
|------|------|------|
| 后台·打包导出 | ✅ | `push_book_to_zai` 经 CLI `pipeline` 调用（`kzocr/cli.py:40,110`） |
| 后台·冻结旧包 | ✅ | `freeze_custom_db` |
| 后台·回导导入 | ⚠️ **有逻辑无入口** | `import_proofread_package` 存在且被测，但 **CLI 无 `import` 子命令**、`kzocr/web/app.py` 无任何路由调用它（grep 确认 web 完全不触 `custom.db`/`proofread`/`import`） |
| 前台·校对人员离线审 `custom.db` | ❌ **不存在** | web 仅绑 BookDB；无任何以 custom.db 为数据源的审查 UI |
| Windows/macOS 桌面打包 | ❌ **不存在** | 仓库无 PyInstaller spec / exe / dmg |

**结论**：交付式校对台的"打包能出、回导逻辑在"，但面向人的链路（离线审查前端 + 回导入口 + 桌面打包）全部缺失。站内校对台已就绪，无需改动。

---

## 2. 数据流（精确）

```
[系统内]  run_engine → BookResult
            │
            ├─ push_book_to_zai() ──→ custom.db（便携校对包，交校对方）
            │                       → 同时回写 BookDB（系统 of record）
            │
            ├─[站内校对]  kzocr web ──直写──→ BookDB（anomalies/resolve 等）
            │
            └─[交付式校对]
                ① 交付：把 custom.db 交给校对人员（Windows/macOS）
                ② 离线校对：校对人员改 custom.db 的 Line.humanFinal（+ Proofread 表）
                ③ 回导：校对回来的 custom.db → import_proofread_package()
                       读 humanFinal + Proofread，按层级键映射 → 回写 BookDB
                       （best-effort 归档 Postgres LineCorrectionArchive）
```

**关键事实**：custom.db 与 BookDB 是**两套不同 schema** 的库。站内 UI 直写 BookDB；交付式链路经 custom.db 中转再回导。web 当前只认 BookDB，**不认 custom.db**——这是"交付式前台缺失"的根本原因。

---

## 3. 三种前端形态取舍（核心决策）

### 方案 A：kzocr web 增加 custom.db「校对模式」（推荐首选）

让同一套 FastAPI + 现有 22 模板支持以 `custom.db` 为数据源的校对模式：启动 `kzocr web --db <custom.db> --mode proofread`，UI 读 custom.db 的 `Line.humanFinal` 供校对人员编辑，导出/冻结复用既有 `push_book_to_zai`/`freeze_custom_db`。

- 优点：复用率最高，无第二套代码库；跨平台（Python+FastAPI 原生支持 Windows/macOS）；与站内 UI 视觉一致。
- 缺点：需抽离 web 当前对 BookDB 的硬绑定（路由里 `BookDB(book_code, ...)` 大量直接构造），改为可切换数据源；模板需区分"操作员视图/校对员视图"权限。
- 工作量：中（数据源抽象 + 校对模式路由/模板分支）。

### 方案 B：独立轻量前端（SPA / 桌面窗口）

另起一个面向校对人员的轻量前端（如 React/Vue SPA 或 Tauri/Electron 桌面窗），后端复用 `kzocr/doc/*` 的导入导出函数，通过新 API 读写 custom.db。

- 优点：与操作员 UI 彻底解耦，可针对校对人员体验深度优化；桌面窗口天然满足 Windows/macOS 离线。
- 缺点：新增代码库与技术栈；需维护第二套前端；仓库当前**无 `package.json`、无前端构建链**，从零搭建成本高。
- 工作量：大。

### 方案 C：静态导出 + 极简阅读器

`export_book_markdown` 已有的基础上，额外导出"待校对 Markdown/HTML + 行级定位"，校对人员用通用编辑器改，回导时靠行号/锚点解析。

- 优点：极简，无需前端；兼容任意环境。
- 缺点：校对人员体验差（无结构化编辑、无差异高亮、回导解析脆弱）；与现有 custom.db + `import_proofread_package`（读 `humanFinal`）机制不兼容，需另写解析器。
- 工作量：小，但回导可靠性低，不推荐作主方案。

**取舍小结（2026-07-22 决策更新）**：交付式前端**定为方案 B（独立轻量前端）**——与站内 web 彻底解耦，可针对校对员体验深度优化，且桌面窗口天然满足 Windows/macOS 离线。方案 A 复用度虽高但会污染站内 UI 且需重抽数据源，已被否决；方案 C 回导解析脆弱不推荐。故**阶段 1 直接走方案 B**，不做 A→B 演进。

---

## 4. 效率与效果权衡与风格一致性

> 本节为 §3 前端形态取舍的支撑分析：拆解"效率"的两层含义，对比 SSR（复用 web）与轻量前端（SPA/桌面）的运行时与开发效率、用户体验差异，并说明两套界面观感能否一致。

### 4.1 "效率"的两层含义

| 维度 | SSR（服务端渲染，方案 A 复用 web） | 轻量前端（SPA/桌面，方案 B/C） |
|------|-----------------------------------|--------------------------------|
| **运行时效率** | 每次跳转 = 请求 + 服务器重渲染整页 HTML + 浏览器整页解析；带宽略大、服务器有渲染开销，浏览器几乎无 JS 负担 | 首屏需下载 JS 包（较重），之后交互仅拉 JSON + 局部更新 DOM，不整页刷新，后续跟手 |
| **开发效率** | 无前端工具链、无构建步骤、前后端同一种 Python、模板即改即生效；**明显更快更省** | 需引入 npm + 前端框架 + 打包构建 + 前端 CI + 状态管理，前后端两种语言；**搭建与维护成本明显更高** |

**对 KZOCR 的实际差异**：两类校对台都是"看书 / 审行 / 改字"的中低频交互，非高频实时操作。故**运行时效率两边差别不大**，真正的分野在**开发效率**（SSR 省）与**交互效果**（SPA 顺）。

### 4.2 效果（用户体验）差异

- **SSR**：整页刷新、交互偏"工程师风"；做内联编辑、实时差异高亮、离线这类顺滑交互较费劲。
- **轻量前端**：类原生 App 手感，可内联编辑、实时高亮、离线可用（PWA/桌面壳）；**对非技术校对员体验好得多**。

> 这正对应前面的判断：集成式（技术操作员）用 SSR 划算；交付式（非技术校对员、离线、Windows/macOS）才值得为"效果"投轻量前端。

### 4.3 风格能做到一致吗

**能，且观感与"谁渲染"基本无关**——两边最终都是浏览器里的 HTML + CSS，视觉由 CSS 决定，不由渲染架构决定。

保持一致的两类路径：

1. **共享同一套样式**：SSR 的 Jinja2 模板与 SPA 组件都引用同一份 CSS / 设计令牌（颜色、间距、字体），或共用同一 CSS 框架（Tailwind / Bootstrap），外观即可对齐。
2. **单一前端统管两端**（即 §3 方案 B）：直接做一个轻量前端同时服务集成式与交付式，**一份 UI 代码 = 绝对一致**，不存在漂移可能。

**唯一风险**：若交付式另起 SPA 又各自写一套 CSS，两边会慢慢"长歪"。故一致性要么靠**纪律共享样式**，要么干脆**统一成一套前端**。

### 4.4 本节结论

- 效率：SSR 赢在**开发效率**且运行时够用；轻量前端赢在**效果**，代价是构建维护成本。KZOCR 规模下运行时差异可忽略，决策关键在于"开发省不省"与"交互顺不顺"。
- 风格：一致性不是障碍——无论复用 SSR（§3 方案 A）还是另起轻量前端（方案 B/C），只要约定共用 CSS，观感都能对齐；想要零漂移则走方案 B 单一前端。

---

## 5. 桌面打包（Windows/macOS）

无论选 A 或 B，交付给校对人员都需"双击即用"形态：

- 工具：`PyInstaller` 打包 `kzocr web`（方案 A）或独立前端（方案 B）为单目录/单文件 exe / app。
- 注意点：
  - 依赖：`PyMuPDF`/`numpy` 需随包；真实引擎（PaddleOCR/torch）**不应**打入交付包（校对人员只需审 text，不需重跑 OCR）——交付包应是"纯校对"最小依赖。
  - 数据源：默认指向随包附带的 `custom.db`，或启动参数 `--db`。
  - 回导：交付包需预留"导出回导包"动作（把改完的 custom.db 交回操作员，由操作员侧 `import` 入 BookDB）。
- 工作量：小（方案 A 下主要是 PyInstaller spec + CI 交叉构建）。

> **2026-07-22 决策：桌面打包纳入本期范围**（原阶段 2 提前，与阶段 1 并行推进）。交付包技术形态随方案 B 确定为「方案 B 独立前端 → 桌面壳（Tauri/Electron 或 PyInstaller 包裹前端）」，最小依赖排除 torch/PaddleOCR/GLM。

---

## 6. 回导入口补齐（最小必需，独立于前端选型）

即使先做方案 A，`import_proofread_package` 也必须有用户入口，否则"校对回来再导入系统"无法落地：

1. **CLI**：新增 `kzocr import <custom.db> [--book-code X]` → 调 `import_proofread_package`。
2. **web 路由**：新增 `POST /import`（上传 custom.db）→ 调 `import_proofread_package` → 提示导入行数；导入前先 `freeze_custom_db` 旧包保护。
3. 测试：扩展 `tests/test_doc_import.py` 覆盖 CLI/web 入口（mock 文件系统）。

**安全要求（评审 security，必须随阶段 0 落地）**：校对回来的 `custom.db` 来自外部人员，属不可信输入：
- 先用**只读连接**打开校验 schema 与行数上限，拒绝畸形/超大库后再导入；
- `--db`/上传路径限制在允许的 `KZOCR_DB_DIR` 内，禁止任意路径（防穿越）；
- `humanFinal` 视为不可信文本，渲染依赖 Jinja2 默认 autoescape（确认开启），防 XSS；
- 交付模式**强制 `register_postgres=False`**（best-effort 静默跳过已具备，改为默认关闭而非"建议"）。

**双写权威性规则（评审 architect，必须明确）**：站内校对（`BookDB.resolve_anomaly`）与交付式（`custom.db.humanFinal` → 回导）是两条修正写入路径。定义冲突优先级——**回导为权威覆盖**：同一行的 `humanFinal` 回导结果覆盖 BookDB 既有终校值（因校对员见到的是最新交付包）；站内实时修改不阻塞回导，回导后以包为准。如需保留站内修改，按 `updated_at` 时间戳取较新者合并（二期再议，一期先覆盖）。

---

## 7. 推荐落地路线（分阶段，已据 2026-07-22 决策调整）

- **阶段 0（必须，最小闭环，独立立项先行）**：补齐回导入口——CLI `kzocr import` + web 上传路由（§6），含其安全要求与双写权威性规则。**阶段 0 与前端形态无关，应最先落地、最先受益。**
- **阶段 1（前端=方案 B）**：独立轻量前端（SPA / 桌面窗），后端复用 `kzocr/doc/*`（`push_book_to_zai`/`import_proofread_package`/`freeze_custom_db`），通过新 API 读写 custom.db；与操作员 UI 彻底解耦。**走新路径，不经 `kzocr/adapter/to_zai_prisma.py` 兼容壳。**（原方案 A「抽 ReviewDataSource 双适配器」路线已撤销——前端已定 B，不再改造站内 web。）
- **阶段 2（本期，桌面打包）**：将阶段 1 的独立前端打包为 Windows/macOS 双击即用形态（最小依赖 FastAPI + PyMuPDF + numpy，排除 torch/PaddleOCR/GLM），预留"导出回导包"动作交回操作员侧 `import`。
- （原阶段 3「演进到方案 B」条款撤销——前端已定 B，无演进分支。）

---

## 8. 风险与开放问题

- **web 数据源硬绑定（评审 architect，高优先级）**：当前 `kzocr/web/app.py` 大量 `BookDB(book_code, db_dir=_db_dir())` 直构。方案 A 落地**必须先抽 `ReviewDataSource` 协议 + `BookDBAdapter`/`CustomDBAdapter` 两个实现**，再让路由依赖协议；否则加 `if mode` 分支会迅速腐化。新代码直接走 `kzocr/doc/*`，不经 `kzocr/adapter/to_zai_prisma.py` 兼容壳。
- **schema 一致性 / 双写冲突**：custom.db 的 `Line.humanFinal` 是回导唯一信任字段；站内校对走 BookDB 的 `resolve_anomaly`。已定义**回导为权威覆盖**规则（§6），冲突时以交付包 `humanFinal` 为准。
- **权限/视图区分**：方案 A 下同一套 UI 服务操作员与校对员，需区分"可改 final"与"仅改 humanFinal"的视图与写权限。
- **Postgres 归档**：交付模式已强制 `register_postgres=False`（§6）。
- **校对上下文缺失（评审 domain，提升为必需）**：custom.db 的 `Line` 仅有文本，校对员改 `humanFinal` 时看不到为何被标记。阶段 1 起应在 custom.db 携带 `disputed`/`auditSource`/severity 及可选原图裁剪 `crop_img`（复用 `engine/run.py` 版心裁剪 + `char_boxes`），并支持字符级校正（用 `charLevelJson`）。

---

## 9. 决策点（2026-07-22 已全部拍板）

1. 前端形态：**已决策 = 方案 B（独立轻量前端）**。评审原建议 A 为主，经权衡改为 B——与站内 web 解耦、针对校对员体验、天然离线、无 web 数据源腐化风险。
2. 阶段 0（回导入口）独立立项：**已确认**，与前端形态无关，最先落地（已写入 §7）。
3. 桌面打包是否本期范围：**已决策 = 本期纳入**（阶段 2 提前，与阶段 1 并行）。
4. 校对人员"差异高亮 / 原图回溯"增强：**评审建议提升为必需（已写入 §8），非可选**——阶段 1 起落实。

---

## 10. 安全与数据完整性（多角色评审修订，round 1）

本节汇总评审 security 角色的发现，作为阶段 0/1 的强制约束：

- **不可信包导入**：外部校对员返回的 `custom.db` 视为不可信。导入前用只读连接校验 schema + 行数上限；拒绝畸形/超大库；`humanFinal` 经 Jinja2 autoescape 防 XSS。
- **路径安全**：`--db` 与上传路由限制 `custom.db` 落在 `KZOCR_DB_DIR` 内，禁任意路径。
- **依赖最小化**：桌面交付包仅含"纯校对"最小依赖（FastAPI + PyMuPDF + numpy），**排除** torch/PaddleOCR/GLM 等引擎依赖（缩体积、缩攻击面）。
- **离线无联网**：交付包运行时无网络依赖；纯校对路径不触发 egress 校验。
- **`.frozen` 仅为防误覆盖软约定**，非安全边界，文档须注明。

---

## 11. 修订记录

- 2026-07-22 round 1 多角色评审（`docs/reviews/2026-07-22-round1-delivered-proofread-station.md`）后修订：
  - §6 增补回导安全要求（只读校验 / 路径限制 / autoescape / 强制 `register_postgres=False`）+ 双写权威性规则（回导覆盖）。
  - §7 明确阶段 0 独立于前端形态单独立项、阶段 1 先抽 `ReviewDataSource` 协议 + 双适配器（禁散点 `if mode`）。
  - §8 升级 web 数据源抽象为协议先行、校对上下文（原图/分歧原因/字符级）提升为必需。
  - 新增 §10 安全与数据完整性汇总。
- 2026-07-22 决策（用户拍板，立项）：
  - 前端形态定为 **方案 B（独立轻量前端）**：与站内 web 解耦；原方案 A「抽 ReviewDataSource 双适配器改造站内 web」路线撤销。
  - **桌面打包纳入本期范围**（阶段 2 提前，与阶段 1 并行）；交付包形态随 B 定为「方案 B 前端 → 桌面壳」，最小依赖排除 torch/PaddleOCR/GLM。
  - 阶段 0（回导入口）独立立项确认；§7 路线据上述调整重排。
  - 待决项全部关闭，设计稿状态由「设计待评审」改为「已立项」。
