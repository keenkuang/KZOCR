# KZOCR 统一 OCR 引擎架构方案 — 数据完整性与建模评审（round4，数据完整性与建模评审专家）

- **评审日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（v0.2 · round3 修订版）
- **本轮焦点**：round3 数据完整性 CRITICAL（目标 schema 错配）在 v0.2 是否被**真闭合**
- **事实来源（本仓库 + 外部真实工件）**：
  - `kzocr/adapter/to_zai_prisma.py` 的 `_SCHEMA_DDL`（当前实际建表子集）
  - `/home/keen/tcm_ocr_zai/prisma/schema.prisma`（规范 schema，独立仓库 `git@github.com:keenkuang/tcm_ocr.git`，KZOCR 子模块 `console/tcm-ocr-zai` 指向它但**当前未检出**）
  - `/home/keen/khub`（仅 `docs/`，无 `*.prisma`、无 `client.py`、无任何方剂表）
  - `docs/reviews/2026-07-09-round3/summary.md`、`data_integrity.md`

---

> **🔴 本轮核心更正（针对我自己的 round3 草稿）**
>
> 我在 round3 `data_integrity.md` 中声称「本仓库内不存在 `schema.prisma`，也不存在 `tcm_ocr_zai/` 目录，相关名称唯一出现处即那份草稿本身」。**这是错误的。** 经本次核查：
> - 规范 `schema.prisma` **确实存在**于 `/home/keen/tcm_ocr_zai/prisma/schema.prisma`（独立仓库 `tcm_ocr_zai`，即说明书 V5.9 的校对台后端）。
> - 它**完整包含** v0.2 §6 引用的全部表：`BookRegistry`、`ContentNode`、`FinalDocumentRecord`、`FormulaComposition`、`ProofreadRecord`、`KnowledgeAuditLog`、`CandidateSubmissionBatch`、`OCRProcessingLog`、`Paragraph`(含 `contentNodeId`/`crossPageGroupId`/`isSplitPart`)、`Line`(含 `charLevelJson`/`humanFinalText`)、`FormulaIngredient`。
>
> 因此 round3 的立论前提（「扁平子集 vs 规范 schema 错配」是 paper 谈兵）**不成立**——规范 schema 真实存在且是正确的目标。**但是**，当前 KZOCR 唯一的写库件 `to_zai_prisma.py::_SCHEMA_DDL` 仍自建一个**与规范 schema 漂移的扁平子集**，既未引用也未对齐规范表/列。所以 round3 的 CRITICAL 在**设计层已闭合（v0.2 §6 指对了目标），在代码层尚未闭合（适配器仍写老子集）**。这正是 round4 要判定「是否真闭合」的结论所在。

---

## 一、结论

**有条件通过，且较 round3 实质性前进：目标 schema 错配这一 CRITICAL 在「方向/建模」层已被 v0.2 §6 真闭合——因为规范 `schema.prisma` 真实存在且具备 `ContentNode`/`FinalDocumentRecord`/`FormulaComposition`/`Paragraph.contentNodeId`/`Line.charLevelJson` 等全部支撑列。但「代码/适配器」层未闭合：`to_zai_prisma.py::_SCHEMA_DDL` 仍自建扁平子集，缺少 `contentNodeId`、`FinalDocumentRecord`、`FormulaComposition`、七类临床字段、`ProofreadRecord.reviewerId`、任何 Outbox，并以 `DELETE WHERE bookCode` 做破坏性幂等（未改 MERGE）。v0.2 把适配器重写推迟到阶段 5，作为**计划**可接受，但「真闭合」需以阶段 5 把适配器对齐规范 schema 为验收点，否则仍是纸上谈兵。**

一句话：**目标对了，桥还没修。**

---

## 二、round3 问题闭合度（逐条，标注 CRITICAL 是否解除）

round3 data_integrity 标记的问题：C1/C2(CRITICAL)、H1/H2/H3(HIGH)、M1/M2(MEDIUM)。以下逐条判定「设计层/代码层」闭合情况。

### 🔴 C1 — 全文归档目标列不存在 + 生命周期无锚点【设计层：已解除；代码层：未解除】
- **v0.2 §6.1**：全文 → `FinalDocumentRecord(full_md, sha256)`，非 `Book.final_markdown`；§6.7 用 `BookRegistry.status='archived'`+`archivedAt` + `OCRProcessingLog(stage='archive')`。
- **规范 schema 验证**：`FinalDocumentRecord`（schema.prisma:412）含 `bookId/fileType/filePath/sha256/sizeBytes` ✓；`BookRegistry`（:31）含 `status/archivedAt` ✓；`OCRProcessingLog`（:378）存在 ✓。
- **结论**：round3 所谓「Book 表无 final_markdown/status/sha256」的断言，在规范 schema 下**不再成立**——正确的落点都存在。**但** `to_zai_prisma.py` 的 `_SCHEMA_DDL`（`Book`/`Line`/`Proofread` 等扁平表）仍不含 `FinalDocumentRecord`/`BookRegistry`，适配器迄今不写规范表。→ 设计闭合，**代码未闭合**，建议阶段 5 验收项。

### 🔴 C2 — 最小小节→Line 归属映射未定义，孤儿行风险【设计层：已解除；代码层：部分未解除（新增身份映射缺口）】
- **v0.2 §6.3**：复用既有 OCR `Line.id`，经 `Paragraph.contentNodeId` 挂到 `ContentNode`，严禁从 `final_markdown` 重切生成新 Line。
- **规范 schema 验证**：`Paragraph`（:104）含 `contentNodeId` ✓、`Line`（:134）含 `charLevelJson`(bbox, :151) ✓ 与 `humanFinalText`( :150) ✓、`ContentNode`（:62）含 `level/sequence/pageStart/pageEnd/source` ✓。规范模型**完全支撑** v0.2 设计。
- **代码层缺口**：(1) 适配器 `Paragraph` DDL（to_zai_prisma.py:36）**无 `contentNodeId`**；(2) 更关键——「既有 OCR `Line.id`」究竟是什么身份？规范 `Line` 主键是 cuid 且经 `paragraphId` 外键归属，而 KZOCR `types.py` 的 `LineResult` 是按 `(page, para, seq)` 派生的行，**两者身份映射未定义**。跨引擎（paddle/VLM/kimi）各自产出不同 Line 集合时，如何稳定映射到同一规范 `Line.id` 以保证「挂载/重跑不重复建行」未说明。→ 设计层解除，**身份映射这一新子缺口需在阶段 5 明确定义**（见 N2）。

### 🟠 H1 — 跨库一致性（假设 3）无任何机制【未解除，且暴露规范 schema 亦缺 Outbox】
- **v0.2 §6.5**：khub 同步「异步可选、不阻塞」，跨库先补 `version`+`checksum`，单向 zai→khub。
- **验证**：(1) 规范 `schema.prisma` **无 `Outbox` 表**（grep 确认）；(2) `/home/keen/khub` 仅有 `docs/`，**无任何 `*.prisma`、无 `client.py`、无方剂表**。
- **结论**：假设 3 裁决（主链只写 zai、khub 异步可选不阻塞）在当前「khub 根本无方剂端点」下是**安全的**（没有可写目标，自然不阻塞）。但 v0.2 所谓「khub 同步异步可选」**完全没有承载机制**——规范 schema 无 Outbox、khub 无表无 client。→ H1 **未解除**，但风险被「khub 不存在」暂时压住；一旦 khub 方剂表立项，缺 Outbox 的跨库一致设计会立刻暴露。建议提前在规范 schema 加 `FormulaSyncOutbox`（见改进建议）。

### 🟠 H2 — 现有幂等对人工层是破坏性清空【设计层：已解除；代码层：未解除】
- **v0.2 §6.6**：重跑仅 TRUNCATE 自动生成层，`humanFinalText`/`ProofreadRecord`/`KnowledgeAuditLog` 按 `lineId` 更新/追加，绝不整书清空——显式呼应 round2 H2。
- **验证**：规范 `ProofreadRecord`（:182）为按 `lineId` 追加结构（含 `reviewerId`），天然适配 MERGE。
- **代码层**：`to_zai_prisma.py:106-109` 仍对 `Proofread/Line/Paragraph/Page/Book/Formula*/Pattern/Term` **全部 `DELETE WHERE bookCode=?`**——这正是 round3 指出的破坏性清空，且表名是 `Proofread`（规范为 `ProofreadRecord`），与 v0.2 的 MERGE 主张**直接冲突**。→ 设计层解除（方案已定义 MERGE），**代码层未闭合**。

### 🟠 H3 — 可追溯性字段部分缺失【设计层：已解除；代码层：未解除】
- **验证**：规范 `ProofreadRecord.reviewerId`( :186)、`KnowledgeAuditLog`( :458)、`OCRProcessingLog`( :378) 均存在，round3 担心的「谁校的/何时归档/全文指纹」在规范模型下**已具备**（archive 审计锚可由 `OCRProcessingLog`+`FinalDocumentRecord.sha256` 承载）。
- **代码层**：适配器仍写扁平 `Proofread`（无 `reviewerId`/`reviewedAt`，to_zai_prisma.py:45-48）。→ 设计层解除，代码层随 H2 一并待阶段 5 改写。

### 🟡 M1 — bookCode 隔离必须延续到新表【转化为「bookCode→bookId 解析」缺口，未解除】
- 规范 schema 不以 `bookCode` 为主键/隔离键，而是 `BookRegistry.id`(cuid) + `bookCode @unique`（:31-33），所有子表经 `bookId` 外键隔离。
- **缺口**：适配器当前以 `bookCode TEXT` 作隔离键（to_zai_prisma.py:31 等），与规范 `bookId` 键不匹配。「bookCode → BookRegistry.id」的解析/写入顺序（先 upsert BookRegistry 拿 id，再写子表带 bookId）**完全未定义**。→ 隔离诉求仍在，只是从「新表带 bookCode」转为「适配器须先解析 bookId」。

### 🟡 M2 — 跨页方剂切分标记【设计层：已解除；代码层：未解除】
- **验证**：规范 `Paragraph.crossPageGroupId`/`isSplitPart`（:114-116）、`FormulaComposition.crossPageGroupId`（:216）**均存在**。
- **代码层**：扁平 `Paragraph` DDL 无此列。→ 设计层解除，代码层待阶段 5。

**CRITICAL 是否解除的最终判定**：round3 的两个 CRITICAL（C1 目标列不存在 / C2 归属映射）在**「v0.2 指对规范 schema 且规范 schema 真实存在」这一意义上已闭合**；但「真闭合」要求适配器落地，而 `to_zai_prisma.py` 仍写漂移的子集，**代码层 CRITICAL 未解除**。建议：把「阶段 5 把 `_SCHEMA_DDL` 重写为规范 schema 的适配层并删除自建子集」列为 round3 CRITICAL 的**硬验收点**，否则 v0.2 的「修正错配」仍是纸面。

---

## 三、v0.2 新引入的数据问题（评审重点 6）

- **N1 — 规范 schema 未纳入 KZOCR，漂移风险高**：`console/tcm-ocr-zai` 子模块**当前未检出**（目录为空），`/home/keen/tcm_ocr_zai` 只是同机另一份 checkout。v0.2 称「唯一事实源是规范 schema.prisma」，但适配器不引用它、自写 `_SCHEMA_DDL`，两份 schema 没有任何同步机制——日后规范 schema 一改，适配器 silently diverge。→ 须 vendore 或 pin 子模块并让适配器从规范 schema 派生。
- **N2 — 「既有 OCR Line.id」身份映射未定义（C2 的延伸）**：v0.2 §6.3 要求「复用既有 OCR `Line.id`」，但规范 `Line` 以 `paragraphId`+cuid 组织，KZOCR `LineResult` 以 `(page,para,seq)` 派生；跨引擎（kimi/paddle/VLM）产出不同 Line 集合时，稳定映射到同一规范 `Line.id` 的规则缺失 → 重跑会重复建行或使 `Paragraph.contentNodeId` 悬空。
- **N3 — ContentNode 写入依赖未落地的 TOC 分析器（round2 H5）**：v0.2 §6.2 直接写 `ContentNode`，但 §7 阶段 5 前置「先补最小 TOC 分析器（regex 标题级）」且 round2 H5 尚未立项。若分析器缺位就先填 `Paragraph.contentNodeId`，会出现**空壳/错误 ContentNode → 外键悬空 → 孤儿 Paragraph/Line**。依赖顺序须显式：TOC 分析器先于 ContentNode 回填。
- **N4 — 七类临床字段在规范 schema 也无家（v0.2 §6.4 自相矛盾）**：v0.2 §6.4 要求 `FormulaComposition` 补齐 `usage/gongyong/zhuzhi/fangjie/fields_json` 七类，但**规范 `FormulaComposition`（:206-227）只有 `formulaAlias/contextReferenceType/contextDescription/referencedFormulaId/rootFormulaId/crossPageGroupId/isArchived/sourcePages`——并不含这七类字段**。即 v0.2 既说「以规范 schema 为准」，又说要补规范 schema 里不存在的列。→ 须决定：要么给规范 schema 加 `formulaFieldsJson`(含七类) 迁移，要么在 v0.2 显式标注「七类需扩展规范 schema」。
- **N5 — `glyph_status` 枚举在规范 schema 无列**：v0.2 §4.3 新增 `Line.glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]`，但规范 `Line`（:134-163）只有 `glyphVerifiedText`/`auditSource`，**无 `glyph_status`**。该枚举落库位置未定义。
- **N6 — khub 同步无承载（呼应 H1）**：`CandidateSubmissionBatch`（:428）仅覆盖 herb/meridian/context 范式与 term，**不含方剂**；规范 schema 无 `Outbox`；khub 无方剂表/client。v0.2 §6.5「khub 同步异步可选」目前无任何可执行路径。

---

## 四、改进建议（含「请先核对 schema.prisma 实际存在性」）

1. **【最高优先】请先核对规范 `schema.prisma` 的实际存在性与归属**：
   - 它**确实存在**：`/home/keen/tcm_ocr_zai/prisma/schema.prisma`，对应独立仓库 `git@github.com:keenkuang/tcm_ocr.git`（说明书 V5.9 校对台）。KZOCR 子模块 `console/tcm-ocr-zai` 指向它，但**当前未检出**。
   - 行动：在 v0.2 中把「唯一事实源」具体化为该文件路径 + commit，并**检出/锁定子模块**或 vendore 一份进 `kzocr/adapter/schema/`，使适配器能引用而非手搓 `_SCHEMA_DDL`。
2. **把「适配器对齐规范 schema」列为 round3 CRITICAL 的硬验收点**：阶段 5 须删除 `to_zai_prisma.py` 自建扁平子集，改为对规范 schema 的适配层（按 `bookCode → BookRegistry.id` 解析后写 `bookId` 外键）。验收：连跑两次同 `bookCode`，人工 `ProofreadRecord`/`humanFinalText` 不丢、不翻倍。
3. **归还 MERGE 幂等（闭合 H2）**：阶段 5 把 `DELETE WHERE bookCode` 改为「自动层 TRUNCATE + 人工层按 `lineId` MERGE」；注意规范表名为 `ProofreadRecord`（非 `Proofread`）。
4. **定义 Line 身份映射（闭合 N2/C2）**：明确 KZOCR `LineResult` → 规范 `Line(paragraphId)` 的稳定键（建议以 `(bookCode, pageNum, paraSeq, lineSeq)` 派生确定性 id 并映射到规范 `paragraphId`），跨引擎重跑复用同一键，避免重复/悬空。
5. **七类字段归宿（闭合 N4）**：在规范 `FormulaComposition` 增加 `formulaFieldsJson`（承载 usage/gongyong/zhuzhi/fangjie 等七类），v0.2 §6.4 注明这是对规范 schema 的扩展迁移而非「已存在」。
6. **补 `glyph_status` 列（闭合 N5）**：在规范 `Line` 加 `glyphStatus String?`，或在 v0.2 注明复用 `verificationStatus`/`auditSource` 表达；二者须二选一并冻结。
7. **跨库 Outbox（闭合 H1/N6，前瞻）**：在规范 schema 补 `FormulaSyncOutbox(pending/sent/failed, syncKey, version, checksum)`，单向 zai→khub；khub 方剂表/client 立项前，主链只写 zai 即为唯一可行路径（与假设 3 裁决一致）。
8. **TOC 分析器先于 ContentNode 回填（闭合 N3）**：在阶段 5 显式把「TOC 分析器（round2 H5）」列为 §6.2 的前置门禁，未产出 `ContentNode` 时不写 `Paragraph.contentNodeId`，避免空壳外键。
9. **隔离键统一（闭合 M1）**：适配器统一用 `bookCode → BookRegistry.id` 解析，所有子表带 `bookId` 外键，替代当前 `bookCode TEXT` 散落。

---

## 五、对假设 3 裁决的再确认

**维持 round3 裁决：主链只写 zai（规范 `FormulaComposition`），khub 异步可选、不阻塞。**

- 本轮新证据**强化**了该裁决的正确性：khub 当前**无任何方剂表、无 `client.py`、仅有 `docs/`**，因此「必须同步 khub」在事实层不可行，「只写 zai」是唯一可落地路径。
- 但补充两点，避免 v0.2 §6.5 给人「khub 同步已设计好」的错觉：
  1. 规范 schema **无 Outbox**，khub **无端点**，故「异步可选」目前**没有任何机制承载**——它不阻塞是因为根本没接，而非设计好了可插拔。
  2. 一旦 khub 方剂表立项，跨库一致必须先补 `version`+`checksum`+`FormulaSyncOutbox`（单向 zai→khub），否则会重演 round3 H1 的「zai 有/khub 无」分裂态。
- 结论：**裁决不变；v0.2 §6.5 应把「khub 同步」明确标注为「待 khub 方剂系统立项后的扩展点，当前主链仅写 zai」，而非暗示已具备同步能力。**
