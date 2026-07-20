# KZOCR 下一轮开发任务计划

> 起草：2026-07-20（v0.21.0 零资源收口 A/B/C/D 完成、全量 820 passed 已推送 main）
> 适用：下一开发会话的待办候选。按价值/风险分层，默认仍走 **main 直推 + 零资源优先** 工作流。

## 当前基线（已核实）
- 版本 **v0.21.0**；全量 **820 passed + 2 skipped + 2 deselected**；ruff 默认与 `--select ANN` 三引擎文件均通过。
- 已落地：v0.7 自适应编排层、DB 分层（BookDB 内容表 + 导出/导入校对闭环）、真实引擎（PaddleOCR/RapidOCR/GLM-4V-Flash）+ 性能基线、Celery 生产接线（已端到端验证）、25 本扩面分歧实测 v4、high 占比二级判据、§5.5 Box-Guided VL 仲裁、零资源收口 A/B/C/D。
- 覆盖率：核心模块 ~83%、ratelimit 95%、web/app.py 53%、prompt_manager 98%、scheduler 各模块 84–100%、registration 100%。
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

### W5  Celery 生产可观测性
- **背景**：worker 已打印 celery 版本与 broker（3fb3738）；但处理吞吐、队列深度、BookDB 落库成功率、VL 调用数无聚合视图。
- **范围（可选，按需求裁剪）**：
  - `process_book_task` 结束记结构化日志（页数/分歧数/VL 调用数/BookDB 落库成功与否/耗时）。
  - 可选：暴露 Prometheus 指标（task 计数、耗时直方图）。**先确认是否真需要再实现**，避免引入 prometheus-client 依赖。
- **验收**：单测断言日志字段完整；生产接线回归测试覆盖落库失败路径。

### W6  xref 损坏渲染回检专项
- **背景**：v4 扩面发现速查表 p30 等源文件有 MuPDF xref 损坏告警（文本层缺失）。`--body-start` + `render_page` 返回 `healthy=False` 标记已加，但**未做专用回检闭环**。
- **范围**：对 `e2e_expand/summary_v4.json` 中带 `render_warnings` 的书/页，跑专用渲染回检脚本（截图 + 人工可读清单），确认是否系统性丢字；结果归档 `docs/e2e-expand-divergence.md` 或独立报告。
- **验收**：产出回检报告；若有丢字，回到版心裁切/渲染参数调优（属运行时调参，零代码风险）。

---

## P2 — 功能深化

### W7  终校反馈闭环 → 混淆集自动回流
- **背景**：`review_manifest` 收集人工终校修正；`add_learned_confusion` 已支持写入自学习混淆集（342fddf 有单测）。目前两者未自动联动。
- **范围**：人工 resolve 的 high 分歧（VL 未决/人工更正）自动 enrich `learned_confusion` 两层形近字黑名单，形成「分歧→仲裁→终校→回流」增强闭环。
- **验收**：端到端单测（人工修正 → 混淆集新增 → 下次同形近字自动路由）；需确认回流去重/置信阈值策略。

### W8  覆盖率门禁进 CI
- **背景**：CI（`test.yml`）目前仅 `pytest` + `ruff`，无覆盖率门禁；`coverage_report/` 为历史产物。
- **范围**：在 `test.yml` 加 `coverage run -m pytest` + `coverage report --fail-under=X`（X 取当前核心模块最低合理值，如 80），避免覆盖率回退。**先与用户确认阈值**，避免误伤。
- **验收**：CI 在覆盖率跌破阈值时失败；本地可复现。

---

## P3 — 技术债务（建议暂缓）

### W9  tcm_ocr 全栈 ANN 补全（192 处）
- 全栈仍 192 处 ANN 缺失（44 ANN001 / 27 ANN201 / 17 ANN003 / 94 ANN401 等）。D 方向已清三核心引擎文件（14 处）。
- **决策**：tcm_ocr 是冻结平行栈，补全收益低、易引入错误；建议维持排除，仅在触碰具体文件时顺手补，**不专项投入**。

### W10  tcm_ocr 与主线 BookDB 统一
- tcm_ocr 仍保留自有 `BookDB`（`kzocr/tcm_ocr/database/sqlite/book_db.py`，被 4 个 knowledge 模块 import）与 `archival.py`；Phase 4 已让其经 `BookPipelineAdapter` 产主线 `BookResult` 并走主线 BookDB。
- **决策**：属长期架构整合，风险高；当前双轨共存已验证无损，**非紧急**。

---

## 推荐下轮启动顺序
1. ~~**W4（VL 预算控制）**~~ ✅ 已实现（v0.21 后续）。
2. **W1（web 补测续）**——延续零资源快赢节奏，低风险提升覆盖。
3. 视用户优先级再选 W5/W6/W7。
4. P3 两项目前不建议投入。

> 注：以上为候选清单，非承诺范围。下轮开始前建议与用户确认本轮回聚焦哪 1–3 项。
