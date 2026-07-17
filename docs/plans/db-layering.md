# 落库分层方案（设计稿 · 待审）

> 状态：设计草稿，未改动代码。依据 `project_db_architecture.md` 定调 ——
> **BookDB 存书籍全量数据，PostgreSQL 主库/运营库只存元数据与运营重要信息。**
> 撰写日期：2026-07-18。已实地核对 `storage/db.py` / `tcm_ocr/database/postgres/runtime_db.py` / `adapter/to_zai_prisma.py` / `tcm_ocr/database/manager.py`。

---

## 1. 目标架构

```
                 PDF
                  │
            ┌─────▼──────────────┐
            │  PostgreSQL 主库     │  ← 运营库（RuntimeDB）
            │  = 元数据 + 指针     │     - BookRegistry(status, pdf_path, db_path→BookDB)
            │  + 运营重要信息       │     - BookMeta(title/author/publisher/...)
            │  （统计/知识库/归档）  │     - 统计、CorrectionKnowledge、HerbOCRPattern…
            └─────┬──────────────┘
                  │ db_path 指向
                  ▼
   ┌──────────────────────────────────────────┐
   │  BookDB  (SQLite, 每书一个文件)              │  ← 书籍全量数据（系统 of record）
   │  db/{book_code}.db                          │     - 元数据（已有 set_book_meta）
   │  - 内容表：Book/Page/Paragraph/Line(+char_boxes)│
   │  - QA 表：page_progress / cross_divergence   │
   │            / hierarchy_anomaly / quality_result│
   │            / benchmark_results（已有）         │
   └──────────────────────────────────────────┘
```

**职责边界**
- **BookDB**：书籍的*全部*数据 —— OCR 全文、行、段、字符级 bbox、质检/分歧记录。按书分库，是内容的唯一权威源。
- **PostgreSQL 主库/运营库**：跨书运营视图所需的*元数据*（BookRegistry + BookMeta）+ *重要运营信息*（处理状态/统计/知识库/校正与内容归档）。**不存全量正文行/字**。
- **`custom.db`（zai 校对台 SQLite）**：见 §5 待决 —— 当前由 `to_zai_prisma.py` 写入，是冲突点。

---

## 2. 现状盘点（已核实，非凭记忆）

| 存储 | 当前实际存的内容 | 与评价 |
|------|------|------|
| **BookDB** (`storage/db.py`, `db/{book_code}.db`) | 5 张表：`page_progress`、`hierarchy_anomaly`、`cross_divergence`、`benchmark_results`、`quality_result`；另有 `set_book_meta`（书级元数据） | **缺 OCR 内容表**（无 Book/Page/Paragraph/Line/char_boxes）。只装了 QA/进度类数据 |
| **PostgreSQL RuntimeDB** (`runtime_db.py`) | `BookRegistry`(含 **`db_path` 指针**)、`BookMeta`、`OCRProcessingLog`、`StageCERStats`、`ProofreadStats`、`Term`、`CorrectionKnowledge`、`HerbOCRPattern`、`archive_ocr_line_result`、`BookContentTree`、`FinalDocumentRecord`… | 已符合"主库存元数据+指针"；但 `archive_ocr_line_result`/`BookContentTree` 存了 OCR 行文本/内容树（是否算"重要信息"见 §5） |
| **`to_zai_prisma` → `custom.db`**（共享 SQLite） | 全量内容：`Book`/`Page`/`Paragraph`/`Line`/`Proofread`/`Pattern`/`Term`/`Formula` | **违反定调**：把全文灌进共享 SQLite，且按 `bookCode` 列隔离而非按书分库 |

**关键发现（重要）**：`tcm_ocr/database/manager.py` **已经按目标架构接好**——
`register_book(pdf_path)` 在 Postgres 注册 → `_get_book_db_path` 生成每书 SQLite 路径 → `UPDATE BookRegistry SET db_path` → `BookDB(db_path).initialize_schema()` + `set_book_meta(...)`。
即"Postgres 元数据 + db_path 指针 → BookDB 全量"的意图在 manager 层已实现，**只差 BookDB 没有内容表**。

**冲突点**：`kzocr/adapter/to_zai_prisma.py` 是另一条（v0.6 主链路 `run_engine → push_book_to_zai`）独立路径，把全文写进共享 `custom.db`，与主库=Postgres、全量=BookDB 的定调矛盾。

**另**：字符级 bbox（`char_boxes`）已在适配层真正接出（`adapters.py` `return_word_box=True` + `types.py` 字段），但**尚未落任何库**。

---

## 3. 差距分析

- **G1 — BookDB 缺内容表**：无 Book/Page/Paragraph/Line 及 `char_boxes` 列，正文无法落库（只能落 QA/进度）。
- **G2 — `to_zai_prisma` 全文灌 `custom.db`**：与主库=Postgres 矛盾；且共享单库靠 `bookCode` 隔离，非按书分库。
- **G3 — `char_boxes` 未持久化**：适配层已产出，但没有任何写入路径消费它。
- **G4 — 两条并行栈**：`tcm_ocr/*`（manager 正确）与 `kzocr/adapter`+`kzocr/engine`（to_zai_prisma 旧）并存，需统一到同一套分层。

---

## 4. 改动清单（分阶段，先设计后实现）

### Phase 1 — BookDB 补内容表（系统 of record）✅ 已实施（2026-07-18）
- `storage/db.py` 新增内容表（`IF NOT EXISTS`，向后兼容现有 5 表）：
  - `book`(book_code PK, title, author, publisher, pub_year)
  - `page`(page_num PK, book_code, text, confidence, char_count, **char_boxes JSON**)
  - `line`(id, page_num, line_seq, text, **char_boxes JSON**, UNIQUE(page_num,line_seq))
- `PageResult` 新增 `char_boxes` 字段；`PaddleOCRAdapter.run_book` 透传 `result.char_boxes`（RapidOCR 自然为 None）。
- 新增写入方法：`save_book_result(BookResult)` / `save_book` / `save_page`（save_page 同时把逐行字符框展开进 `line` 表）/ `get_page` / `get_page_char_boxes` / `get_book_pages`。
- **字符级 bbox 存储位置**：`page.char_boxes` 存整页（list[行][字][4]），`line.char_boxes` 由前者展开（每行一条）。`line.text` 暂空，待 v0.7 `_build_pages_result` 提供真实行分段后补。
- **已验证（真实引擎端到端）**：PaddleOCR 单页 37 行 / 801 逐字框 → `save_book_result` → `get_page_char_boxes` 读回完全一致；`line` 表 70 行（37+33）。单测 `tests/test_bookdb_content.py` 2 passed；ruff 在 `kzocr/ tests/` 全通过。

### Phase 2 — 把 OCR 结果写进 BookDB（含 char_boxes）✅ 已实施（2026-07-18）
- **关键修复**：`orchestrator.orchestrate_book` 最终 `BookResult` 原由 `pages_text` 重建，**丢弃了** Tier1 适配器产出的 `char_boxes`。新增 `_merge_tier1_char_boxes(final_pages, tier1_result)` 按 `page_num` 把字符框合并回最终页（`tier1_result` 来自 `adapter.run_book`，即已带 char_boxes 的 `PaddleOCRAdapter`）。
- `kzocr/storage/db.py` 新增 `BookDB.persist_book_result(book, db_dir="")` 便捷落库函数。
- `kzocr/engine/run.py: run_engine` 在 `KZOCR_PERSIST_DB=1` 时调用 `persist_book_result` 落库（默认关闭，不破坏既有调用方/测试）。
- **注意**：`tcm_ocr` 栈（`kzocr.tcm_ocr.database.manager.py` / `book_pipeline.py`）使用**另一套** `BookDB`（`kzocr.tcm_ocr.database.sqlite.book_db`）与自有适配器，未接 `kzocr.engine.types.BookResult`。Phase 2 落在主线 `kzocr/engine` 路径（`run_engine` → `orchestrate_book` → `PaddleOCRAdapter.run_book`），与 `web/app.py`/`export_zai.py`/`cli_review.py` 共用的 `kzocr.storage.db.BookDB` 一致。tcm_ocr 栈如需同样落字符框，需单独对接（超出本期）。
- **已验证（真实引擎端到端）**：`run_engine(1页PDF, book_code="PH2", KZOCR_PERSIST_DB=1)` → 返回 1 页（char_boxes 36 行）→ `get_page_char_boxes(0)` 读回 36 行 / 798 字 → **与 run_engine 返回完全一致**。单测 `tests/test_orchestrator_charboxes.py`（2 passed）+ `tests/test_bookdb_content.py`（2 passed）；ruff 全通过。

### Phase 3 — 统一 `push_book_to_zai` 为「BookDB 打包导出/导入校对包」（解决 G2/G4）✅ 已实施（2026-07-18，主线 kzocr/engine 路径）

数据模型原则：**页-段-行-字**层级（用户拍板，不压扁平索引）。

- `kzocr/adapter/to_zai_prisma.py`：
  - `push_book_to_zai` 重组：① best-effort 落 BookDB（`KZOCR_PERSIST_DB` 同 `run_engine` 开关，默认关）；② best-effort 注册 Postgres 元数据（`register_book`/`set_book_meta`/`update_book_status("proofreading")`，无 PG 静默跳过）；③ 写可移植 `custom.db` 校对包（schema 不变，`Line` 新增 `paraSeq` 列，层级 key `paraSeq`/`seqInPara`/`line_id` 含段序号）。
  - 新增 `import_proofread_package(db_path, book_code, ...)`：读 `custom.db` 的 `Line.humanFinal` 与 `Proofread`，按层级键 `(pageNum,paraSeq,seqInPara) → (page_num,para_seq,line_seq)` 写回 BookDB（人工终校 + 校对记录），best-effort 归档 Postgres `LineCorrectionArchive`。
  - 新增 `freeze_custom_db(db_path)`：设只读权限(0440) + 写 `.frozen` 标记，落实「旧库冻结」。
- `kzocr/storage/db.py`：`line` 表仍以 `id` 自增为主键，新增 **UNIQUE 层级键 `(page_num, para_seq, line_seq)`** + `human_final` 列；新增 `proofread` 表（含 UNIQUE(`line_id`,`corrected_text`) 幂等）；`save_book_result` 按段落×行**位置派生**层级键（1-based，不依赖引擎是否填充 `sequence_in_*` 字段，保证导出/导入回路 key 自洽）；新增 `save_line_human_final`/`save_line_human_finals`(批量)/`save_proofreads`(INSERT OR IGNORE)/`get_line_human_final`/`get_human_final_map`/`get_proofreads`；旧 `line` 表 RENAME/重建迁移（`INSERT OR IGNORE`，para_seq 归 0）。
- `kzocr/export_zai.py`：`export_book_markdown` 行排序改 `ORDER BY paraSeq, seqInPara`（修多段页交错）。
- 测试：`tests/test_bookdb_proofread.py`（层级 human_final/proofread round-trip）、`tests/test_to_zai_import.py`（导出→改 humanFinal→导入写回一致 + freeze）；微调 `tests/test_bookdb_content.py` 一处断言（`save_page` 不再展开行）。
- **术语对齐**：层级键在两侧命名不同但语义一致 —— `custom.db` 的 `Line.id`（主键，形如 `{bookCode}-P{page}-{{paraSeq}}-{seqInPara}`）、`Proofread.lineId`（外键引用 Line.id）；`BookDB` 的 `line` 表用 UNIQUE 组合键 `(page_num, para_seq, line_seq)`。导出端两侧均由位置派生，导入端按 `(pageNum,paraSeq,seqInPara) → (page_num,para_seq,line_seq)` 映射，无需依赖 `Line.id` 字符串。
- **已验证**：Phase 3 相关单测 + `test_cli`/`test_integration` 全绿；`custom.db` 行按 (段,行) 层级导出与导入映射一致。

#### Phase 3 闭环用法（导出 → 校对 → 导入 → 再导出）

```python
from kzocr.adapter.to_zai_prisma import (
    push_book_to_zai, import_proofread_package, freeze_custom_db,
)

# 1) 导出校对包（同时 best-effort 落 BookDB + 注册 Postgres 元数据）
res = push_book_to_zai(book, db_path="work/custom.db",
                       pdf_path=Path(pdf), persist_bookdb=True)
# → work/custom.db 交给校对方在 zai 工作台改 Line.humanFinal / 加 Proofread

# 2) 校对完成后，把校对结果导入回 BookDB（系统 of record）
imp = import_proofread_package(db_path="work/custom.db")
# imp == {"book_code":..., "imported_lines":N, "imported_proofreads":M}

# 3) 旧包冻结（只读），新一版导出走新路径
freeze_custom_db("work/custom.db")

# 4) 重新导出时，human_final 从 BookDB 重新载入合并（闭环不掉终校，W1）
push_book_to_zai(book, db_path="work/v2.db", persist_bookdb=True)
```

> 范围说明：本次与 Phase 1/2 一致，**只落主线 `kzocr/engine` 路径**。`tcm_ocr` 并行栈（`archival.py` 写 `OCRLineResultArchive`、其自有 BookDB）未动；决策 #2「逐行 OCR 结果停写 Postgres」在主线本就不写，tcm_ocr 栈的逐行归档保留（如需统一需另立项）。

### Phase 4 — 校验不破
- `web/app.py`、`export_zai.py` 已读 BookDB（内容表加好后直接服务 UI）。
- 现有 e2e / `tests/test_*` 不应因新增列破坏（新增列带默认值/`IF NOT EXISTS`）。

---

## 5. 决策记录（2026-07-18 已拍板）

1. **`custom.db` / zai 校对台 = 可移植的「人工校对工作台」库（导出/导入包）**
   - 校对工作台可在本机，也可在其他机。因此需要**把书籍数据打包交出去（导出 `custom.db`），校对完成后再把数据导入回来**。
   - 故 `push_book_to_zai` 的定位是**「从 BookDB 抽取全书内容 → 打包成可移植 `custom.db` 交给校对方」**；并需配套**「导入」**接口把校对结果写回 BookDB / Postgres。
   - `custom.db` **不是**运营库（运营库=Postgres），也不是系统 of record（系统 of record=BookDB）。它是交换包。

2. **Postgres 归档口径：`BookContentTree` 算「运营重要信息」保留；`archive_ocr_line_result`（逐行 line result）不算，不留存**
   - Postgres 主库保留 `BookContentTree`（内容树快照，供运营/审计），**停止写入** `archive_ocr_line_result`（逐行 OCR 结果不入主库）。
   - 实时全量正文/逐字框的唯一权威仍在 BookDB。

3. **旧 `custom.db` 冻结**：不迁移现有数据；新流程生效，旧库只读冻结。

4. **元数据去重**：以 PostgreSQL `BookMeta` 为元数据权威；BookDB 仅存内容所需的书级键（如 `book_code` + 轻量字段），不与 `BookMeta` 重复全量元数据。

> 由此，三层职责最终定为：
> - **BookDB**（每书 SQLite）= 书籍全量数据（正文/行/段/字符级 bbox/QA），系统 of record。
> - **PostgreSQL 主库/运营库** = 元数据（`BookRegistry`+`BookMeta`）+ 运营重要信息（`BookContentTree` 快照/统计/知识库），**不存逐行 OCR 结果**。
> - **`custom.db`（zai 校对工作台）** = 从 BookDB 打包导出的可移植校对包，交校对方、校对后导入回 BookDB/Postgres；旧库冻结。

---

## 6. 风险与兼容

- 现有 5 张 BookDB QA 表不动，仅追加内容表 → 向后兼容。
- 所有 DDL 用 `CREATE TABLE IF NOT EXISTS`；新增列带默认值。
- `to_zai_prisma` 改动需保留 `db_path` 覆盖入口（不破坏现有调用方）。
- char_boxes 落库为 JSON（`list[list[list[int]]]`），单行体积可控；如需检索可考虑后续拆 `char_box` 子表（本期不做）。

---

## 附：本次已落地的前置（供实现 Phase 2 复用）
- `kzocr/engine/types.py:242` `AdapterPageResult.char_boxes` 字段已加。
- `kzocr/engine/adapters.py` `PaddleOCRAdapter.run_page` 已 `return_word_box=True`，`_parse_ppocr_result` 已解析 `text_word_boxes` → `char_boxes`（实测单页 37 行 / 801 逐字框）。
- RapidOCR 无字符级框，`char_boxes=None`。
