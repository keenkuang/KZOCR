# KZOCR 统一 OCR 引擎架构方案 — 数据完整性与存储评审（round3）

- **评审日期**：2026-07-09
- **评审角色**：数据完整性与存储评审专家
- **评审对象**：`docs/plans/ocr-engine-unification.md`（重点第 5/6 章归档层、第 8 章假设、第 9 章风险）
- **对照事实来源**：
  - `kzocr/adapter/to_zai_prisma.py`（`_SCHEMA_DDL` 扁平子集，方案第 6 章所依据的 schema）
  - `tcm_ocr_zai/prisma/schema.prisma`（zai 校对台**规范** schema —— 方案第 6 章几乎未引用，但它是校对台 UI 真正读取的模型）
  - `kzocr/engine/types.py`（`BookResult` 归一化结构）
  - `docs/plans/toc-driven-pipeline-design.md`（TOC 三级结构）

---

## 一、结论

**有条件通过，但第 6 章（归档层）与第 8 章假设 2/3 在"目标 schema 是什么"这一根本问题上存在系统性错配，必须回到规范 schema 重写后才能落地。**

核心问题：方案第 6 章声称"已有 `Book/Page/Paragraph/Line/Proofread/Pattern/Term/Formula/FormulaIngredient` 表结构，需**新增 TOC/Section 表**"，这一判断建立在 `to_zai_prisma.py` 的**扁平子集**之上；而 zai 校对台的**规范 `schema.prisma` 已经存在** `ContentNode`（即 TOC/Section 树）与富字段的 `FormulaComposition`（含 `formulaAlias`/`rootFormulaId`/`referencedFormulaId`/`crossPageGroupId`）。两套 schema 的**表名、列名、隔离键**均不一致（`Book` vs `BookRegistry`、`Line.glyphVerified` vs `Line.glyphVerifiedText`、`bookCode TEXT` 主键 vs `bookId` cuid 外键图）。

若归档层按扁平子集实现，将出现：**（a）校对台 UI 的 TOC 树、方剂别名/派生关系、知识审计日志、sha256 归档全部落空；（b）最小小节切分后的 Line 丢失原图回溯能力；（c）方剂跨库一致性无任何版本/校验支撑**。方案必须先明确"归档层的唯一目标 schema = 规范 `schema.prisma`"，并收敛/废弃扁平子集，否则归档数据与可校对数据脱节。

---

## 二、关键问题（按严重度）

### 🔴 CRITICAL

**C1 — 归档层目标 schema 未定义，且方案所依据的扁平子集与规范 schema 严重不符**
- 方案第 6 章 / 第 183 行称"zai 现有 `Book/Page/Paragraph/Line/.../Formula/FormulaIngredient` 已覆盖第 1/3/4 点的大部分；需**新增 TOC/Section 表**"。
- 事实：规范 `schema.prisma` **已包含** `ContentNode`（`bookId`/`parentId`/`title`/`level`/`sequence`/`source`/`section`/`pageStart`/`pageEnd`），它**就是** TOC/Section 树，无需新增；规范 `Formula` 实为 `FormulaComposition`（含 `formulaAlias`/`referencedFormulaId`/`rootFormulaId`/`crossPageGroupId`/`isArchived`），而非扁平 `Formula`。
- 同时，扁平子集与规范 schema **表名/列名**都不一致：
  - `Book`（扁平）vs `BookRegistry`（规范，且含 `status`/`archivedAt` 生命周期）
  - `Line.glyphVerified TEXT` + `auditSource`（扁平）vs `Line.glyphVerifiedText TEXT` + `auditSource`（规范，且 `Paragraph.verificationStatus` 另有枚举）
  - 扁平 `Paragraph` **缺** `contentNodeId`/`crossPageGroupId`/`isSplitPart`/`pageId` 外键——而这些正是"最小小节归属 + 跨页方剂"的关键列。
- 方案还在第 5 章说"推送复用现有 `to_zai_prisma.py` → zai `db/custom.db`"，但 `to_zai_prisma.py` 写的是扁平子集，并非规范表。两套 schema 并存风险极高。
- **影响**：归档层无论写哪一套，都会与另一套脱节；方案对"复用现有 schema"的说法在事实层面不成立。

### 🟠 HIGH

**H1 — 最小小节→Line 的"原图回溯"会在 markdown 重切中丢失（映射断裂）**
- 方案第 180 行："依 TOC 把 `final_markdown` 切成最小小节，落入 `Section/Paragraph/Line`（已有表结构）"。
- 风险 1：`final_markdown` 是**纯文本**，`export_markdown` 已把行摊平为 `## 第 N 页` + 若干文本行，**丢失了 `Line.id`/`pageNum`/`charLevelJson(bbox)`**。从它重新切分生成的"新 Line"若无外键指回既有 OCR Line，人工校对时**无法回溯原页/原图裁剪**。
- 风险 2：扁平子集 `Line` 主键是 `{bookCode}-L{page}-{para}-{seq}`，规范 `Line` 主键是 cuid 且经 `Paragraph` 外键定位。若归档层另起炉灶重切，会产生**第二套 Line**，与原 OCR 行来源歧义、难以对账。
- 规范 schema 其实**已经解决这个问题**：`Paragraph.contentNodeId` 把段落挂到 TOC 节点、`Line` 保留 `pageNum` 与 `charLevelJson`（含 bbox），`Paragraph.crossPageGroupId` 处理跨页。但方案未利用、扁平子集也无 `contentNodeId`。
- **影响**：人工校对"看原图"这一核心诉求（方案第 5 章 HumanGate 明确要求）在归档后失效。

**H2 — 方剂库跨库一致性：缺少同步键、版本与去重语义（对应假设 3）**
- 方案第 181 行把方剂入库简化为"写 `Formula` + `FormulaIngredient`（已有表）"，并在假设 3 纠结"写 zai 即可 vs 同步到独立 khub 方剂系统"。
- 问题：(a) 规范 `FormulaComposition` 已内置 `formulaAlias` + `rootFormulaId` + `referencedFormulaId` 表达"同方异名/派生"——但扁平 `Formula` 无这些列，若按扁平实现则去重能力归零；(b) **两套 schema 都没有 `version` / `checksum` 列**，khub↔zai 同步无从保证幂等与版本；(c) 方案未定义**单一事实源**与**规范化同步键**（如规范化方剂名 + 来源书 + 剂量标准），去重仅靠别名图且要求 khub 也维护该图。
- **影响**：同方异名（如"鳖甲消瘤方/甲瘤汤"变体）在跨库复制时既可能重复又可能失去关联，方剂数据不可信。

**H3 — 字形校验状态落库与审计追溯不足（对应假设 2 的审计诉求）**
- 方案第 4.3 节：`Line.glyphVerified`(PASS/UNKNOWN/FAIL/UNCERTAIN) + `auditSource` 已够支撑"校对后归档审计"。
- 不一致：(a) 枚举与规范 schema 错位——规范 `Paragraph.verificationStatus` 用 `pending/local_verified/cloud_verified/needs_human_review`，而规范 `Line.glyphVerifiedText` 是**自由文本非枚举**；方案自定义枚举未被任何一张规范表采用；(b) 人工校对**回写**：规范已有 `humanFinalText` + `ProofreadRecord(reviewerId)` + `KnowledgeAuditLog(reviewerId, beforeData, afterData)`，但扁平子集 `Proofread` **缺 `reviewerId`**，按扁平实现则审计责任人丢失；(c) 归档完整性：规范 `FinalDocumentRecord(filePath, sha256, sizeBytes)` 正是"归档后审计追溯"的锚点，扁平子集**无此表**——若归档层走扁平子集，sha256 归档与知识审计日志全缺。
- **影响**：方案声称的"校对后归档审计追溯"在扁平子集上无法兑现。

### 🟡 MEDIUM

**M1 — 幂等机制对"人工层"是破坏性清空**
- 现有幂等（`to_zai_prisma.py:106-109`）是 `DELETE FROM ... WHERE bookCode=?` **整书清空**。对归档层而言，若因任何原因"重新归档"，会**连已有人工校对（`Proofread`/`humanFinalText`）一起清空**，破坏审计追溯。
- 要求：归档必须是"校对后的最终动作"，且重跑时对人工层采用 **MERGE（按 lineId 更新 humanFinalText，按 (lineId,reviewerId,createdAt) 追加 Proofread）** 而非 TRUNCATE。
- 另：隔离模型不一致——扁平子集用 `bookCode TEXT` 主键，规范 zai 用 `bookId`(cuid) 外键图（`BookRegistry.bookCode @unique`）。方案第 5/6 章反复称"按 `bookCode` 隔离"，与规范 `bookId` 隔离模型不符。

**M2 — "全文 → Book(final_markdown)"列不存在**
- 方案第 178 行：全文 → `Book` 表（`final_markdown` 汇总）。但两套 schema 的 `Book`/`BookRegistry` **都没有 `final_markdown` 列**。全文应落规范 `FinalDocumentRecord(fileType='full_md'|'body_md'|'final_json', sha256)`。若擅自加自定义列，迁移与 zai UI 均不认。

**M3 — 跨页方剂切分标记需保留**
- 规范已为跨页方剂设计 `Paragraph.crossPageGroupId`/`isSplitPart` 与 `FormulaComposition.crossPageGroupId`（toc 设计文档亦强调"方剂常跨页"）。markdown 切分若不携带这些标记，跨页方的行会被错误拆分/错误归属到相邻小节。归档层必须沿用而非重建。

**M4 — 归档生命周期态未翻转**
- 规范 `BookRegistry.status` 含 `archived` + `archivedAt`，`OCRProcessingLog.stage` 含 `archive`。方案未提及在归档后翻转状态、写 `FinalDocumentRecord`、记 `OCRProcessingLog(stage='archive', status='completed')`。结果："本书是否已归档"不可查，回退/重跑无从判断。

---

## 三、改进建议

1. **（C1 根因）明确唯一目标 schema**：归档层以规范 `tcm_ocr_zai/prisma/schema.prisma` 为**唯一**写入目标；将 `to_zai_prisma.py` 的扁平子集收敛为"向规范 schema 的适配层"（补足 `ContentNode`/`FormulaComposition` 富字段写入），或在文档中明确"扁平子集仅用于无 zai 时的独立演示"，归档正式路径只走规范 schema。所有第 6 章措辞改为引用规范表名。

2. **（H1）最小小节切分不重建 Line**：归档时**复用既有 OCR `Line.id`**，仅通过 `Paragraph.contentNodeId` 把段落挂到对应 `ContentNode`（小节节点）；从 `final_markdown` 仅做"展示层聚合"，**不**据文本重新生成 Line 主键。每个 Line 保留 `pageNum` + `charLevelJson`（bbox），确保校对台可回溯原页/原图。

3. **（H1/H3）补 `ContentNode` 写入**：归档层必须写 `ContentNode`（章=科/节=病/小节=方三级，`level`/`sequence`/`pageStart`/`pageEnd`/`source='toc'`），使 zai 的 TOC 树可用——这直接替代方案所谓"新增 TOC/Section 表"的错误假设。

4. **（H2）方剂以规范 `FormulaComposition` 为准，定义跨库策略**：
   - 不新建扁平 `Formula`；每书方剂作为 `FormulaComposition` 出现记录（含 `formulaAlias`/`rootFormulaId`/`referencedFormulaId`/`crossPageGroupId`）。
   - khub 为**权威方剂库**，zai 为每书缓存；同步**单向 zai→khub 导入**（非双写），同步键 = 规范化方剂名 + 别名图（`rootFormulaId`）+ 来源书 + 剂量标准（`PharmacopoeiaTimeline`）。
   - **先给两张库补 `version` + `checksum` 列**再谈跨库一致性；khub 未建成前只写 zai 并保留 import 接口。

5. **（H3）统一字形状态枚举与审计**：在规范 schema 上统一 `glyphVerified` 语义（建议 `Line.glyphVerifiedText` 存枚举 PASS/UNKNOWN/FAIL/UNCERTAIN，`auditSource` 取值 dictionary/consensus/human），并**保留扁平子集所缺的 `reviewerId`**（写 `ProofreadRecord`/`KnowledgeAuditLog`）；归档必须落 `FinalDocumentRecord(sha256)` 作为完整性锚。

6. **（M1）归档幂等改为 MERGE 人工层**：重跑归档时仅 TRUNCATE 自动生成层（引擎文本/共识），对 `humanFinalText`/`ProofreadRecord`/`KnowledgeAuditLog` 采用追加/按 lineId 更新，绝不整书清空人工成果。

7. **（M2/M3/M4）补齐归档落库项**：全文→`FinalDocumentRecord(full_md, sha256)`；跨页方剂沿用 `crossPageGroupId`/`isSplitPart`；归档收尾翻转 `BookRegistry.status='archived'` + `archivedAt`，并写 `OCRProcessingLog(stage='archive', status='completed')`。

---

## 四、对第 8 章假设项立场

### 假设 2（最小小节的定义）
**立场：采用 TOC 三级中的"小节=方"（`^\d+\.\d+`，见 toc 设计文档 §0/§2.4）作为最小可检索单元，存为 `ContentNode(level=subsection)`。**
- 反对把"段落/行"下沉为最小小节做 markdown 重切——那会丢失 pageNum/bbox 回溯（见 H1）。
- 最小小节的"归属"通过 `Paragraph.contentNodeId` 实现，而非从 `final_markdown` 重新生成 Line。
- 切分粒度建议：章=科(`level=1`)、节=病(`level=2`)、小节=方(`level=3`)；方证内的 9 类固定字段（来源/组成/用法…）作为 `FormulaComposition` 的结构化字段，不再视为更细的"小节"。

### 假设 3（方剂库归属）
**立场：不新建扁平 `Formula` 表；以规范 `FormulaComposition`（已含 alias/root/referenced/crossPage）作为每书出现缓存，khub 为权威方剂库，单向 zai→khub 导入而非双写。**
- 跨库一致性前提：两张库先补 `version`+`checksum`；同步键用规范化名 + 别名图 + 来源书，去重由 `rootFormulaId` 统一承载（khub 须同样维护别名图）。
- 在 khub 方剂系统建成前，归档层**只写 zai `FormulaComposition`**，并显式保留"向 khub 导入"的接口与映射，避免日后双写无据。
- 因此方案第 181 行"写入 `Formula` + `FormulaIngredient`（已有表）"应改为"写入 `FormulaComposition` + `FormulaIngredient`（规范 schema，含别名/派生/跨页字段）"。

---

## 附：方案 vs 规范 schema 对照速查

| 方案第 6 章表述 | 扁平子集（`to_zai_prisma.py`） | 规范 `schema.prisma` | 评审判定 |
|---|---|---|---|
| 全文→`Book(final_markdown)` | `Book` 无该列 | `BookRegistry` 无该列；应为 `FinalDocumentRecord(sha256)` | ❌ 列不存在（M2） |
| 需**新增** TOC/Section 表 | 无 | **已有 `ContentNode`** | ❌ 误判，应直接复用（C1/H3） |
| 最小小节→`Section/Paragraph/Line` | `Paragraph` 缺 `contentNodeId` | `Paragraph.contentNodeId` 已可挂 TOC | ⚠️ 须用 `contentNodeId`，勿重切 Line（H1） |
| 方剂→`Formula`+`FormulaIngredient` | 扁平 `Formula`(无 alias/root) | `FormulaComposition`(含 alias/root/referenced/crossPage) | ❌ 应改写 `FormulaComposition`（H2/C1） |
| 字形 `Line.glyphVerified`+`auditSource` | 有，但 `Proofread` 缺 `reviewerId` | 有 `glyphVerifiedText`/`auditSource`/`ProofreadRecord(reviewerId)`/`KnowledgeAuditLog` | ⚠️ 枚举与审计字段需对齐（H3） |
| 按 `bookCode` 隔离 | `bookCode TEXT` 主键 | `bookId` cuid 外键图 + `BookRegistry.bookCode @unique` | ⚠️ 隔离模型不一致（M1） |
| （未提及）归档完整性/生命周期 | 无 `FinalDocumentRecord`/`OCRProcessingLog` | 有 `FinalDocumentRecord(sha256)`/`BookRegistry.status='archived'` | ❌ 缺失导致不可审计/不可查（H3/M4） |
