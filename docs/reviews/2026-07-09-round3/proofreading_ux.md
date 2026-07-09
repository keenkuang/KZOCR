# KZOCR 第 3 轮评审 — 人工校对体验（UX）专审

- **评审日期**: 2026-07-09
- **评审角色**: 人工校对体验（UX）评审专家
- **评审对象**: `docs/plans/ocr-engine-unification.md`（第 4 章字形校验、第 5 章 HumanGate）、`adapter/to_zai_prisma.py`、`export_zai.py`、zai `prisma/schema.prisma`
- **总体结论**: **有条件通过**。主方案的"人工兜底"意图正确（绝不静默放行错字），但 5 个核心 UX 环节均存在缺口：**漏放（UNKNOWN 不触发）、校对台无原图、无优先级、无批量聚合、闭环回填未定义**。其中前两项属严重级，会直接削弱"兜底"本意。

---

## 一、结论速览

| 环节 | 现状 | 是否合格 |
|------|------|----------|
| 1. 触发合理性 | FAIL/UNCERTAIN 触发；**UNKNOWN 被遗漏**；无优先级 | ⚠️ 漏放 + 无分级 |
| 2. 校对台信息 | engine_texts/consensus/glyphVerified 已带；**无原图、无校验依据、auditSource 错用、mock 未标** | ❌ 缺定夺依据 |
| 3. 批量与一致性 | 无错字聚合、无术语/方剂批量辅助 | ❌ 重复劳动 |
| 4. 闭环回填 | 仅文字承诺，**机制未定义** | ⚠️ 不可验证 |
| 5. 导出一致性 | `export_zai.py` 缺 Term/Formula、行序可能错乱、丢小节结构 | ⚠️ 与归档层不一致 |

---

## 二、关键问题（按严重度）

### 🔴 严重（Critical）

**C1. HumanGate 触发条件遗漏 `UNKNOWN` —— 新字/新药材名被自动放行进库（漏放）**
- 主方案第 4.2 节明确定义 `UNKNOWN`：字符 ∉ 已知集但属合法 Unicode CJK（如新见药名）"进入待确认"。
- 但第 5 章 HumanGate 触发条件仅列 `FAIL` 或 `UNCERTAIN` 且多引擎不一致，**未列 `UNKNOWN`**。
- 后果：UNKNOWN 行不会推送人工，又不在"多数引擎一致且通过字形校验 → 提升 PASS"的范围内（它根本没通过校验），实际出口未定义——要么被当作可放行，要么静默滞留。这正是"错字进库"的漏放路径，与原则 4「绝不静默放行错字」直接冲突。
- 修复：把 `UNKNOWN` 明确纳入 HumanGate 触发（至少兜底为需人工确认）。

**C2. 校对台无原图缩略/裁剪 —— 形似字人工无法定夺，且 mock 假数据未标注**
- `adapter/to_zai_prisma.py` 写入 `Line` 的字段含 `engineTexts/consensus/glyphVerified/charLevelJson` 等，但**没有任何图像字节或裁剪路径字段**；zai `schema.prisma` 的 `Line` 也无图像列（仅 `charLevelJson` 含 bbox 坐标，无像素）。
- 后果：对"未/末""已/己""白木/白术"这类**形似混淆**，人工看不到原图只能靠多引擎候选推断；若候选也分歧（UNCERTAIN），则**无法定夺**，兜底退化为猜。
- 叠加：Round2 H8「publish 假古籍」风险在校对台延续——`Book` 表无 `is_mock` 列，adapter 把 `engine_label` 写入但 `BookResult.is_mock` 未映射，校对员无法区分 mock 演示数据与真实结果。
- 修复：对兜底行回看原图裁剪（方案 4.2 末尾已埋伏笔 `VisionRecheckAdapter`）并把裁剪图随行推送；`Book` 增加 `is_mock`/`source` 标记。

### 🟠 高（High）

**H1. 无严重度/优先级分级，审校员时间被平均分配**
- `Proofread.severity`（`info/warning/critical`）字段已存在，但 HumanGate 是二元推送（推/不推），**未把 `glyphVerified` 状态、置信度、是否涉及有毒药材映射到 severity**。
- 后果：整页全失败会一次性推送大量行，低风险行（如标点）与高风险行（有毒药材"误白木为白术"）混排，校对台无法按风险排序，关键错字被淹没。

**H2. 同一错字跨页无聚合 / 无批量校正**
- 每条兜底行是独立 `Line`，无"相同错字分组"。方剂书"白术→白木"可能每页出现，校对员需逐处重复校正，**无法"一次校对全局生效"**。
- zai 已有 `Term`/`HerbOCRPattern` 库可作"建议校正/批量替换"依据，但方案未说明 HumanGate 是否利用这些库辅助批量。

**H3. `glyphVerified` 仅存状态、无校验依据**
- `Line.glyphVerified` 只存 `PASS/UNKNOWN/FAIL/UNCERTAIN` 字符串，`schema.prisma` 无 `glyphVerifiedReason` 列。
- 后果：人工拿到 `FAIL` 不知"为何 FAIL"（"未见于知识库" vs "形似 未/末" vs "置信度低于阈值"），需自行重判，效率低。

**H4. 闭环回填机制未定义（违背第 9 章承诺）**
- 第 9 章称"知识库持续从人工校对结果回流（见 khub/term_kb 闭环）"，但**无任何机制描述**：人工在 zai 改完一字，如何写回 `term_kb` / `HerbOCRPattern` / khub 术语库？`to_zai_prisma.py` 只写 zai 库，khub 推送在 `cli.py`（Round2 H6 指其异常类型错误）。
- 后果：人工劳动无法沉淀，下一本书同一错字仍触发人工，违背"避免重复劳动"目标（评审重点第 4 点）。

**H5. `auditSource` 语义错用**
- adapter 写入 `auditSource = book.engine_label`（`to_zai_prisma.py:153`），但第 4.3 定义 `auditSource` = 通过哪类校验（dictionary/consensus/human）。
- 后果：校对员误以为该字段表示校验来源，实际是引擎名，误导"为何可信"的判断。

### 🟡 中（Medium）

**M1. `export_zai.py` 缺 Term / Formula 模块 —— 与 adapter 导出不一致**
- `adapter/to_zai_prisma.py:export_markdown`（行 272–281）导出"术语 + 方剂"；但 zai 的 `export_zai.py`（行 48–63）只导出 `Pattern`（药名/经络），**无 Term、无 Formula**。
- 后果：人工在 zai 校对台终校后导出 Markdown，会比从 KZOCR 导出的少术语/方剂，归档层内容不一致。

**M2. 导出按 `seqInPara` 排序、无段落维度 —— 多段落页面行序错乱**
- `export_zai.py:38` `ORDER BY seqInPara` 仅按行内序号；adapter 写入的 `seqInPara = ln.sequence_in_paragraph`（段落内序号），同页多段落会交错（段落顺序本应由 `Paragraph.seqInPage` 决定，但导出未 join `Paragraph`）。

**M3. 导出丢失标题层级 / 最小小节分割 —— 与归档层（第 6 章）不一致**
- 导出是扁平"第 N 页"结构，`Line.headingLevel` 写 `NULL`，无 `###` 标题；未做最小小节（ContentNode/Section）分割。属 Round2 H5（TOC 分节未落地）子项，但影响校对后归档可读性。

**M4. `--require-human` / mock 全量推送，无"需校对 / 仅供参考"区分**
- 三者（FAIL / 整页失败 / require-human / mock）混在同一推送流，require-human 与 mock 会把已高置信 PASS 行一并推给人工，浪费人力。建议 mock/require-human 推送带 `severity=info` 或单独标记，与真正的兜底行区分。

**M5. 单引擎（Strategy A）模式系统性一致错误无法触发**
- 单引擎下无"多引擎不一致"，`UNCERTAIN` 子条件永不成立，仅靠 `FAIL` 触发；若所有引擎（仅一个）对某字**系统性读错**且未触发字形 FAIL，则整行漏放。建议单引擎模式以 `UNKNOWN`/低置信度补触发（与 C1 联动）。

---

## 三、改进建议（按优先级）

1. **补 UNKNOWN 触发（C1/M5）**：第 5 章 HumanGate 列表新增 `glyphVerified = UNKNOWN`，且"单引擎下低置信或 UNKNOWN 即需人工"；明确 UNKNOWN 的出口（进人工，不得自动放行）。
2. **兜底行带原图（C2）**：实现 4.2 末尾的 `VisionRecheckAdapter`，对 FAIL/UNKNOWN/UNCERTAIN 行回看版心裁剪，把裁剪图（或路径 + bbox）随 `Line` 推送；`Book` 增加 `is_mock` 列并映射 `BookResult.is_mock`。
3. **加优先级（H1）**：HumanGate 推送时计算 `severity`——`critical`=涉及有毒药材/否定词；`warning`=FAIL/UNCERTAIN；`info`=require-human/mock，使校对台可排序。
4. **错字聚合 + 批量校正（H2）**：HumanGate 层对同一 `glyphVerified` 失败字形做 group-by，支持"一处校正、全局套用"；复用 `Term`/`HerbOCRPattern` 给出建议候选。
5. **补校验依据（H3/H5）**：`Line` 增 `glyphVerifiedReason` 列，`auditSource` 改回语义（dictionary/consensus/human），修正 adapter 写入。
6. **定义闭环回填（H4）**：明确"人工校正 → `CandidateSubmissionBatch`（两步提交）→ term_kb / HerbOCRPattern / khub 术语·方剂库"管线，复用 schema 既有 `CandidateSubmissionBatch/CandidateItem/KnowledgeAuditLog`，并修正 Round2 H6 的 khub 异常类型。
7. **统一导出（M1/M2/M3）**：`export_zai.py` 补齐 Term/Formula 模块；导出 join `Paragraph.seqInPage` 再 `seqInPara` 保证顺序；保留 `headingLevel` 还原小节结构，与归档层第 6 章对齐。

---

## 四、对第 8 章设计假设的立场

- **假设 1（字形校验不加独立再识别视觉模型）— 反对维持现状，建议收敛为"兜底行启用可选 VisionRecheck"**。校对 UX 视角下，C2 已证明无原图时形似字无法定夺；即便不全量加视觉模型，也**必须**对 FAIL/UNKNOWN 行回看原图裁剪并随行推送（4.2 末尾已埋伏笔，应纳入默认兜底而非可选）。
- **假设 2（最小小节定义）— 倾向比"三级标题"更小（段落 / 方证）**，以支持方剂校正的局部回填与 M3 的小节级导出；但需与 Round2 H5 的 TOC 分节落地联动，否则导出仍为扁平全文。
- **假设 3（方剂库归属）— 主张必须同步 khub**。H4 闭环回填要求人工校正结果能回流，若只在 zai `Formula` 表而不同步 khub 方剂系统，术语/方剂闭环在跨书场景断裂；建议经"两步提交协议"回流。
- **假设 4（consensus 成本）— 同意默认 single，但要求 single 模式以 UNKNOWN/低置信补触发**（见 C1/M5），否则单引擎系统性一致错误漏放。
- **假设 6（字形知识库来源）— 建议 KZOCR 内置精简字形白名单 + 可写回通道**。直接复用 kimi `term_kb` 会强耦合，且 H4 闭环要求 KZOCR 侧有可写回的字形库，二者需解耦但互通。

---

_评审范围：仅调查与文档评审，未修改主方案或代码。_
