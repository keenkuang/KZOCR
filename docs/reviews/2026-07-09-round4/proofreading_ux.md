# KZOCR 第 4 轮评审 — 人工校对体验（UX）专审

- **评审日期**：2026-07-09
- **评审角色**：人工校对体验（UX）评审专家
- **评审对象**：`docs/plans/ocr-engine-unification.md`（v0.2 · round3 修订版），重点 §1 HumanGate、§4.2 UNKNOWN、§5 人工兜底强化、§4.3 落库字段
- **对照**：round3 `summary.md`（I6/I7 + 校对 C1/C2/H1/H2/H3/H4/H5/M4/M5）、自稿 `round3/proofreading_ux.md`、代码 `adapter/to_zai_prisma.py`、`export_zai.py`、`engine/types.py`
- **评审范围**：仅调查与文档评审，未修改主方案或代码。

---

## 一、结论

**v0.2 在"文档层面"已对 round3 全部 5 个核心 UX 缺口作出明确回应**——尤其 C1（UNKNOWN 漏放）已真实补入 §5 触发条件，H4（闭环回填）从"无机制"升级为"命名机制 + 目标表"。从 round3 评审的视角，这是一次**文本层面的有效闭合**。

但必须区分两层：

1. **规划层（planning）闭合度：高**。§5 把 UNKNOWN 触发、原图裁剪、severity、聚合批量、`glyph_verified_reason` + `auditSource` 语义修正 全部点名列入，且 §6.5 给出 `CandidateSubmissionBatch` 回填管线。round3 提出的"文档缺口"在 v0.2 文案中已基本补齐。
2. **实现层（real closure）闭合度：低 / 仍开口**。落到当前代码（`to_zai_prisma.py` L29–63 DDL、`types.py` L30–144），**五个环节无一真正贯通**：`auditSource` 仍写 `book.engine_label`（L153，bug 未修）、`glyph_verified_reason`/`glyph_status`/`crop_img`/`Line.severity` 列在 `Line` 表与 `LineResult` 中均**不存在**、`Book` 表**无 `is_mock` 列**。

**核心判定**：v0.2 把 round3 的 UX 问题"写进了待办"（阶段 4/5），但**未在任何字段契约上定义落点**——多数要求停留在 §5 的意图陈述，缺少"字段加在哪张表、由谁计算、如何取图/取图存哪"的链路。对 UX 评审而言，属**"已规划、未闭合、且存在 2 处真实缺口需补"**。

逐项判定见下表，文末附新引入问题与假设裁决再确认。

| 环节 | round3 状态 | v0.2 规划层 | 当前代码层 | 真闭合？ |
|------|------------|------------|-----------|---------|
| 1. UNKNOWN 触发（C1/M5） | 漏列 | §5 已列入触发条件 ✓ | 仅文档，无代码 | 规划闭合 ✅ / 实现开口 ⚠️ |
| 2. 原图裁剪透传（C2） | 无图 | §5 要求 crop_img/路径+bbox ✓ | `LineResult` 无该字段、`Line` 表无图列、取图存储未定义 | 规划部分 ✓ / 存储链路断裂 ✗ |
| 3. severity 优先级（H1/M4） | 无分级 | §5 定义 critical/warning/info ✓ | `Line` 表无 severity 列（仅 Proofread 有）；毒性 critical 触发机制未定义 | 规划部分 ✓ / 落点缺失 ✗ |
| 4. 错字聚合+批量（H2/H4） | 无/口头 | §5 group-by + §6.5 `CandidateSubmissionBatch` 管线 ✓ | 仅目标表存在，无聚合逻辑 | 机制命名 ✓ / 端到端未定义 ⚠️ |
| 5. `glyph_verified_reason`+`auditSource`（H3/H5） | bug 未识 | §4.3 点名修正 ✓ | `auditSource` 仍=`book.engine_label`（L153），reason 列不存在 | 规划闭合 ✅ / bug 未修 ✗ |
| 6. mock/真实可区分（I7/C2） | 未标 | §5/§9 `is_mock` 列 + 阻断 publish ✓ | `Book` 无 `is_mock` 列；行级无标记；无 publish 守卫 | 规划部分 ✓ / 行级开口 ✗ |

---

## 二、round3 问题闭合度（逐条）

### C1 / M5 — UNKNOWN 入 HumanGate 触发（漏放新药材名）　判定：**规划已闭合，实现待做**
- **round3**：§4.2 定义 `UNKNOWN`，但 §5 触发仅列 FAIL/UNCERTAIN，漏放路径成立，违背原则 4。
- **v0.2 §5 L195**：触发条件显式列出 `glyph_status ∈ {FAIL, UNKNOWN, UNCERTAIN}`，并补"单引擎下以 UNKNOWN/低置信补触发"（呼应 §3 L160、假设 4 裁决）。§4.2 L181 把 UNKNOWN 明确指向"送人工"。**文案缺口已真实补上。**
- **残余缺口（非 round3 提出，但影响闭合）**：§4.2 同时引入 `RARE` 态（L179），"命中中医候选字表→放行不进人工队"。这正确解决了 I6 的"稀有字淹没"，但**"中医候选字表"本身未随方案提供/定义来源**——若字表过窄，本该 RARE 的新药名仍会落入 UNKNOWN 推人工（可接受）；若过宽，会把真错字当 RARE 放行。字表口径是 round3 未深挖、v0.2 仍未定义的隐性风险，**建议定稿时补字表来源与评审口径**。
- **结论**：C1 在规划层已闭合；实现依赖阶段 4，且需配套 `RARE` 字表落地。

### C2 — 校对台无原图 / `is_mock` 未标　判定：**规划部分闭合，存储链路断裂（真缺口）**
- **原图裁剪**：v0.2 §5 L202 要求 FAIL/UNKNOWN/UNCERTAIN 行带"原图裁剪（`crop_img` 或路径+bbox）"。但：
  - `AdapterPageResult.crop_img` 仅在 §2.1 L101 适配器边界声明，**从未进入 `LineResult`/`BookResult`**（`types.py` 无该字段）；
  - `to_zai_prisma.py` 的 `Line` DDL（L39–44）无图像字节列、无裁剪路径列、无 bbox 列（仅有 `charLevelJson` 含 bbox 坐标，无像素）；
  - **"取图存哪"完全未定义**：np.ndarray 是瞬时对象不落库；若存为文件需定义路径命名/隔离/生命周期，若存 blob 需加列。方案只说"随 Line 推送"，缺存储落点。
  - 因此"看原图"在 v0.2 仍是**意图陈述**，链条 `AdapterPageResult.crop_img → LineResult → Line 表列 → 校对台读取 → export` 在任一环都断。
- **`is_mock` 标记**：v0.2 §5 L206、§9 L253 要求 `Book` 增 `is_mock` 列并映射 `BookResult.is_mock`，且阻断 publish。现状：`to_zai_prisma.py` 的 `Book` DDL（L32）**只有 `source` 列（承载 engine_label）、无 `is_mock` 列**；`BookResult.is_mock`（types.py L143）已存在且 mock 源端正确（mock.py L147），但 **sink 端未写、无 publish 守卫**。属 I7 sink 端开口（架构 round4 已标注为定稿必改）。
- **结论**：C2 两半均为"规划承认 + 代码未落地"，且原图存储链路是 v0.2 的新开口，需在阶段 4 前冻结存储方案（见改进建议）。

### H1 / M4 — severity 优先级分级　判定：**映射语义已定义，但落点缺失 + 毒性 critical 触发未定义（真缺口）**
- v0.2 §5 L203 定义 `critical`=有毒药材/否定词、`warning`=FAIL/UNCERTAIN、`info`=require-human/mock，意图清晰。
- **缺口 1（落点）**：`Line` 表 DDL（L39–44）**无 severity 列**；`severity` 仅存在于 `Proofread` 表（L45–48，人工校正记录）。v0.2 说"每条待校行须带 severity"，但行级 severity 无处可存——若打算复用 Proofread.severity（人工填），则与"推送时即带优先级供排序"的诉求错位（人工还没校哪来 severity）。**字段归属未澄清**。
- **缺口 2（critical 触发机制）**："`critical`=有毒药材"依赖 §6 L217 的 `toxic_herbs.json` + 剂量红线，但**没有任何机制说明 OCR 行文本如何被识别为"含附子/细辛"并升级为 critical**。`FormulaIngredient.isToxic` 只在方剂结构化后才存在，普通正文行（如"附子三钱"）不会自动标 critical。否定词（"无""不""忌"）检测也未定义。即 critical 级的"判定来源"在 v0.2 悬空。
- M4（require-human/mock 与真兜底混流）：v0.2 已用 `severity=info` 区分（§5 L203），方向对，但同样卡在"行级 severity 列缺失"。
- **结论**：severity 的*值语义*已规划；*列落点*与*critical 自动判定*两处真实缺口，需在阶段 4 冻结。

### H2 / H4 — 错字聚合 + 批量校正 + 闭环回填　判定：**机制从"无"升级为"命名 + 目标表"，仍非端到端闭合**
- round3 H4 批评"仅文字承诺、机制未定义"。v0.2 显著改善：
  - §5 L204 要求"同字形 group-by、复用 `Term`/`HerbOCRPattern` 给候选、一处校正全局套用"；
  - §6.5 L218 定义"人工校正 → `CandidateSubmissionBatch` → term_kb / HerbOCRPattern / khub 管线"，并复用既有 `CandidateSubmissionBatch/CandidateItem/KnowledgeAuditLog` 概念（注：这些表在**当前 `to_zai_prisma.py` DDL 中并不存在**，属外部/规范 schema 引用，本仓库无实体）。
- **残余缺口**：
  - 聚合是"校对台 UI 行为"还是"推送时预聚合"？未定。若依赖 zai 工作台的 group-by，则 KZOCR 侧只需保证同字形可对齐（需稳定字形键，当前 `Line.glyphVerified` 存的是文本非字形键）；
  - "一处校正全局套用"的套用目标 = `Term`/`HerbOCRPattern`/term_kb。**当前 `to_zai_prisma.py` 已有 `Term`/`Pattern` 表（L49–57）**，可作为候选源，但 **HumanGate 在推送时是否填充 `Term` 作候选、回填时是否反向更新 `Term`** 均未描述，闭环仍是单向"写 zai 范式库"，缺"读 Term 辅助 + 写回 Term/Kb"双向；
  - 假设 3 裁决已**否决**"必须同步 khub"（round3 我的立场被否决），v0.2 §6.5 采纳"khub 异步可选"——这与 H4 闭环精神一致，但意味着跨书闭环在 khub 缺位时只剩 zai 内部 Term/Pattern，**可接受**，只是需明确"本书内闭环靠 Term/Pattern，跨书跨库靠异步 khub"两层。
- **结论**：H4 由"无机制"变为"命名机制 + 目标表"，规划层明显进步；但端到端（推送预聚合 → 人工校正 → 写回）仍停留在命名，未定义字段与触发。属**部分闭合**。

### H3 / H5 — `glyph_verified_reason` 列 + `auditSource` 语义修正　判定：**规划已点名，bug 仍存活于代码**
- v0.2 §4.3 L189 明确："新增 `Line.glyph_verified_reason`；`Line.auditSource` 改回语义（dictionary/consensus/human/rare_allowlist/confusion），修正 `to_zai_prisma.py` 误把 `auditSource` 写成 `book.engine_label` 的 bug。"
- **代码现状**：`to_zai_prisma.py` L153 仍 `auditSource=book.engine_label`；`Line` DDL（L39–44）无 `glyph_verified_reason` 列；`LineResult.glyph_verified`（types.py L36）仍是"校验后文本"语义，与 v0.2 想拆出的 `glyph_status`（枚举）/`glyph_verified_reason`（原因）**两套新字段在 types.py 均不存在**。
- **额外冲突点**：v0.2 §4.3 L188 保留 `glyph_verified` 作"校验后文本"，同时新增 `glyph_status` 枚举——但当前 `to_zai_prisma.py` 把 `ln.glyph_verified`（文本）写入 `glyphVerified` 列且无 `glyph_status` 列。若要落 v0.2，需**同时**在 `LineResult` 增 `glyph_status`/`glyph_verified_reason`、在 `Line` 表增两列、改 `push_book_to_zai` 写入。这是一处需要同步改 3 个文件的契约变更，v0.2 已计划但未做，且**未标注与 `glyph_verified` 旧列的兼容/迁移**。
- **结论**：H3/H5 在规划层已"真闭合"（点名 bug + 修正方案）；实现层 bug 存活。属**规划闭合、实现开口**，是 round4 最该优先修的代码项（单点改动，风险低）。

### I7（跨角色，UX 侧=C2 叠加）— mock 强制透传　见 C2 的 `is_mock` 部分。规划承认，sink 端（`Book` 表缺列 + 无 publish 守卫）未落地。

---

## 三、v0.2 新引入的 UX 问题

### 新-UX-1 — 原图裁剪的"出境合规 vs 看原图"冲突（H0-C / §9 自相矛盾）
- v0.2 在 §0 H0-C（L24）、§9（L250）明确"版心裁剪**仅压缩带宽、不构成脱敏**"，且"开启云端 consensus 会让一页图像同时送多家云端……须逐书/逐页同意 + 出境审计日志"。
- 但 §5 L202 又要求把"原图裁剪（crop_img 或路径+bbox）随 Line 推送"到校对台。若校对台（zai）部署在远端/跨境，推送版心裁剪图 = 推送含全量文字的图像，**其出境面与"送云端 OCR"等价**，与"裁剪非脱敏"的定性自相矛盾——方案一边说裁剪不脱敏、一边把裁剪图推到可能跨境的校对台。
- **UX 后果**：要么校对台必须本地部署（限制部署形态），要么"看原图"功能在跨境场景下被合规卡死，退回"无原图盲校"。方案未给此冲突的裁决。
- **建议**：在 §5 补一句"原图裁剪仅在本机/局域网校对台可用，跨境校对台降级为 bbox 坐标 + 本地取图代理"，把合规与 UX 的取舍显式化。

### 新-UX-2 — mock 行与真实行的行级可区分性缺位（Book 级 `is_mock` 不够）
- v0.2 §5/§9 把 `is_mock` 放在 **`Book` 表级**。但在 `EngineRouter` 多候选降级下（§3 L161），可能"一本书部分行来自真实引擎、部分行降级为 mock 候选"。此时：
  - `Book.is_mock=True` 只能告诉校对员"本书含演示数据"，但**哪几行是 mock、哪几行是真**在 `Line` 层无标记（`engine_texts` 当前 `dict[str,str]` 不携带 `is_mock`/`source` 每源，§5 L201 虽要求"含 is_mock/source 透传"但 `LineResult.engine_texts` 结构是 str 值，未升级为带元信息）；
  - 校对员在校对台看到一行文字，无法判断它是真实引擎产出还是 mock 桩，仍可能把假古籍当真校。
- **UX 后果**：I7 "假古籍"风险在行级未被根除，仅 Book 级粗粒度拦截。
- **建议**：把 `is_mock`/`source` 下沉到 `LineResult.engine_texts` 的值结构（或 `engine_results` 已含 `engine` 字段，可加 `is_mock` 标记），并在 `Line` 表增 `isMockLine`/`sourceDetail`，使行级可区分。

### 新-UX-3 — `glyph_verified_reason` 与 `glyphVerified` 旧列的迁移责任未划（连带 H3）
- 见 H3 部分：v0.2 既保留 `glyphVerified`（文本）又新增 `glyph_status`（枚举）+`glyph_verified_reason`。`to_zai_prisma.py` 当前只有 `glyphVerified` 列且它同时被 UX 当作"状态"误用（round3 C1 把 `glyphVerified` 当状态枚举讨论，实则它是文本）。v0.2 未说明旧 `glyphVerified` 列是保留、改名还是拆分，定稿前若不冻结，阶段 4 落地会制造列语义混乱。

---

## 四、改进建议（按优先级，供主会话定稿）

1. **【定稿即改·低风险的单点】修 `auditSource` bug**：`to_zai_prisma.py` L153 改为语义值（dictionary/consensus/human/rare_allowlist/confusion），并随 `GlyphVerifier` 输出落 `LineResult.audit_source`。同时 `Line` DDL 增 `glyphStatus`、`glyphVerifiedReason` 两列，types.py `LineResult` 增 `glyph_status`/`glyph_verified_reason`/`audit_source` 字段——这是 H3/H5 的真闭合，改动集中、无架构风险。
2. **【阶段 4 前必冻结·原图存储方案】**：明确 `crop_img` 的落点——建议"本地校对台存文件（按 `bookCode/pageNum/lineId` 命名、随库隔离、生命周期绑定 Book 删除），跨境场景降级为 bbox + 本地取图代理"；在 `Line` 表增 `cropImgPath`/`cropBbox` 列（或合并 `charLevelJson` 已含 bbox 则只加 `cropImgPath`）。解决 C2 + 新-UX-1。
3. **【阶段 4 前必冻结·severity 落点】**：在 `Line` 表增 `severity` 列（与 `Proofread.severity` 区分：Line.severity = 推送时由 HumanGate 计算的风险级，Proofread.severity = 人工校正类型级）；并定义 critical 自动判定：复用 `toxic_herbs.json`（§6.5）对行文本做药名匹配 + 否定词词典扫描，输出 `critical` 原因写入 `glyphVerifiedReason`。解决 H1/M4 + 新-UX-漏洞。
4. **【阶段 4 闭环】聚合与批量**：在 HumanGate 推送前，按 `glyph_status`（FAIL/UNKNOWN/UNCERTAIN）同字形分组，复用既有 `Term`/`Pattern` 表填候选；定义"一处校正 → 更新本批同字形 Line.humanFinal + 反向写 `Term`/异步 khub"的双向管线，明确 `CandidateSubmissionBatch` 在**本仓库实际 DDL 中的位置**（当前 DDL 无此表，需补或改为复用 `Term`/`Pattern`）。解决 H2/H4。
5. **【行级 mock 标记】**：`LineResult.engine_texts` 值结构升级为携带 `is_mock`/`source`；`Line` 表增 `isMockLine`；`Book.is_mock` 仅作整书概览。解决新-UX-2 + I7 行级残留。
6. **【RARE 字表口径】**：v0.2 §4.2 引入 `RARE` 态，但"中医候选字表"需随方案给出来源/评审口径，避免过宽放行真错字或过窄淹没人工。解决 C1 的隐性风险。

---

## 五、对相关假设裁决的再确认（UX 视角）

- **假设 1（字形校验不加独立再识别视觉模型）— 维持 round3 立场并强化**：v0.2 采纳"默认不加、预留 VisionRecheck 挂点、recheck 仅限本地视觉引擎"，且 §5 把"回看裁剪图随行推送"列为默认兜底（非可选）。**UX 视角完全支持**——C2 已证明无原图时形似字无法定夺。但**前提是新-UX-1 的合规取舍要先裁决**（本地校对台可用、跨境降级），否则"随行推送原图"在跨境场景自相矛盾。
- **假设 2（最小小节）— 维持 round3 立场**：倾向比三级标题更小以支撑方剂局部回填 + M3 小节级导出。v0.2 采纳"可配置 + 按 book_type + 经 contentNodeId 挂载"，方向对；但 `export_zai.py`（L32–46）当前仍扁平 `ORDER BY seqInPara`、无段落维度、无标题层级（M2/M3 在 v0.2 仍未触及），导出一致性缺口**本轮未闭合**，需在阶段 5/6 一并修。
- **假设 3（方剂库归属）— 接受被否决的结果**：v0.2 采纳"主链只写 zai、khub 异步可选"。UX 侧闭环改以 zai 内 `Term`/`Pattern` 作本书内闭环、异步 khub 作跨书闭环，**两层分明即可接受**；但需保证 H4 的"写回"至少打通 zai 内部（见建议 4），否则本书内闭环也断。
- **假设 4（consensus 成本）— 维持**：同意默认 single 且**要求 single 以 UNKNOWN/低置信补触发**。v0.2 §3 L160 + §5 L195 已落实，C1/M5 闭合。✓
- **假设 6（字形库来源）— 维持**：支持 KZOCR 内置白名单为事实源 + 可写回通道。与 H4 闭环一致；写回目标即建议 4 的 `Term`/异步 khub。✓

**总体再确认**：v0.2 对 6 项假设裁决的吸收与 UX 立场一致，无冲突；唯一需在主会话定稿时补强的是**原图合规取舍（新-UX-1）**与**RARE 字表口径（C1 隐性风险）**两处，二者不解决会让 §5 的"看原图/不漏放"在落地时打折。

---

_评审范围：仅调查与文档评审，未修改主方案或代码。代码证据截至 `to_zai_prisma.py` / `export_zai.py` / `engine/types.py` 当前版本。_
