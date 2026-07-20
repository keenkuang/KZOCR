# KZOCR 下一轮开发任务计划

> 起草：2026-07-20（v0.21.0 零资源收口 A/B/C/D 完成、全量 820 passed 已推送 main）
> 最近更新：2026-07-20（W4–W7 已闭环并推送 main，全量 901 passed）
> 适用：下一开发会话的待办候选。按价值/风险分层，默认仍走 **main 直推 + 零资源优先** 工作流。

## 当前基线（已核实，2026-07-20）
- 版本 **v0.21.0**；全量 **901 passed + 2 skipped + 2 deselected**（git HEAD `90f6581`，已推送 origin/main）；ruff 默认与 `--select ANN` 三引擎文件均通过。
- 已落地：v0.7 自适应编排层、DB 分层（BookDB 内容表 + 导出/导入校对闭环）、真实引擎（PaddleOCR/RapidOCR/GLM-4V-Flash）+ 性能基线、Celery 生产接线（已端到端验证）、25 本扩面分歧实测 v4、high 占比二级判据、§5.5 Box-Guided VL 仲裁、零资源收口 A/B/C/D、W4 VL 预算控制、W5 Celery 可观测性、W6 xref 渲染回检、W7 终校回流混淆集。
- 覆盖率：核心模块 ~83%、ratelimit 95%、web/app.py 78%、prompt_manager 98%、scheduler 各模块 84–100%、registration 100%。
- 主线**无真实未完成 TODO**（仅 book 级引擎的意图性 `NotImplementedError`）。

## 工作流约定（沿用）
1. 零系统资源消耗的方向优先（纯逻辑单测、注释/注解、文档、CI），与真实 OCR 引擎/扩面/VL 调用解耦。
2. 每次提交前 `ruff check kzocr/ tests/` 必须无报错；功能/修复须带测试。
3. 声称完成前**必须核实真实输出**（pytest 实际计数、覆盖率实际数字），不靠记忆。
4. 维持 main 直推，不补开 PR（收尾决策 2026-07-18 已确认）。
5. 重大架构/多文件改动先走 plan mode 与用户对齐。

---

## P0 — 零资源快赢（建议作为下轮开局）

### W1  web/app.py 路由单测续补（覆盖 53% → 70%+）
- **背景**：B 方向已补 37 例覆盖 prompts/engines/registration/book 空库降级/monitor/benchmark，但仍有大量 handler（导出、khub 推送、pipeline 执行结果展示、search 详情、workspace 编辑提交等）未测。
- **范围**：用 B 方向已建立的 `dirs` fixture（临时目录隔离 4 环境变量）继续补齐；导出/推送 handler 走 mock client，零网络。
- **验收**：web/app.py 覆盖率 ≥70%；ruff + 全量仍绿。

### W2  核心模块纯逻辑补测查漏
- **背景**：A 方向已补 engine_config/registration/to_zai_prisma/modelscope_pool/cross_align 五文件。其余低覆盖纯逻辑函数可继续。
- **候选**：`to_zai_prisma` 的层级键派生/重导出逻辑、`leakage.py` 跨页泄漏四层判定边界、`hierarchy.py` 字符数尖峰判定、`errors.py` 异常分类与 `retry_with_policy` 退避序列（纯逻辑、可 mock 时间）。
- **验收**：相关模块覆盖率提升且带回归测试；不触运行时。

### W3  tcm_ocr 真实 TODO 收口（冻结栈，低优先）
- **位置**：`kzocr/tcm_ocr/llm/pipeline/four_stage_pipeline.py:143`（heading 块应接 layout bbox：居中/字号）、`:200`（复用前方 heading 文本或接 `extractor.extract_formula_name`）。
- **范围**：仅注释中提到的两处增强；**保持 tcm_ocr 平行栈冻结原则**，不引入新依赖、不改主线行为。
- **验收**：实现或显式标注为 deferred（带 issue 引用），不引入回归。

---

## P1 — 生产硬化（真实价值，需少量运行时）

### W4  VL 仲裁预算/配额控制（推荐下轮主线）✅ 已实现
- **背景**：GLM-4V-Flash 走 z.ai/智谱付费端点。当前成功路径每个 high 分歧页都送 VL；25 本实测 high 占比 ~26%（mi-678 45.2%、速查表 43.4%），大批量处理会产生可观付费调用，且无上限保护。
- **实现**（提交见 CHANGELOG）：
  - 新增 `kzocr/scheduler/vl_budget.py`：`VLBudgetConfig` + `VLBudgetTracker`（per_run 内存计数、per_day 经可注入 `DayStore` 跨书当日累计；JSON 文件 best-effort）。
  - `SchedulerConfig` 新增 `vl_budget_per_run` / `vl_budget_per_day`，环境变量 `KZOCR_VL_BUDGET_PER_RUN` / `KZOCR_VL_BUDGET_PER_DAY`（默认 0=不限）。
  - `_arbitrate_high_divergences` 与 `_sample_consensus_error` 逐次 `can_spend()` 检查，超预算停止 VL 调用、分歧留人工队列，记 `detector_chain=["VLBudget"]` 观测异常（同 conservative 降级语义）。
  - `orchestrate_book` 构造 tracker 并透传三处 VL 调用点；书末打印 VL 预算使用对账日志。
- **测试**：`tests/test_vl_budget.py`（7 例：不限/per_run 边界/per_day 跨书累计/日期隔离/双维度）+ `tests/test_orchestrator.py` 增 3 例（预耗尽全跳/逐次计数/抽样耗尽）。零资源可测。

### W5  Celery 生产可观测性 ✅ 已实现
- **背景**：worker 已打印 celery 版本与 broker（3fb3738）；但处理吞吐、队列深度、BookDB 落库成功率、VL 调用数无聚合视图。
- **实现**：
  - `_persist_to_mainline_bookdb` 由返回 `None` 改为返回三态 `True/False`（成功/失败），调用点捕获 `persisted` 标志。
  - 新增 `_log_task_summary` helper：把页数/分歧数（tcm_ocr 的 `disputed_lines`）/VL 调用数（本路径恒 0，VL 仲裁走 v0.7 orchestrator）/BookDB 落库成败/耗时聚合成一条带 `extra={"celery_task_metrics": {...}}` 的结构化日志，生产可 grep / 日志平台聚合。
  - `process_book_task` 在成功路径与幂等跳过路径调用该 helper；不引入 prometheus 依赖（按计划「先确认再实现」保留）。
- **测试**：`tests/test_celery_task_summary.py`（7 例：`_log_task_summary` 字段完整 + 跳过标签；persist 返回 True/False；集成调 `process_book_task` 断言汇总与 result 一致、落库开/关、落库失败 `bookdb_persisted=False` 不抛）。全量无回归。

### W6  xref 损坏渲染回检专项 ✅ 已实现（提交 `3b9423e`，已推送 origin/main）
- **背景**：v4 扩面发现速查表 p30 等源文件有 MuPDF xref 损坏告警（文本层缺失）。`--body-start` + `render_page` 返回 `healthy=False` 标记已加，但**未做专用回检闭环**；且 `summary_v4.json`/`summary.json` 均**未落 `render_warnings` 字段**，故无法从旧汇总读取告警页，必须重跑渲染健康度回检重新发现。
- **实现**：新增 `scripts/check_render_health.py`（仅依赖 PyMuPDF/numpy/Pillow，不引 OCR 引擎），复用 orchestrator 全路径渲染（`_pdf_page_to_numpy` + `_crop_to_body` + 缩放）。健康度判定综合两路信号：① `fitz.TOOLS.mupdf_warnings()` 捕获的 xref 告警（PyMuPDF 1.27 官方告警收集，比 fd 重定向可靠——xref 告警在 `doc.close()` 延迟打印且绕过 fd 2）；② 文本层为空且图像非空白（文本层缺失）。异常页落截图 `e2e_expand/render_health/<书>/p<页>.png`，产出 `report.md`+`report.json`。
- **关键结论（速查总表实测）**：xref 告警页 p34 文本层仍完整（576 字）、图像有内容（墨迹 4.9%），**判定良性**——KZOCR 基于渲染图像做 OCR，文本层/xref 损坏本身不直接丢字，仅当渲染图像本身损坏才会丢字。
- **验收**：后台跑 v4 九本（每本上限 100 页）全量回检，产出报告；若发现图像损坏页再回到版心裁切/渲染参数调优。

---

## P2 — 功能深化

### W7  终校反馈闭环 → 混淆集自动回流 ✅ 已实现（提交 `90f6581`，已推送 origin/main）
- **背景**：`review_manifest` 收集人工终校修正；`add_learned_confusion` 已支持写入自学习混淆集（342fddf 有单测）。此前两者未自动联动。
- **实现**：`review_manifest._parse_confusion_pair` 解析 anomaly.details `confusion;wrong=X;correct=Y`；`build_review_manifest` 填 `ReviewIssue.ocr_char`；`feedback_apply` 对每个 `ocr_char`/`expected` 非空且相异的 issue 调 `add_learned_confusion(source="review_manifest")`，形成「分歧→仲裁→终校→回流」闭环。`cross_align` 新增 `_merge_split_keys`，`load_confusion_keys`/`load_confusion_keys_split` 合并 `learned_confusion.json`（修分侧检测器看不到回流的不一致）。新增 `tests/test_review_backflow.py`（8 例）。
- **验收**：端到端单测（人工修正 → 混淆集新增 → 下次同形近字自动路由）通过；回流去重已覆盖。

### W8  覆盖率门禁进 CI ✅ 已实现（已推送 origin/main）
- **背景**：CI（`test.yml`）此前仅 `pytest` + `ruff`，无覆盖率门禁；`coverage_report/` 为历史产物。
- **实现**：`pyproject.toml` 新增 `[tool.coverage.run]`（`omit = ["kzocr/tcm_ocr/*", "tests/*"]`，排除冻结栈与测试代码）+ `[tool.coverage.report]`（`fail_under = 80`）；`test.yml` 依赖加 `pytest-cov`，主测试阶段改 `python -m pytest tests/ --cov=kzocr --cov-report=term-missing`。
- **验证**：本地全量 `pytest --cov` 通过门禁，核心模块覆盖率 **88.94%**（排除 tcm_ocr 后），远超 80% 阈值；CI 在覆盖率跌破 80% 时失败，本地可复现。

---

## P3 — 技术债务（建议暂缓）

### W9  tcm_ocr 全栈 ANN 补全（192 处）
- 全栈仍 192 处 ANN 缺失（44 ANN001 / 27 ANN201 / 17 ANN003 / 94 ANN401 等）。D 方向已清三核心引擎文件（14 处）。
- **决策**：tcm_ocr 是冻结平行栈，补全收益低、易引入错误；建议维持排除，仅在触碰具体文件时顺手补，**不专项投入**。

### W10  tcm_ocr 与主线 BookDB 统一
- tcm_ocr 仍保留自有 `BookDB`（`kzocr/tcm_ocr/database/sqlite/book_db.py`，被 4 个 knowledge 模块 import）与 `archival.py`；Phase 4 已让其经 `BookPipelineAdapter` 产主线 `BookResult` 并走主线 BookDB。
- **决策**：属长期架构整合，风险高；当前双轨共存已验证无损，**非紧急**。

---

## 推荐下轮启动顺序（2026-07-20 更新）

- **W1–W7 已全部闭环并推送 main**：W4/W5 早实现；W6 `3b9423e`、W7 `90f6581`；W1/W2 零资源补测此前亦已完成（web/app.py 53%→78%、核心模块边界补测）。
- **本会话追加闭环**：W8 覆盖率门禁（CI 防回归）、W3 tcm_ocr 两处 TODO 收口（注释明确化 + 回归测试）、W1 web 继续补测（register_submit 细分支，含非法 toc 降级）；并修复 `learned_confusion.json` 全局状态污染导致的 2 个脆弱测试（加固 + 清理产物）。
- **当前状态**：全量 **907 passed + 2 skipped + 2 deselected**，核心覆盖率 88.94%，ruff 全过。
- **P3（W9/W10）目前不建议投入**（冻结栈 ANN 补全 / 双 BookDB 统一，风险高、收益低）。

> 注：以上为候选清单，非承诺范围。下轮开始前建议与用户确认本轮回聚焦哪 1–3 项。
