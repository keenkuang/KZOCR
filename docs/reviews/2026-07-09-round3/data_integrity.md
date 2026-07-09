# KZOCR 统一 OCR 引擎架构方案 — 数据完整性与建模评审（round3，数据完整性与建模评审专家）

- **评审日期**：2026-07-09
- **评审角色**：数据完整性与建模评审专家
- **评审对象**：`docs/plans/ocr-engine-unification.md`（重点第 5 章归档层、第 6 章 Archiver、第 8 章假设 2/3、第 9 章风险）
- **事实来源（仅限本仓库内真实存在的工件）**：
  - `kzocr/adapter/to_zai_prisma.py` 的 `_SCHEMA_DDL`（扁平子集，即本评审所认定的"现有 schema"）
  - `kzocr/engine/types.py` 的 `BookResult`/`LineResult` 等归一化结构
  - `docs/plans/toc-driven-pipeline-design.md`（TOC 三级结构：章=科 / 节=病 / 小节=方）
  - `kzocr/khub/client.py`（khub 推送，仅 HTTP，无事务能力）

---

> **⚠️ 与同轮同名草稿的口径说明（重要）**
>
> 本目录下已有一份 `data_integrity.md`，其 CRITICAL 结论建立在一个"规范 `schema.prisma`"之上，声称其中已存在 `ContentNode`/`FormulaComposition`/`BookRegistry`/`FinalDocumentRecord`/`KnowledgeAuditLog`/`reviewerId`/`checksum`/`crossPageGroupId` 等表与列。
> 经核查，**本仓库内不存在该 `schema.prisma`，也不存在 `tcm_ocr_zai/` 目录**；上述名称在本仓库中唯一出现处即为那份草稿本身。该规范 schema 属于本仓库之外的 zai 控制台独立工程，无法在此验证。
> 因此本评审**不采信那份草稿的"扁平子集 vs 规范 schema 错配"立论**（C1/H1/H2/H3 链条在仓库内不可证伪，且违背用户给定的"现有 schema = `_SCHEMA_DDL` 扁平子集"这一前提）。本评审改以**仓库内真实存在的扁平 `_SCHEMA_DDL`** 为唯一建模基线，重新推导归档层的数据完整性问题。若日后确实要以外部 zai 规范 schema 为归档目标，应单独立项并把它纳入本仓库或显式引用其 URI——这是方案本身的一个待决缺口（见 C1），但不是"已存在可复用"的表。

---

## 一、结论

**有条件通过：第 6 章归档层在"数据模型"层面有 2 项关键缺口、3 项高危及 2 项中危问题，需在落地前补齐建模，否则归档数据将出现孤儿行、跨库方剂不可信、重跑丢失人工校对三大完整性事故。**

方案第 6 章对"现有 schema 已覆盖大部分"的判断部分成立——`Book/Page/Paragraph/Line/Proofread/Pattern/Term/Formula/FormulaIngredient` 这一扁平子集确实存在，且 H2 整改后已带 `bookCode` 隔离。但方案**漏掉了三个真实存在的事实缺口**：

1. 该子集**没有**任何 TOC/Section 表（方案说"新增"，但未定义结构与归属关系）——这是本评审 C2 的核心。
2. `Book` 表**根本没有 `final_markdown` 列**，且 `push_book_to_zai` 也从不写 `final_markdown`——方案第 178 行"全文→`Book(final_markdown)`"在 schema 层面落空（C1）。
3. 现有幂等是"`DELETE FROM ... WHERE bookCode=?` 全表清空"，**连 `Proofread`/`humanFinal` 一起删**——归档作为"校对后的最终动作"若重跑，会抹掉人工成果（H2）。

方案第 8 章假设 3（方剂库归属）仍"待评审确认"，等于把跨库一致性设计推迟，但归档层一旦落地就必须面对跨库同步——这是不安全的悬空设计（H1）。

---

## 二、关键问题（按严重度）

### 🔴 CRITICAL

**C1 — 全文归档目标列不存在，且归档生命周期无锚点**
- 方案第 178 行："全文 → `Book` 表（`final_markdown` 汇总）"。
- 事实：`_SCHEMA_DDL` 的 `Book` 表列为 `bookCode/title/author/publisher/pubYear/pubEra/bookType/source/pageCount/lineCount/cerValue/lineAccuracy`——**无 `final_markdown`**。且 `push_book_to_zai` 的 INSERT 也未写该列。
- 同时 `Book` 无 `status`/`archivedAt`（"本书是否已归档"不可查）、无 `sha256`（归档全文完整性不可校验）。
- **影响**：方案宣称的"全文归档"在现有 schema 下无法落库；校对后全文以何介质追溯、是否被篡改均不可证。

**C2 — 最小小节→Line 的归属映射完全未定义，孤儿行风险**
- 方案第 180 行："依 TOC 把 `final_markdown` 切成最小小节，落入 `Section/Paragraph/Line`（已有表结构）"。
- 事实：
  - 现有 `Line` 只有 `pageNum`/`seqInPara` 定位，**没有任何指向小节（TOC 节点）的外键**；
  - 现有 `Paragraph` 同样无 `tocNodeId`；
  - `final_markdown` 在 `export_markdown` 里已被摊平成 `## 第 N 页` + 纯文本行，**丢失了 `Line.id`/`charLevelJson`（bbox）**，从它重新切分出的"新行"若无外键指回既有 OCR Line，便是孤儿数据。
- **影响**：同一本书被 TOC 切分后，Line 与"最小小节"无强制归属关系。任一 Line 若未被任何小节覆盖（如扉页、插图说明、TOC 页本身），将悬空；且因无约束，重切易产生重复/错挂的行，违背"可检索到最小知识单元"的目标。

### 🟠 HIGH

**H1 — 跨库一致性（假设 3）无任何机制：无事务、无最终一致、无回滚/重试**
- 方案第 181 行把方剂入库简化为"写 `Formula`+`FormulaIngredient`"，并在假设 3 纠结"写 zai 即可 vs 同步到独立 khub 方剂系统"。
- 事实：
  - zai 是本地 SQLite（`sqlite3`），khub 是独立 HTTP 服务（`khub/client.py`）。二者**不可能处于同一事务**——没有任何两阶段提交/分布式事务。
  - 方案未定义：(a) 跨库**同步键**（规范化方剂名？来源书+页码？）；(b) **幂等消费键**（khub 侧如何判重）；(c) **失败处理**（khub 不可达时 zai 已写、是否回滚 zai？还是保留 outbox 重试？）；(d) **版本/校验和**（无 `version`/`checksum`，无法检测两侧漂移）。
- **影响**：假设 3 若选"同步到 khub"，在 khub 抖动/超时时会产生"zai 有、khub 无"或"重复写入 khub"的分裂态，且无法自愈，方剂库不可信。

**H2 — 现有幂等对人工层是破坏性清空**
- `to_zai_prisma.py:106-109` 对 **全部** 9 张表（含 `Proofread`、`Line` 的 `humanFinal`）按 `bookCode` 整体 `DELETE` 后再 INSERT。
- 事实：归档层位于 HumanGate 之后（方案第 6 章）。若因任何原因（换引擎、补 TOC、修正切分）触发"重新归档"，会把**已有人工校对（`Proofread` 记录与 `Line.humanFinal`）一并抹掉**。
- 此外，隔离键不一致：`Formula`/`FormulaIngredient` 的 `id` 是 `F{idx}`/随机 cuid，**不携带 `bookCode` 前缀**；虽两表都有 `bookCode` 列，但 `Formula.id` 在跨书重跑时若 `idx` 重复会与另一本书的 `Formula` 主键冲突（当前靠 `DELETE WHERE bookCode` 兜底，但若是直接 upsert 会撞 PK）。
- **影响**：归档重跑 = 人工成果归零，违反"校对后归档可回溯"的根本诉求。

**H3 — 可追溯性字段部分缺失**
- 方案第 5 章要求人工校对时能对照"原图与多源结果"，第 4.3 节要求落 `glyphVerified`/`auditSource`。
- 现状（扁平子集**已具备**）：`Line.engineTexts`(多源 JSON)、`consensus`、`llmCorrected`、`glyphVerified`、`auditSource`、`charLevelJson`(bbox) 均在——这部分**达标**。
- 缺失项：
  - `Proofread` 表**无 `reviewerId`/`reviewedAt`**——人工校对的"责任人 + 时间"不可追溯（方案第 5 章要求"对照多源"，但谁改的、何时改的无法审计）。
  - 无**归档审计锚**：没有 `archiveLog(bookCode, archivedAt, engineLabel, sha256, lineCount)` 记录"何时以何引擎归档、全文指纹为何"。
  - `final_markdown` 未持久化（见 C1），导致"校对后全文"这一最终结论本身不在库内，只能从行重新拼，漂移风险大。
- **影响**：单条 Line 的多源回溯达标，但"谁校的、何时归档的、归档全文是否被改"三类审计缺失。

### 🟡 MEDIUM

**M1 — bookCode 隔离必须显式延续到新增表**
- H2 整改已让 9 张表带 `bookCode`，但方案新增的 TOC/Section 表若漏带 `bookCode`，会出现"不同书的方剂/术语挂到同一小节树"的串库风险。所有新表必须：`bookCode TEXT NOT NULL` + 对 `Line`/`Formula` 的 `bookCode` 做外键/一致性约束。

**M2 — 跨页方剂切分标记在扁平 schema 中不存在**
- TOC 设计文档 §0 明确"方剂常跨页（如 26.4 从页4 末跨到页5 首）"。
- 事实：扁平 `Paragraph`/`Line`/`Formula` 均**无 `crossPageGroupId`/`isSplitPart`** 之类标记（外部规范 schema 据说有，但本仓库无）。
- 影响：归档切分若仅按 `final_markdown` 文本行切，跨页方的行会被错误拆到相邻小节，或丢失"属于同一方"的关联，导致方剂结构断裂。

---

## 三、改进建议（含建议的 Section/TOC 表结构）

### 3.1 新增 TOC/Section 表（替代方案"新增 TOC/Section 表"的模糊表述）

直接落库 TOC 三级树（章=科 / 节=病 / 小节=方），并显式建立"最小小节→Line"归属，杜绝孤儿行：

```sql
-- TOC / Section 树：level 1=科(章) / 2=病(节) / 3=方(小节 最小检索单元)
CREATE TABLE IF NOT EXISTS TocNode (
    id          TEXT PRIMARY KEY,          -- 形如 {bookCode}-T{order} 或 cuid
    bookCode    TEXT NOT NULL,             -- 延续 H2 按书隔离
    parentId    TEXT,                       -- 自引用，章的 parentId=NULL
    title       TEXT,
    level       INTEGER NOT NULL,          -- 1/2/3
    sequence    INTEGER NOT NULL,          -- TOC 内序号，整合排序用（对齐 toc 设计 §2.0 SectionPlan.order）
    subsectionId TEXT,                     -- 仅 level=3：'26.4' 等（对齐 ^\d+\.\d+ 切分标记）
    slug        TEXT,                       -- 文件系统安全名，便于与 output/<book>/sections/<slug>/ 对应
    pageStart   INTEGER,
    pageEnd     INTEGER,
    source      TEXT DEFAULT 'toc',        -- toc(目录分析) / auto(启发式回查)
    FOREIGN KEY (bookCode) REFERENCES Book(bookCode)
);

-- 最小小节 → Line 归属（强制，杜绝孤儿行）
-- 每个 Line 必须恰好归属一个 level=3 的 TocNode（最小小节）
CREATE TABLE IF NOT EXISTS SectionLine (
    lineId      TEXT NOT NULL,
    tocNodeId   TEXT NOT NULL,             -- 指向 TocNode(id, level=3)
    bookCode    TEXT NOT NULL,
    role        TEXT,                       -- body/heading/cross_page_part
    PRIMARY KEY (lineId, tocNodeId),
    FOREIGN KEY (lineId)   REFERENCES Line(id),
    FOREIGN KEY (tocNodeId) REFERENCES TocNode(id),
    FOREIGN KEY (bookCode) REFERENCES Book(bookCode)
);
-- 约束建议（SQLite 用触发器或应用层保证）：每个 Line 在 SectionLine 中有且仅 1 行；
-- 归档后跑校验：SELECT lineId FROM Line WHERE id NOT IN (SELECT lineId FROM SectionLine) 应为空，否则报错。
```

要点：
- **不重新生成 Line 主键**。归档切分只把既有 `Line.id` 通过 `SectionLine` 挂到小节节点；从 `final_markdown` 做的只是"展示层聚合"，**绝不**据纯文本新造 Line，从而保住 `pageNum`/`charLevelJson`(bbox) 的原图回溯能力（回应 C2、H3 回溯诉求）。
- 切分粒度：最小单元 = `level=3` 小节=方（`^\d+\.\d+`，见 toc 设计 §0/§2.4）；方证内 9 类固定字段（来源/组成/用法…）作为 `Formula` 的结构化字段，**不再下钻为更细的"小节"**，避免行级误切。
- 跨页方剂（M2）：在 `SectionLine.role='cross_page_part'` 标记续接行，并以 `TocNode.pageStart/pageEnd` 表达跨页范围；在切分逻辑里沿用 toc 设计 §2.2 的 `<!-- page N -->` 标记推算，避免重建 `crossPageGroupId`。

### 3.2 全文归档落库（修复 C1）

- 给 `Book` 加 `finalMarkdown TEXT` 与 `status TEXT`/`archivedAt TEXT`；
- 或（更稳妥）新增 `ArchiveArtifact` 表存全文与指纹，避免大文本塞主表：
  ```sql
  CREATE TABLE IF NOT EXISTS ArchiveArtifact (
      id         TEXT PRIMARY KEY,
      bookCode   TEXT NOT NULL,
      kind       TEXT,            -- 'full_md' / 'body_md' / 'final_json'
      content    TEXT,
      sha256     TEXT,            -- 全文完整性锚
      sizeBytes  INTEGER,
      archivedAt TEXT,
      FOREIGN KEY (bookCode) REFERENCES Book(bookCode)
  );
  ```

### 3.3 跨库一致性（修复 H1，对应假设 3）

zai(SQLite) 与 khub(HTTP) 无法同事务。采用 **Outbox + 幂等消费** 的最终一致模式：

```sql
CREATE TABLE IF NOT EXISTS FormulaSyncOutbox (
    id          TEXT PRIMARY KEY,
    bookCode    TEXT NOT NULL,
    formulaId   TEXT NOT NULL,                 -- zai 侧 Formula.id
    syncKey     TEXT NOT NULL,                 -- 规范化方剂名 + bookCode + 来源页（去重/幂等键）
    payloadJson TEXT,                          -- 待推 khub 的结构化方剂
    status      TEXT DEFAULT 'pending',        -- pending / sent / failed
    attempts    INTEGER DEFAULT 0,
    lastError   TEXT,
    createdAt   TEXT,
    sentAt      TEXT
);
```
- zai 写入 `Formula` 后，**同一事务内**写 `FormulaSyncOutbox(pending)`；khub 推送由独立 worker 消费，成功置 `sent`，失败递增 `attempts` 并保留 `lastError`——zai 永不因 khub 失败而回滚。
- 幂等：khub 侧以 `syncKey` 判重（upsert）；zai 侧 `Formula.id` + `syncKey` 双键保证可重放。
- **回滚语义**：不存在跨库回滚。失败策略是"zai 为事实源留底 + outbox 重试"，而非撤销 zai。
- 先给 `Formula`/`FormulaIngredient` 补 `version INTEGER` + `checksum TEXT` 再谈跨库漂移检测（当前两表均无）。

### 3.4 幂等改为人工层保留（修复 H2）

- 归档重跑时，**仅 TRUNCATE 自动生成层**（`Line.engineTexts/consensus/llmCorrected`，以及 `Pattern`/`Term` 的自动沉淀）；
- 对 `humanFinal`/`Proofread` 采用 **MERGE（按 `lineId` 更新 `humanFinal`，按 `(lineId, reviewerId, reviewedAt)` 追加 `Proofread`）**，绝不整书清空人工成果；
- `Formula.id` 改带 `bookCode` 前缀（如 `{bookCode}-F{idx}`），消除跨书 PK 冲突。

### 3.5 可追溯性补齐（修复 H3）

- `Proofread` 加 `reviewerId TEXT` / `reviewedAt TEXT`；
- 新增 `ArchiveLog(bookCode, archivedAt, engineLabel, sha256, lineCount, tocNodeCount)` 作为归档审计锚；
- 确认归档全程保留 `Line.engineTexts`/`consensus`/`glyphVerified`/`auditSource`/`charLevelJson`（扁平子集已具备，勿在切分时丢弃）。

---

## 四、对第 8 章假设项立场

### 假设 2（最小小节的定义）
**立场：采用 TOC 三级中的"小节=方"（`^\d+\.\d+`）作为最小可检索单元，存为 `TocNode(level=3)`。**
- 反对把"段落/行"下沉为最小小节去做 markdown 重切——那会丢失 `pageNum`/`charLevelJson`(bbox) 回溯（C2）。
- 归属通过 `SectionLine`（既有 `Line.id` → `TocNode.id`）实现，而非从 `final_markdown` 重新生成 Line。
- 方证内 9 类字段作为 `Formula` 结构化字段，不再视为更细小节。

### 假设 3（方剂库归属）
**立场：先确定"zai 为每书缓存 + khub 为权威方剂库"的单向同步模型，采用 Outbox 最终一致（见 3.3），不要双写、不要跨库事务。**
- 在 khub 方剂系统建成前，归档层**只写 zai `Formula`/`FormulaIngredient`**，并保留 `FormulaSyncOutbox` 导入接口；
- 跨库一致性**前提**：先给 `Formula`/`FormulaIngredient` 补 `version`+`checksum`，并定义 `syncKey`（规范化方剂名 + `bookCode` + 来源页）；
- 方案第 181 行"写入 `Formula` + `FormulaIngredient`（已有表）"可保留，但须补：别名/派生去重键（建议规范化名 + `syncKey`）、跨库 outbox、以及 `version`/`checksum`；"同方异名"的去重不能仅靠表内字段，需 khub 侧维护别名图。

---

## 五、落地优先级（给方案修订）

1. **必改（CRITICAL）**：补 `TocNode`/`SectionLine` 表 + 强制归属校验（C2）；补全文归档落库 `ArchiveArtifact`/`finalMarkdown`（C1）。
2. **必改（HIGH）**：跨库 Outbox + 同步键 + `version`/`checksum`（H1）；归档幂等改人工层保留（H2）；补 `Proofread.reviewerId/reviewedAt` + `ArchiveLog`（H3）。
3. **建议（MEDIUM）**：新表全带 `bookCode` 外键（M1）；跨页方剂 `SectionLine.role='cross_page_part'` 标记（M2）。
4. **待决（方案层面）**：第 8 章假设 2/3 由"待评审确认"转为上述明确立场，并写入第 6 章实施细节；若日后改以外部 zai 规范 `schema.prisma` 为归档目标，须将其纳入本仓库或显式引用 URI，否则"复用现有 schema"的措辞在事实层不成立。
