# 软件工程评审 — Round 1 (v0.7 自适应 OCR 引擎编排层)

| 字段 | 值 |
|---|---|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.7.md` |
| 审查角色 | 软件工程 / 代码质量 |
| 审查人 | code-reviewer-7 |
| 日期 | 2026-07-10 |
| 代码基线 | `kzocr/engine/run.py`, `kzocr/engine/types.py`, `kzocr/config.py`, `kzocr/engines/errors.py`, `kzocr/engines/hierarchy.py` |

---

## 总体判断

**方案方向正确，架构设计清晰，但存在 2 项阻塞级问题（Orchestrator 伪代码与实际数据模型脱节、Tier 1 引擎不可独立调用假设）和多项中等风险问题。建议修复阻塞项后进入下一轮。**

方案将当前 `run.py` 中的硬编码 if-else 体系重构为 Registry→Scheduler→Verifier→Orchestrator 的四层架构，是合乎逻辑的演进方向。代码组织（集中到 `kzocr/scheduler/` 包）与现有模块化风格一致。

---

## 逐项评审

### E1: EngineRegistry（引擎注册中心）— 中等风险

**实施难度：⭐⭐⭐☆☆（中等）** — 新增文件，不破坏现有逻辑，可独立开发测试。

**代码风格一致性：✅ 良好**

`EngineRegistration` / `EngineStats` 使用 `@dataclass`，与 `types.py` 的 `ProbeResult`、`AdapterMeta` 风格一致。`probe_engines()` 复用现有 `ProbeResult` → 扩展为逐引擎探测，这条路径清晰。

**发现的问题：**

1. **EngineStats.avg_latency_per_page_ms 与 glyph_pass_rate 的维护方式未定义。** 这两个派生字段可以在每次 `record()` 时实时计算（以 `total_latency_ms / total_pages`），也可以由外部聚合查询时计算。方案应明确：是存原始累加值还是实时计算值。建议只存原始累加值（`total_calls`、`total_latency_ms`、`glyph_pass_count`、`glyph_fail_count`），派生值在访问时计算——避免更新顺序导致的不一致。

2. **缺少 `unknown_count` 字段。** `glyph_pass_rate` 定义为 `pass / (pass + fail + unknown)`，但 `EngineStats` 没有 `glyph_unknown_count` 字段。`GlyphStatus` 有 `UNKNOWN` 状态，历史统计应追踪它。建议增加 `glyph_unknown_count: int = 0`。

3. **`last_seen` 使用 `time.monotonic()`** — monotonic clock 适合测量间隔但不适合记录"最近一次看到的时间戳"。如果进程重启，monotonic 值会重置到 0。建议改用 `time.time()`，或者同时存 wall-clock 时间用于跨进程持久化。

4. **benchmark 持久化路径未定。** 方案提到 `KZOCR_BENCHMARK_DIR` 但未说明格式（JSON / SQLite / pickle）。若每引擎独享一个 JSON 文件，并行写入存在竞态。若用 SQLite，需要 schema 设计。建议用每个引擎一个独立 JSON 文件 + 原子写入（复用 `kzocr/engines/atomic.py`），避免竞态。

---

### E2: EngineScheduler（引擎调度器）— 高风险

**实施难度：⭐⭐⭐⭐☆（高）** — 调度策略的首次实现需要验证和调优。

**代码风格一致性：✅ 良好**

`select_candidates()` 函数签名清晰，返回 `list[EngineRegistration]`。Tier 定义明确，与架构图一致。

**发现的问题：**

1. **🔴 阻塞：冷启动问题未处理。** `glyph_pass_rate` 和 `avg_latency` 在首次运行时均为 0/0.0。`0 × (1/0) = NaN` 或 `0 × ∞ = 0`，导致排序无效。所有新引擎在第一次调用前无法区分优先级。方案需要：
   - 为 `glyph_pass_rate` 定义**默认值**（如 0.5 — 中等置信度假设）
   - 为 `avg_latency` 定义**默认值**（如 1000ms — 保守估计）
   - 或：首次运行时按预设优先级（Tier 内定序）排序，积累数据后切换为数据驱动

2. **🔴 阻塞：选择策略未考虑引擎健康状态的时效性。** 如果 `last_seen` 是 30 天前，但 `glyph_pass_rate` 仍显示 95%，系统会高估该引擎。建议引入**衰减因子**：`effective_score = score × decay(last_seen)`，时间越久置信度越低。

3. **`N` 的可配置性。** 方案说 `N 可配置，默认 2`，但未指定配置项。建议与 E5 中 `KZOCR_MAX_TIER1_ENGINES` 统一命名。

4. **层级约束与选择逻辑的交互不清晰。** 方案说"Tier 约束" + "资源过滤" + "预算检查"，但不清楚它们的执行顺序。如果预算不足，是跳过整个 Tier 还是减少候选数？建议明确顺序：资源过滤 → 预算检查 → 层级约束 → 加权排序 → 取 Top-N。

---

### E3: GlyphVerifier（字形验证器）— 中高风险

**实施难度：⭐⭐⭐⭐☆（高）** — 依赖多个外部资源和未完成的 B1/B5 设计。

**代码风格一致性：✅ 良好**

`GlyphVerdict` dataclass 与现有类型风格一致。复用 D4 层级异常检测和 C1 泄漏检测的路线合理。

**发现的问题：**

1. **🔴 阻塞：关键外部依赖未确认就绪。**
   - `resources/confusion_set.json`（B5 引用）是否存在？若不存在，形似混淆集检测就是空壳。
   - "药材名/术语知识库匹配" — 目前 `config.py` 有 `KZOCR_TERM_KB_PATH` 但未确认该知识库是否已加载为可查询的数据结构，也未确认性能和查询接口。
   - 建议：方案应明确标注这些外部资源的就绪检查点，或定义降级行为（资源不存在 → 跳过该检测器，仅记录日志）。

2. **性能瓶颈风险未评估。** 每页每引擎调用一次 `verifier.check()`，即每页调用 3~5 次。如果每次检查都要读知识库文件或执行大量正则，会成为吞吐瓶颈。建议：
   - 知识库启动时一次性加载到内存（`lru_cache`）
   - 为 Verifier 设置内部超时（单次检查 < 50ms）
   - 在方案中标注性能预算

3. **检测器的优先级和短路逻辑未定义。** 多个检测器（字符数尖峰、泄漏检测、知识库匹配、混淆集）的输出如何合并为单一 `GlyphVerdict`？举例：
   - D4 标记 `UNCERTAIN` 但知识库匹配为 `PASS` → 谁优先？
   - C1 泄漏检测为 `FAIL`，但字形匹配为 `PASS` → 谁优先？
   - 建议定义检测器优先级表，并支持检测器短路（某检测器 `FAIL` 直接跳过后续检测）。

4. **`details` 字段存储格式未定。** 多条规则触发时是拼接字符串还是 JSON？建议用结构化格式如 `"rule=char_count_spike,value=5000;rule=herb_match,name=黄芪"`，便于日志分析。

---

### E4: Orchestrator（编排主循环）— 高风险

**实施难度：⭐⭐⭐⭐⭐（最高）** — 方案的核心集成点，涉及所有新模块的组装。

**代码风格一致性：⚠️ 部分问题**

伪代码风格与现有代码风格基本一致（逐页循环、failed_pages 收集），但与 `types.py` 中 `BookResult.pages` 字段类型不匹配。

**发现的阻塞问题：**

1. **🔴 阻塞：`engine.run(page)` — EngineRegistration 没有 `run()` 方法。** 伪代码第 166 行 `result = engine.run(page)` 假设 `EngineRegistration` 是一个可调用的引擎实例，但 E1 定义的 `EngineRegistration` 只包含 `meta`/`config`/`status`/`stats` 四个字段，不包含引擎实例引用。这是方案中最大的设计脱节。需要：
   - 在 `EngineRegistration` 中增加 `adapter: Callable | None` 字段，或
   - 在 `EngineRegistry` 上提供 `get_adapter(name) → Callable` 方法，或
   - 让 `select_candidates()` 返回的不是 `EngineRegistration` 而是包装了可调用实例的新类型 `EngineInstance`

2. **🔴 阻塞：缺少单引擎执行错误处理。** 伪代码中每引擎调用没有 try/except。如果一个引擎抛出异常（网络超时、OOM、模型加载失败），整个 `orchestrate_book()` 会崩溃。需要用 try/except 包裹 `engine.run(page)` 并将失败记录到 `registry.record(engine, success=False, error=exc)`，然后继续尝试同 Tier 下一个引擎。

3. **`BookResult` 构建不完整。** 伪代码构建了 `pages_text: list[str]`，但 `BookResult.pages` 的类型是 `list[PageResult]`，需要从字符串转换为 `PageResult` 结构（包含 `LineResult`、`ParagraphResult`）。这需要类似当前 `_vlm_markdown_to_pages()` 的转换逻辑。方案应包含这个转换步骤。

4. **预算检查未在循环中体现。** 方案 E2 提到预算检查，但 Orchestrator 的伪代码没有在 Tier 间或页间检查预算消耗。如果 Tier 1 耗尽了全部预算，Tier 2/3 的调用应当跳过。需要在每个 Tier 入口处加预算检查。

5. **并发与串行的矛盾。** 架构总览的表格说"多引擎并行（同一页）"，但伪代码是串行 `for engine in engines`。如果要并行（同一页的多引擎同时跑），E2 需要返回 `Future` 或使用 `concurrent.futures`。如果串行就够（Tier 内按优先级逐一尝试），方案应去掉"并行"声明，避免混淆。

6. **Tier 1 引擎集合对真实可用性的假设不成立。** Tier 1 包含 paddleocr、rapidocr、mineru、unirec，但现状是这些引擎通过 `BookPipeline` 统一调用（`_run_real()` → `BookPipeline.process_book()`），并非各自有独立可调用接口。如果要逐个独立调用，需要在 kimi 引擎包中为每个 OCR 引擎暴露独立 API。这是**架构依赖**，方案未提及。

---

### E5: 现有关联文件修改 — 中等风险

**实施难度：⭐⭐⭐☆☆（中等）** — 触及文件多但改动范围明确。

**代码风格一致性：✅ 良好**

修改清单与现有模块职责一致。`run.py` 改为调用 Orchestrator 是合理的切入点。

**发现的问题：**

1. **`config.py` 新增字段名称规范问题。** 方案示例 `KZOCR_MAX_TIER1_ENGINES`（蛇形大写 env var）对应的 Python 字段名未指定。现有惯例是 `vlm_host`/`sensenova_api_key`（蛇形小写）。建议：
   - 环境变量：`KZOCR_MAX_TIER1_ENGINES`
   - Python 字段：`max_tier1_engines: int = 2`
   - 保持一致即可，但方案应明确映射关系

2. **`errors.py` 新增调度层异常。** 方案提到 `SchedulerError` 和 `AllEnginesFailedError`，与现有 `OcrError` 层级的关系未定义。建议：
   - `SchedulerError` 继承 `OcrError`（保持统一基类）
   - `AllEnginesFailedError` 集成 `RuntimeError`（属于编排层而非 OCR 层）

3. **`cli.py` benchmark 子命令未定义接口。** 方案的"新增 `kzocr benchmark` 子命令"是合理的，但未说明输入输出。基准测试需要标准化的测试集（已知答案的 PDF + 期望文本）。建议至少标注 benchmark 子命令的预期行为。

4. **`run.py` 的过渡策略未明确。** 方案说"`run_engine()` 改为调用 Orchestrator"，但未说明过渡期间的兼容策略——当前 `run_engine()` 的 mock/VLM/real 三条路径是否保留为 Orchestrator 内部的特殊 case？建议分两步：Step 1: Orchestrator 内部先包装当前 if-else 逻辑（作为简单调度器）；Step 2: 逐步替换为真实 registry/scheduler。

---

## 阻塞项（BLOCKING — 必须修复后方可实施）

| 编号 | 所属 | 问题描述 | 严重程度 |
|------|------|---------|---------|
| B1 | E2 | 调度器冷启动：空历史数据下 `glyph_pass_rate × (1/avg_latency)` 为 NaN/0，需定义默认值和降级排序策略 | CRITICAL |
| B2 | E4 | `engine.run(page)` 脱节：`EngineRegistration` 没有 `run()` 方法，方案需补充引擎实例引用机制 | CRITICAL |
| B3 | E4 | 单引擎错误处理缺失：伪代码未包裹 try/except，引擎异常会打穿整个编排循环 | CRITICAL |
| B4 | E4 | Tier 1 引擎不可独立调用：paddleocr/rapidocr/mineru/unirec 当前通过 BookPipeline 统一调用，非独立接口 | CRITICAL |
| B5 | E3 | 外部依赖就绪状态不明：`confusion_set.json`、术语知识库是否已存在可供查询的接口？ | HIGH |
| B6 | E2 | 健康状态时效性：`last_seen` 为 30 天前的引擎仍可能因历史高分被选中，需引入衰减因子 | HIGH |

---

## 建议项（SUGGESTED — 推荐修复，不强阻塞）

| 编号 | 所属 | 建议内容 | 优先级 |
|------|------|---------|--------|
| S1 | E1 | 简化 `EngineStats`：去掉派生字段，只存原始累加值，在访问时计算 | P3 |
| S2 | E1 | 增加 `glyph_unknown_count` 字段 | P2 |
| S3 | E1 | `last_seen` 改用 `time.time()` 而非 `time.monotonic()`，支持跨进程 | P2 |
| S4 | E1 | benchmark 持久化格式应明确，建议每引擎独立 JSON + 原子写入 | P2 |
| S5 | E2 | 调度顺序应明确：资源过滤 → 预算检查 → 层级约束 → 排序 → Top-N | P3 |
| S6 | E2 | `select_candidates()` 的默认 Top-N 值应作为 Config 字段暴露 | P3 |
| S7 | E3 | 定义检测器优先级表和短路规则（如 C1 FAIL 短路径直接标记 FAIL） | P2 |
| S8 | E3 | 设定单次验证性能预算（建议 < 50ms），知识库启动时一次性加载 | P2 |
| S9 | E3 | `GlyphVerdict.details` 应使用结构化格式（如 `key=val;key=val`） | P3 |
| S10 | E4 | 消除并行/串行矛盾：若串行则删除"多引擎并行"声明；若并行需引入 Future | P2 |
| S11 | E4 | 在 Tier 入口处增加预算消耗检查 | P2 |
| S12 | E4 | 补充 `pages_text → list[PageResult]` 的转换步骤 | P2 |
| S13 | E5 | 新增异常的继承关系应明确定义（建议 `SchedulerError` 继承 `OcrError`） | P3 |
| S14 | E5 | Config 字段应与现有 snake_case 风格一致，方案应列出完整映射表 | P3 |
| S15 | E5 | benchmark 子命令应定义输入格式（PDF 集 + 期望答案），以免留下未完成接口 | P2 |

---

## 实施顺序建议

### 依赖关系图

```
E1 ──→ E2 ──→ E4 ←── E3
                   ↑
E5 ←───────────────┘
```

### 评述

**E1→E2→E3→E4 的执行顺序基本合理**，但有两点需要注意：

1. **E2 和 E3 可部分并行开发。** `select_candidates()` 的调度逻辑和 `GlyphVerifier.check()` 的规则引擎没有依赖关系，可以在 E1 完成后并行开发。建议：
   - Week 1-2: E1（Registry）
   - Week 2-3: E2（Scheduler）+ E3（Verifier）并行
   - Week 3-4: E4（Orchestrator 组装）
   - Week 3-5: E5（修改关联文件，与 E4 并行）

2. **E4 应分两阶段实施，降低迁移风险：**
   - **Phase 1（增量适配）：** Orchestrator 的第一个版本只是包装当前的 `if use_mock → mock; if use_vlm → VLM; else → real` 逻辑，不改变行为。`run_engine()` 改为调用 `orchestrate_book()`，后者内部走原三条路径。此阶段风险最低，可快速上线。
   - **Phase 2（真实验替）：** 实现 E1/E2/E3，然后让 Orchestrator 的调度路径替换 if-else。可以加 feature flag（`KZOCR_USE_SCHEDULER=1`）做灰度切换。

3. **E5 中的 `run.py` 修改应与 E4 Phase 1 同步进行。** 不要先改 `run.py` 再等几周才完成 E4——这会导致 `run_engine()` 指向不存在或有 bug 的 Orchestrator。

### 推荐实施顺序

```
Phase 1（低风险，可独立交付）：
  └── E5: config.py 新增字段 + errors.py 新增异常 + types.py 扩展
  └── E4 Phase 1: Orchestrator 包装当前 if-else（run_engine → orchestrate_book 重命名）
  └── E5: run.py 简化

Phase 2（核心功能，需要充分测试）：
  └── E1: EngineRegistry（含 probe_engines + benchmark 持久化）
  └── E2: EngineScheduler（含冷启动默认值 + 衰减因子）—— 与 E3 并行
  └── E3: GlyphVerifier（含检测器优先级表 + 性能预算）—— 与 E2 并行
  └── E4 Phase 2: Orchestrator 接入真实调度（feature flag 保护）
  └── E5: cli.py benchmark 子命令

Phase 3（增强，可后续迭代）：
  └── Tier 1 引擎独立化改造（从 BookPipeline 拆出独立 OCR 调用）
  └── 多引擎并行（可选，需要线程安全）
```

---

## 边界情况检查

| 场景 | 方案处理 | 评估 |
|------|---------|------|
| 空引擎列表（`probe_engines()` 返回 `[]`） | 未显式处理 | ⚠️ E4 中 for 循环不会执行，verdict 保持 FAIL → 落入 HumanGate，但应记录 WARNING 日志 |
| 所有引擎 UNAVAILABLE | `select_candidates()` 返回空列表 | ⚠️ 同上。需要在 E2 入口加日志 |
| 全部引擎调用失败 | HumanGate 捕获 | ✅ 伪代码第 196 行正确处理 |
| 单引擎崩溃（非返回失败，而是抛出异常） | 未处理 | 🔴 B3：需要 try/except |
| 跨页恢复（第 5 页失败后第 6 页可否用不同引擎） | 隐式支持（每页独立调度） | ✅ 架构天然支持 |
| 引擎中途不可用（第 20 页时某个引擎 OOM） | 未显式处理 | ⚠️ 建议 E1 提供 `mark_unavailable(name)` 方法，调度器应尊重运行时状态变更 |
| 配置变更导致调度策略变化 | 未处理 | ⚠️ `_compute_config_hash` 风格的 hash 可复用，但方案未提及 |
| 超大 PDF（500+ 页）下 EngineStats 内存爆炸 | `total_calls` / `total_pages` 等累加器占用极小 | ✅ 无风险 |
| GlyphVerifier 无规则可匹配（空知识库） | 所有调用返回 `UNKNOWN` | ✅ 可接受，Tier 链正常继续 |

---

## 迁移风险评估

### 从当前 if-else 到 Orchestrator 的过渡策略

**风险等级：中高**

主要风险点：

1. **🧨 高：`run_engine()` 的 API 契约可能被破坏。** 当前 `run_engine()` 被 `cli.py:cmd_pipeline()` 和其他潜在调用方使用。如果 Orchestrator 首个版本的返回值格式与当前 `BookResult` 有任何差异，会导致 CLI 中断。**建议：** Phase 1 的 Orchestrator 直接委托给当前 `_run_vlm()` / `_run_real()`，不改返回值。

2. **🧨 中：`KZOCR_USE_MOCK` / `KZOCR_USE_VLM` 与调度器共存问题。** 如果 `use_mock` 为 True，Orchestrator 应该直接返回 mock 数据还是仍然走调度器但只包括 mock 引擎？建议：`use_mock=1` 和 `use_vlm=1` 作为 Orchestrator 的特殊调度策略（Scheduler 的硬编码 fallback），而不是与调度器互斥。这样 remove 环境变量是平滑的，而非 breaking change。

3. **✅ 低：测试文件新增而非修改。** `tests/test_registry.py` 等是全新文件，不与现有测试冲突。但需要注意 `test_vlm.py` 中的 `test_routes_to_vlm_when_use_vlm_is_true()` 等路由测试，在 Phase 1 后不应失效。建议在该测试中增加 `run_engine` → `orchestrate_book` 的别名兼容性断言。

4. **🧨 中：benchmark 数据的第一次运行缺乏基线。** benchmark 子命令需要在"有调度器之前"就能运行和录入数据，否则调度器首次启动时没有历史数据。建议在 E1 实现后立即实现 benchmark 子命令（移到 Phase 1 末尾），确保 E2 上线时已有数据。

---

## 可维护性/复杂度关注

1. **新增依赖关系图的复杂度。** 当前 `run.py` 是单文件 ~700 行，如果将所有逻辑拆入 4 个新文件（~150 行/文件），整体认知负担增加（需要理解 4 个类的交互），但每个模块的可测试性显著提升。净收益为正。

2. **EngineRegistration 与 AdapterMeta 的重叠。** `EngineRegistration.meta` 包含 `AdapterMeta`，而 `AdapterMeta` 已有 `name`/`label`/`kind` 等字段。`EngineRegistration` 新增的 `status`/`stats`/`config` 是否与 `AdapterMeta` 有字段重叠？建议 `AdapterMeta` 保持纯元信息不变，`EngineRegistration` 作为运行时包装器。

3. **GlyphVerifier 长期膨胀风险。** 随着更多检测规则加入（C1、D4、知识库、混淆集），`verifier.py` 可能变得臃肿。建议从第一天起将每个检测器实现为独立函数或类（`class CharCountSpikeDetector`, `class LeakageDetector` 等），`GlyphVerifier` 通过组合调用它们。这个模式在 `hierarchy.py:check_hierarchy_anomaly()` 中已有先例。

4. **Benchmark 数据文件格式锁定。** 一旦 JSON 格式发布并被用户使用，后续格式变更需要迁移。建议在 v0.7 阶段就定义带版本号的格式（如 `{"version": 1, "engines": {...}}`），预留扩展空间。

5. **测试文件的命名与组织。** 方案中的 `test_registry.py` / `test_scheduler.py` / `test_verifier.py` / `test_orchestrator.py` 与现有 `test_vlm.py` / `test_hierarchy.py` 的命名风格一致，但现有测试还使用 `test_atomic.py` / `test_leakage.py`（模块名直接对应）。建议统一使用模块名对应文件名（`test_registry.py` ✅，`test_scheduler.py` ✅）。

---

## 测试策略评估

### 现有测试模式

现有测试使用 `pytest` + `unittest.mock`。测试类用 `class` 组织（如 `class TestExceptionHierarchy`），测试函数用 `def test_*()`。参数化测试用 `@pytest.mark.parametrize`。

### 方案中测试文件的覆盖评估

| 文件 | 应覆盖的场景 | 方案是否充分 |
|------|------------|------------|
| `test_registry.py` | 空环境探测、全引擎可用、部分不可用、基准数据加载/保存、跨进程恢复 | ⚠️ 方案仅列出文件名，未指定场景 |
| `test_scheduler.py` | 空候选列表、冷启动排序（默认值）、带历史数据排序、预算耗尽过滤、Tier 约束 | ⚠️ 同上 |
| `test_verifier.py` | 所有 GlyphStatus 输出、空知识库、混淆集匹配、多种检测器同时触发、C1/D4 集成 | ⚠️ 同上 |
| `test_orchestrator.py` | 全引擎成功、部分引擎失败降级、全部失败到 HumanGate、单引擎崩溃恢复、预算超时 | ⚠️ 同上 |

**评估：** 方案提到了测试文件名但**没有列出具体的测试场景**。建议补充以下最低限度的测试清单（至少每个文件 5-8 个测试函数）：

```
test_registry.py:
  - test_probe_empty_env_returns_no_engines
  - test_probe_detects_available_engines
  - test_record_updates_stats_correctly
  - test_save_and_load_benchmark
  - test_benchmark_survives_process_restart

test_scheduler.py:
  - test_select_empty_registry_returns_empty
  - test_cold_start_sorts_by_default_score
  - test_warm_start_sorts_by_glyph_pass_rate
  - test_excludes_unavailable_engines
  - test_excludes_budget_exhausted_engines
  - test_tier_constraint_filters_correctly

test_verifier.py:
  - test_empty_knowledge_base_returns_unknown
  - test_herb_match_returns_pass
  - test_char_count_spike_returns_uncertain
  - test_unknown_glyph_returns_unknown
  - test_all_detectors_disabled_returns_unknown

test_orchestrator.py:
  - test_all_engines_succeed_returns_full_result
  - test_single_tier_gives_up_after_all_fail
  - test_all_tiers_fail_triggers_human_gate
  - test_engine_crash_continues_to_next_candidate
  - test_empty_registry_returns_empty_result_with_warning
  - test_mock_flag_bypasses_scheduler
```

---

## 总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⚠️ 良（有阻塞） | 四层架构清晰，但 E4 伪代码与数据模型脱节 |
| 代码风格一致性 | ✅ 良 | dataclass 使用、包组织、模块划分与现有项目一致 |
| 边界情况覆盖 | ⚠️ 中 | 空引擎/全失败已隐式处理，但单引擎崩溃未覆盖 |
| 测试策略 | ⚠️ 中 | 文件命名正确但缺少具体测试场景枚举 |
| 迁移平滑度 | ⚠️ 中 | Phase 1 策略可行，但 Tier 1 独立化是未解决的架构依赖 |
| 可维护性 | ✅ 良 | 模块拆分提升长期可维护性，需注意 Verifier 膨胀风险 |
| 实施依赖顺序 | ✅ 良 | E1→E2/E3→E4 基本合理，建议 Phase 分拆降低风险 |

**总体裁决：需要修改后重新审查（CHANGES REQUESTED）**

请在下一版中解决 B1-B6 阻塞项，并至少为测试文件补充部分测试场景枚举。重点关注 E4 伪代码与 `EngineRegistration` 数据模型之间的脱节（B2）——这是方案中最大的设计缺口，不解决将无法编码。
