# tcm_ocr 栈对接主线（实施前评审设计稿 · 待多角色评审）

> 状态：设计草稿，经架构评审 + code-reviewer 两轮复审修订（首轮核实 4 项 CRITICAL 前提错误，次轮修正第 3 处闭环断点 `run.py:64` + char_boxes ROI 尺寸 + 决策 #2 删列）。待 docs-architect 复审 → 推送，再进入实施。
> 撰写日期：2026-07-18。已实地核对 `kzocr/tcm_ocr/**`、`kzocr/adapters/engine_runners.py`、
> `kzocr/engine/{types,run}.py`、`kzocr/scheduler/orchestrator.py`、`kzocr/storage/db.py`、
> `kzocr/adapter/to_zai_prisma.py`。
> 上游：DB 分层 Phase 1/2/3 已在主线 `kzocr/engine` 落地（见 `db-layering.md`），本设计将其扩展到 tcm_ocr 平行栈。

---

## 1. 问题背景（已核实，非凭记忆）

tcm_ocr 与主线 `kzocr.engine` 是**两套并行的 OCR 栈**，事实如下：

1. **两条独立入口**：
   - 主线：`run_engine` → `orchestrate_book`（:423 `adapter.run_book(pdf_path)`）→ 返回 `BookResult`（含 `char_boxes`）。生产由 `web/app.py`、`cli.py` 调用；未配 `kimi_engine_dir` 时 Tier1 = 主线 `PaddleOCRAdapter`。
   - tcm_ocr：`BookPipeline.process_book` 由 **Celery 任务**（`celery_tasks/tasks.py`）独立驱动，返回**摘要 dict**（仅 `status/pages_processed/...`），把全文写进**自建 snake_case SQLite**，再 `archive_to_postgresql` 归档到 Postgres。其行结构存在 `self.page_results`（每页 dict 含 `page_number` 与 `lines`，行用 `fused_text`/`confidence`/`bbox`/`engine_results`）。
2. **主线调 kimi 的路径是断的**：`BookPipelineAdapter.run_book`（`engine_runners.py:75`）`return self._pipeline.process_book(...)`（摘要 dict）；且 `orchestrate_book`（:423）调用时**未传 `pipeline_config`**（适配器 `_ensure_pipeline` 仅在传入时才初始化，否则 RuntimeError）也**未传 `book_code`**。kimi 被选中即崩溃且无法初始化。即「主线 `run_engine` → tcm_ocr」从未闭环。
3. **三套并存 schema**：book_pipeline 自建 snake_case SQLite（真实生产路径）、`manager.py` 的 PascalCase `BookDB`（`kzocr/tcm_ocr/database/sqlite/book_db.py`）—— **非死代码**，被 4 个生产模块真实 import（`knowledge/{formula/extractor.py:26, herb_pattern/auto_discover.py:24, meridian_pattern/auto_discover.py:23, context_pattern/auto_discover.py:23}`，并经 `process_book`→`_run_auto_discovery`/方剂提取使用）、主线 `kzocr/storage/db.py` 的 `BookDB`。
4. **char_boxes 在 tcm_ocr 主链路缺失**：`PagePipeline._multi_engine_recognition`（`page_pipeline.py:597`）只用 `engine.recognize(roi)`（返回 str，无 bbox），**未调 `recognize_char_level`**；`extract_char_bboxes`（`core/engines/paddleocr_adapter.py:414`，CTC 反投影）已实现但**当前全仓无任何调用方**（仅定义 + doctest；`graded_scheduler.py:259` 只调 `recognize_char_level`，不调 `extract_char_bboxes`）。本设计首次启用它。落库侧 `OCRLineResultArchive` 也无 bbox。
5. **Postgres 决策 #2 未落实 + 有读方**：`archival.py` 实时写 `OCRLineResultArchive`（逐行 OCR），与 `db-layering.md` 决策 #2（应停写逐行 OCR、仅留 `BookContentTree`）冲突；但 `runtime_db.get_book_stats(:2012)` 读 `ocr_result_count`、`manager.get_book_stats(:283/294)` 调用并上报——**停写会使该统计恒为 0**，不能直接默认关。

---

## 2. 目标

把 tcm_ocr 栈对接到主线 `kzocr.engine` 归一化体系，使：

- **(G1) 统一输出类型 + 闭环**：tcm_ocr 产出 `kzocr.engine.types.BookResult`（含真实 `book_code`），让「主线 `run_engine` → tcm_ocr kimi」真正闭环（修复断点 + 补 `pipeline_config`/`book_code` 贯通）。
- **(G2) 统一系统 of record**：tcm_ocr 经适配器产出的 `BookResult` 落库到主线 `kzocr.storage.db.BookDB`（与 Phase 3 一致），以主线 BookDB 为权威源。
- **(G3) 统一字符框**：tcm_ocr 主链路产出 `char_boxes`（复用已有 `extract_char_bboxes`），落进 BookDB 与导出包，与主线路径对齐。
- **(G4) 落实决策 #2**：tcm_ocr 归档路径停写 `OCRLineResultArchive`/`archive_ocr_line_result`（逐行 OCR），保留 `BookContentTree`；并修正 `get_book_stats` 的 `ocr_result_count` 来源（改由 BookDB 行数派生或移除），避免恒为 0。
- **(G5) 复用既有导出/导入闭环**：tcm_ocr 产出的 `BookResult` 天然可走 `push_book_to_zai` / `import_proofread_package`（它们消费 `BookResult`），无需新适配器代码。

---

## 3. 设计（接入点 + 接口）

### 3.1 新增转换器 `kzocr/tcm_ocr/pipeline/book_result_convert.py`

```python
def book_result_from_tcm_ocr(
    page_results: list[dict],          # BookPipeline.page_results（每页 dict 含 page_number + lines）
    *,
    book_code: str,
    title: str = "", author: str = "", publisher: str = "", pub_year: int = 0,
    engine_label: str = "kimi",
    formulas=None, herb_patterns=None, meridian_patterns=None, context_patterns=None, terms=None,
) -> BookResult:
```

映射要点（已按代码核实）：
- **page_num**：取 `page_result["page_number"]`（`page_pipeline.py:129`，`"page_number": page_num`）。
- **行序**：tcm_ocr 行可能未按阅读序（MinerU blocks 未必按 y 排序）→ 先按 `line.get("bbox", [0, 0, 0, 0])[1]`（y 上沿）升序排序，保证行序=阅读序，避免行/char_boxes 错位。用 `line.get(...)` 而非 `line["bbox"]`，对缺 bbox 的行兜底为 `[0,0,0,0]`（不抛 KeyError）。
- **文本**：取 `line["fused_text"]`（:656，非 `text`；`text` 多来自 block 且常空）→ `LineResult.final` / `consensus`。
- **置信度**：`line["confidence"]`（:657）。
- **字符框**：`line.get("char_bboxes")`（页绝对坐标，由 §3.3 填充，元素为 `dict` 含 `bbox: list[float]`，见 `extract_char_bboxes` 返回结构）。**类型转换（关键）**：`types.PageResult.char_boxes` 字段类型为 `list[list[list[int]]]`（每行→该行逐字 `[x1,y1,x2,y2]`），而 `extract_char_bboxes` 返回 `List[Dict]`（每字含 float `bbox`）。需显式折算，否则按字面塞入 list-of-dicts 会令落库/导出报错：
  ```python
  page_char_boxes: list[list[list[int]]] = []
  for line in lines:
      raw = line.get("char_bboxes") or []
      page_char_boxes.append(
          [[int(round(b)) for b in d["bbox"]] for d in raw]   # d["bbox"] = [x1,y1,x2,y2] 浮点
      )
  PageResult(page_num=..., char_boxes=page_char_boxes or None, ...)
  ```
  **注意**：主线 `LineResult` 类型**不含** `char_boxes` 字段（已核对 `types.py:52` 仅 `PageResult.char_boxes` 与 `AdapterPageResult.char_boxes` 持有）。字符框只挂在 `PageResult.char_boxes` 层；`storage/db.py::save_book_result` 据此按行落 `line.char_boxes`（与 Phase 1/2 主线一致）。转换器**不要**往 `LineResult` 写 char_boxes。
- **段落**：初版**整页单段**（para_seq=1），与主线 `run.py` mock/VLM 路径一致；层级键按位置派生（1-based），与 Phase 3 导出/导入回路自洽。多段版式切分后续接目录/版式检测。
- 多引擎 `engine_results` / `disputed` / `dispute_reason` 可按需映射到 `LineResult.engine_texts` / `glyph_status`（初版可选，先保证文本+框闭环）。

### 3.2 修复闭环 + book_code 贯通（G1）

三处改动（缺一不可，首轮评审漏了第 3 点导致闭环仍断）：
- **适配器初始化**：`BookPipelineAdapter.__init__` 接收并保存 `pipeline_config`（kimi 引擎配置 dict），不再依赖 `run_book` 传入；`run_book` 签名加必填 `book_code`。
- **注册中心注入配置（CRITICAL）**：`engine/run.py:64` 当前为 `adapter=BookPipelineAdapter("kimi", temperature=0.0)`，**从未调用 `_build_engine_config()`** → 适配器 `self._pipeline_config=None` → `run_book` 触发 RuntimeError，G1 不达。改为：
  ```python
  adapter=BookPipelineAdapter("kimi", pipeline_config=_build_engine_config(), temperature=0.0),
  ```
  `_build_engine_config()` 已存在于 `run.py:143`（从环境变量构造 kimi config），此处直接复用。
- **orchestrator 调用贯通**：`orchestrator.py:423` 改为 `tier1_result = adapter.run_book(pdf_path, book_code=book_code)`。适配器在 `_init_v07_registry` 构造时已通过 `__init__` 拿到 `pipeline_config` 并完成 `_ensure_pipeline`，故 `run_book` 不再崩；返回的 `BookResult.book_code` = 真实 `book_code`（G2 主键一致）。

```python
# engine_runners.py
class BookPipelineAdapter:
    def __init__(self, engine_name="kimi", pipeline_config=None, temperature=0.0):
        ...
        self._pipeline_config = pipeline_config
        if pipeline_config:
            self._ensure_pipeline(pipeline_config)

    def run_book(self, pdf_path: str, *, book_code: str, **kw) -> BookResult:
        if self._pipeline is None:
            if self._pipeline_config is None:
                raise RuntimeError("BookPipelineAdapter 未配置 pipeline_config")
            self._ensure_pipeline(self._pipeline_config)
        self._pipeline.process_book(pdf_path, book_code or "TCM-UNK")
        return book_result_from_tcm_ocr(
            self._pipeline.page_results,
            book_code=book_code or "TCM-UNK",
            engine_label=self.engine_name, **kw,
        )
```
Celery 任务仍直接调 `process_book`（不经适配器），不受影响。

### 3.3 接入 char_boxes（G3，已修正坐标域）

`PagePipeline._multi_engine_recognition`（`page_pipeline.py:597`，PaddleOCR 分支）：
- 对识别出的行 ROI（`roi = page_img[y1:y2, x1:x2]`，:593），先取字符级细节 `char_details = engine.recognize_char_level(roi)`（`paddleocr_adapter.py:147`，与 `graded_scheduler.py:259` 同款），再调
  `extract_char_bboxes(det_box, char_details, orig_line_h, orig_line_w)`（:414）。
- **`orig_line_h/orig_line_w` 取行 ROI 自身尺寸**：`orig_line_h, orig_line_w = roi.shape[:2]`（即该行裁剪图的高/宽）。**不要传整页高/宽**——`extract_char_bboxes` 的 scale 公式用 `orig_line_h / rec_input_h` 把 CTC 时间步反投影回行 ROI 坐标系，传整页高会放大偏移、破坏框精度。
- **坐标域**：`extract_char_bboxes` 的 docstring 示例证明它**直接返回页绝对坐标**（det_box 传入页绝对行框，返回 bbox 的 y∈[det_y1,det_y2]、x∈[det_x1,det_x2]）。**不要再额外偏移 det_box**，否则双偏移把框推出页外。
- 把每行 `char_bboxes`（页绝对，`List[Dict]`，每 dict 含 float `bbox`）写入 `page_results[i]["lines"][j]["char_bboxes"]`；转换器（§3.1）据此折算为 `PageResult.char_boxes`（`list[list[list[int]]]`，详见 §3.1 类型转换）。`LineResult` 无 `char_boxes` 字段。

> 备注：tcm_ocr 字符框来自 CTC 反投影（`extract_char_bboxes`），与主线 `return_word_box=True` 是**两条独立来源**；统一目标是"都能产出 `char_boxes`"，不强行共用同一实现。识别 char_level 多一次前向，属性能开销，仅 PaddleOCR 路径触发。

### 3.4 统一落库（G2）

落库**统一由 `run_engine` 处理**（`kzocr/engine/run.py:131` 的 `KZOCR_PERSIST_DB` 开关，以真实 `book_code` 落 `BookDB.persist_book_result`）。**适配器 `run_book` 不再内嵌落库**——否则「run_engine → 适配器 → run_engine 再次落库」会双重写（虽 UPSERT 幂等无数据丢失，但 kimi 路径每本多一次全量 DB 写，且与主线 `PaddleOCRAdapter.run_book` 不落库、`run_engine` 统一落库的做法不一致）。

> 实施修正（code review WARNING）：初版设计把落库放在适配器内，复审发现与 `run_engine` 落库重复，已改为仅由 `run_engine` 落库，适配器只产出归一化 `BookResult`。

**不删除** book_pipeline 自建 snake_case SQLite（Celery 下游读者仍在），但明确主线 BookDB 为**权威 of record**，新读/导出均走主线 BookDB。

> **已知分歧（WARNING）**：上述落库仅发生在「主线 `run_engine` → 适配器 `run_book`」路径。`celery_tasks/tasks.py` 仍直接调 `process_book`（不经适配器），**不会**走 `KZOCR_PERSIST_DB` 落主线 BookDB——Celery 生产链路仍只写自建 snake_case SQLite + Postgres 归档。本轮**刻意不改造 Celery 路径**（避免触碰生产异步链路）；其统一落库留作后续任务。测试以适配器路径为准，Celery 路径仅保证不被改动破坏。

### 3.5 落实决策 #2（G4，已修正读方）

`kzocr/tcm_ocr/pipeline/archival.py`：
- `_archive_engine_results`（写 `OCRLineResultArchive`）：加开关 `KZOCR_ARCHIVE_LINE_RESULTS`（默认 `"0"`），**默认不写**逐行 OCR；保留 `_archive_content_tree`（`BookContentTree` 始终写）。
- 修正统计读方：因 `ocr_result_count` 来自 `OCRLineResultArchive`，默认停写后该统计恒为 0。处置（INFO，二选一，推荐移除列）：
  - **推荐**：直接**删除 `ocr_result_count` 列**——`runtime_db.get_book_stats(:2012)` 移除该子查询、`manager.get_book_stats(:283/294)` 不再返回该键。逐行统计本就与「Postgres 只存元数据」决策 #2 矛盾，移除最干净；grep 确认 `web/app.py` 等消费方不依赖该字段。
  - 备选：改为由主线 `BookDB.line` 表行数派生（需 `book_path → kzocr.storage.db.BookDB`），但引入跨库依赖，不如直接删列。

### 3.6 复用导出/导入闭环（G5）

tcm_ocr 产出的 `BookResult` 直接可被现有 `push_book_to_zai` / `import_proofread_package` 消费（它们接收 `BookResult`）。**无需新适配器代码**。补冒烟测试：转换器产 `BookResult` → `push_book_to_zai` → 改 `humanFinal` → `import_proofread_package` 写回 BookDB。

### 3.7 关于 `manager.py` PascalCase BookDB（**不删**）

`kzocr/tcm_ocr/database/sqlite/book_db.py` 被 4 个生产模块真实 import（见 §1.3），**本轮不删除、不强行改其消费者**。统一策略：保留 `book_db.py` 现状；主线 BookDB 作为**新增的权威 of record**，两套库并存但新逻辑以主线 BookDB 为准。后续单列「把 4 个 knowledge 消费者迁移到 `kzocr.storage.db.BookDB`」任务，彻底退役 `book_db.py`。`DatabaseManager` 的 Postgres 协调职责与 `RuntimeDB` 保留（`to_zai_prisma.py` 依赖 `RuntimeDB`）。

---

## 4. 范围边界（本轮）

**纳入**：3.1 转换器 + 单测；3.2 适配器闭环修复 + orchestrator 调用贯通（pipeline_config/book_code）；3.3 char_boxes 接入（recognize_char_level + extract_char_bboxes，页绝对坐标）；3.4 适配器层落主线 BookDB；3.5 决策 #2 开关 + get_book_stats 统计修正；3.6 复用闭环 + 冒烟测试。

**不纳入（后续另立）**：
- 不重写 tcm_ocr 识别/调度内部（graded_scheduler、多引擎融合保留）。
- **不删除 `book_db.py` 及其 4 个消费者**（保留为 legacy，仅降级权威地位；迁移另立任务）。
- 不删除 book_pipeline 自建 snake_case SQLite 写（保留 legacy 兼容）。
- 不改造 `web/app.py` 校对台前端（仍读 custom.db / BookContentTree）。
- 多段落版式切分（初版整页单段）；`engine_results`/`disputed` 完整映射（初版可选）。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| char_boxes 坐标域双偏移 | 3.3 直接用 `extract_char_bboxes` 返回的页绝对坐标，不额外偏移；测试断言坐标在页范围内 |
| 行序非阅读序导致错位 | 3.1 转换器先按 bbox y 排序；测试补"乱序→排序映射"用例 |
| `fused_text` 取错字段得空 | 3.1 明确映射 `fused_text`（已核实 `text` 多空） |
| 停写 `OCRLineResultArchive` 使 `ocr_result_count` 恒 0 | 3.5 直接删除 `ocr_result_count` 列（与决策 #2 一致） |
| Celery 路径不经适配器、不落主线 BookDB | 3.4 标注为已知分歧，本轮不改造 Celery 生产链路 |
| 删 `book_db.py` 误伤生产链路 | 3.7 明确不删，保留 legacy |
| kimi 路径缺 pipeline_config 仍崩 | 3.2 适配器 `__init__` 存 pipeline_config 并预初始化 |
| Celery 下游读自建 snake_case SQLite | 保留其写，不破坏既有读者；仅切换权威源 |

---

## 6. 测试计划

- `tests/test_book_result_convert.py`：fixture `page_results`（含 `fused_text`/`confidence`/`bbox`/`char_bboxes`、乱序行）→ `BookResult`，断言 page_num 取自 `page_number`、行按 y 排序、文本取自 `fused_text`、`char_boxes` 页绝对且结构正确、层级键位置派生。
- `tests/test_tcm_ocr_persist.py`：`book_result_from_tcm_ocr` → `BookDB.persist_book_result`（KZOCR_PERSIST_DB=1）→ `get_page_char_boxes` 读回一致（含 char_boxes）。
- `tests/test_tcm_ocr_push_loop.py`：转换器产 `BookResult` → `push_book_to_zai` → 改 `humanFinal` → `import_proofread_package` 写回 BookDB（验证 G5 复用闭环）。
- `tests/test_bookpipeline_adapter.py`：`BookPipelineAdapter.run_book` 在注入 `pipeline_config` + `book_code` 后返回 `BookResult` 且 `book_code` 正确（用 mock/stub `process_book` 注入 `page_results`，避免真跑引擎）；`orchestrate_book` 能消费其返回。
- `tests/test_archive_line_results_off.py`（G4）：默认 `KZOCR_ARCHIVE_LINE_RESULTS` 未开时 `archive_to_postgresql` / `_archive_engine_results` **不写** `OCRLineResultArchive`（用 stub/count 断言无 INSERT）；`BookContentTree` 仍写；并验证 `runtime_db.get_book_stats` 不再引用已删除的 `ocr_result_count` 列（或按其新返回结构断言）。
- **既有测试回归（必做）**：`tests/test_engine_runners.py` 中 `adapter.run_book("fake.pdf")` 调用（:21/:47/:61/:69）在 `BookPipelineAdapter.run_book` 改为必填 `book_code` 关键字参后会抛 TypeError。实施时同步改为 `adapter.run_book("fake.pdf", book_code="TEST-CODE")`；若其中某些用例用的是其它适配器（mock/vlm），确认其 `run_book` 协议签名是否也需 `book_code`，统一补齐（grep `run_book(` 全仓确认波及面）。

---

## 7. 验收

1. `BookPipelineAdapter.run_book(pdf_path, book_code=...)` 返回 `BookResult`（`book_code` 正确），`orchestrate_book` 正常消费（断点修复，G1）。
2. tcm_ocr 路径在 `KZOCR_PERSIST_DB=1` 时落主线 `BookDB`，且含 `char_boxes`（页绝对，G2/G3）。
3. `KZOCR_ARCHIVE_LINE_RESULTS=0`（默认）时 `OCRLineResultArchive` 不写，`BookContentTree` 仍写，`get_book_stats` 已移除 `ocr_result_count` 列（不再依赖逐行归档，G4 与决策 #2 一致）。
4. tcm_ocr 产 `BookResult` 走通 `push_book_to_zai` / `import_proofread_package`（G5）。
5. `ruff` 通过；上述新增单测 + 既有 `tests/` 全绿（目标全量 585+ 保持）。
