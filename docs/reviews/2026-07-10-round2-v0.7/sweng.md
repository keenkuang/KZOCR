# 软件工程评审 — Round 2 (v0.7 自适应 OCR 引擎编排层，修订版)

| 字段 | 值 |
|---|---|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.7.md` (修订版) |
| 审查角色 | 软件工程 |
| 审查人 | code-reviewer-8 |
| 日期 | 2026-07-10 |
| 代码基线 | `kzocr/engine/types.py`, `kzocr/engine/run.py`, `kzocr/config.py`, `kzocr/engines/errors.py` |

---

## 总体判断：**条件通过 (CONDITIONALLY APPROVED)** — 6 项阻塞全部修复，但存在 3 项中等风险的新问题和 2 项数据依赖未解决，需在 Phase 1 实施前确认或补充。

修订版方案在架构完整性上有显著提升。EngineRunner Protocol 和两级流水线模型是核心设计改进，填补了 v0.7 初版最大的缺口。建议在当前方案基础上推进 Phase 1 实施，同时解决以下发现的中等风险问题。

---

## 第一轮 6 项阻塞复查

### B1: 冷启动 NaN（E2—CRITICAL）

**状态：✅ 已修复**

| 指标 | 初版 | 修订版 |
|------|------|--------|
| 评分退化 | `0 × (1/0) = NaN` | 贝叶斯平均 `(n × rate + C × prior) / (n + C)` |
| 默认 pass_rate | 未定义 | `GLYPH_PASS_RATE_DEFAULT = 0.5` |
| 默认 latency | 未定义 | `AVG_LATENCY_DEFAULT_MS = 10000` |
| 辅助策略 | 无 | 5% 概率轮询调度 + 预设优先级排序 |

**遗留小问题：** `prior = 0.7` 与 `GLYPH_PASS_RATE_DEFAULT = 0.5` 不一致。贝叶斯平均首次退化为 `prior × (1 / AVG_LATENCY_DEFAULT_MS)`，所以首评分数取决于 prior=0.7 而非默认值 0.5。这两个值应该统一（建议全部用 0.5，更保守），或明确文档说明为什么先验与默认值不同。但这不是阻塞级问题。

---

### B2: `engine.run(page)` 不存在（E4—CRITICAL）

**状态：✅ 已修复 — 这是修订版最大的设计改进**

引入了 `EngineRunner(Protocol)`（`kzocr/engine/types.py`），包含：
- `run_page(page: PageInput) -> AdapterPageResult` — 页级执行
- `run_book(pdf_path: str) -> BookResult` — 书级执行

同时新增 `EngineRegistration.adapter: Callable | None = None` 字段持有引擎实例引用。

**两级流水线模型（关键架构决策）：**
```
Tier 1 (书级): BookPipeline → BookResult → 逐页 GlyphVerifier → 失败页降级
Tier 2/3 (页级): 逐页 `run_page()` 补充 OCR
```

这个模型替代了初版的「逐页循环调所有引擎」的单级循环，解决了书级引擎（BookPipeline）被碎片化调用的架构矛盾。

---

### B3: 单引擎异常处理缺失（E4—CRITICAL）

**状态：✅ 已修复**

修订版伪代码（§6.2）中，Tier 1/2/3 的引擎调用均包裹了 try/except：
- Tier 1: `try: ... except Exception as exc: book_result = None`
- Tier 2/3: `try: ... except Exception as exc: continue` + `registry.record(engine, success=False, error=str(exc))`

**遗留小问题：** Tier 1 的 except 分支没有调用 `registry.record()`，但 Tier 2/3 有。建议 Tier 1 也加上 `registry.record()` 调用，确保 stat 完整性（`registry.record(tier1_engines[0], success=False, error=str(exc))`）。

---

### B4: Tier 1 引擎不可独立调用 / BookPipeline 假设（E4—CRITICAL）

**状态：✅ 已修复（条件性）**

两级流水线模型（Tier 1 书级 → Tier 2/3 页级）正确地将 BookPipeline 作为书级黑盒处理，避免了逐页调用的矛盾。Tier 1 结果逐页进入 GlyphVerifier。

**条件性说明：** Tier 1 伪代码只使用 `tier1_engines[0]`（第一个候选），不尝试同 Tier 的其它引擎。这在当前 BookPipeline 架构下合理（BookPipeline 内部一次运行全部引擎），但方案应明确说明这个设计意图。建议在 §6.2 的注释或文档中标注：Tier 1 书级引擎当前设计为"首选引擎驱动 BookPipeline"，因为 BookPipeline 内部已并行运行多个引擎。

---

### B5: 外部资源就绪状态不明（E3—HIGH）

**状态：⚠️ 部分修复 — 架构方法正确，但数据依赖未解决**

**修复内容：**
- Detector 插件架构（每个检测器独立类，enable/disable 控制）
- GlyphVerifier 初始化时一次性加载知识库
- 检测器丢失时明确降级为"no_detector_matched" → UNKNOWN

**未解决的数据依赖（非阻塞但必须跟踪）：**

| 依赖资源 | 方案中引用位置 | 实际存在？ | 风险 |
|---------|-------------|-----------|------|
| `resources/confusion_set.json` | ConfusionSetDetector 数据源 | ❌ 不存在 | 中等 — Phase 2 实施前需创建 |
| `resources/toxic_herbs.json` | ToxinDoseDetector 数据源 | ❌ 不存在 | 中等 — 需领域专家提供 |
| `resources/rare_allowlist.json` | TermKBMatcher 数据源 | ❌ 不存在 | 低 — 空列表也可运行 |

建议在 Phase 1 末尾新增一项"创建空资源桩文件"任务，确保 Phase 2 启动时这些文件的存在性。

---

### B6: 引擎健康时效性（E2—HIGH）

**状态：✅ 已修复**

引入指数衰减函数：
```python
def decay(last_seen, half_life_days=7.0):
    elapsed_days = (time.time() - last_seen) / 86400
    return 0.5 ** (elapsed_days / half_life_days)
```

有效评分 = 贝叶斯评分 × 衰减因子。轮询采样数据不参与衰减，避免冷启动陷阱。半衰期 7 天是合理的工程默认值。

---

## 本轮新发现的中等风险问题

### N1: `egress.py` 不存在，但方案多处引用 `validate_url()`

**严重程度：中等**

方案 §4.5 和 §6.2 中引用了 `kzocr.engines.egress.validate_url()` 作为 Tier 2 云引擎调用前的 B3 安全校验。但该文件**当前不存在**。

方案 E5 的变更清单没有列出创建 `egress.py`。如果 Phase 3 实现编排循环时才发现 `validate_url()` 未实现，会造成阻塞。

**建议：** 在 E5 中明确增加 `kzocr/engines/egress.py`（或 `kzocr/scheduler/egress.py`）创建任务，实现 `validate_url()` 函数（对 egress allowlist 的 URL 匹配检查）。建议放在 Phase 1 中完成，至少在 Phase 1 提供一个空实现（`def validate_url(url): pass`）。

---

### N2: `ProbeResult.keys` 类型变更会破坏现有代码

**严重程度：中等**

方案 §3.3 声明 `ProbeResult.keys` 由 `dict[str, str]` 改为 `dict[str, bool]`。当前 `types.py:138` 定义为 `dict[str, str]`。

这个变更是**二进制不兼容的**。任何当前读取 `.keys` 字典值的代码（如 `keys.get("sensenova")` 期望返回 API key 字符串）都会在运行时异常或行为改变。

**建议：**
1. grep 搜索 `ProbeResult.keys` 的所有调用点，确认无代码依赖其字符串值
2. 或在方案中标注此变更为 breaking change，并列出所有受影响文件的迁移步骤
3. 如果仅仅为了安全（不存明文），更简单的方案是新增 `ProbeResult.key_exists: dict[str, bool]` 字段，保留 `keys` 为向后兼容——但方案已声明 `keys` 改为 `dict[str, bool]`，测试可以同步改

---

### N3: `_compute_config_hash()` 仍包含 API key，方案声称已移除

**严重程度：低—中**

方案 §3.3 声称"`_compute_config_hash()` 移除 API key 作为 hash 输入"。但当前 `run.py:367` 的实现仍包含 `cfg.sensenova_api_key` 作为 hash 输入的一部分。

这本身不是 bug（API key 变化确实应使 hash 失效），但方案声明的变更与当前实现不一致。**关键是确认：方案是要移除它，还是保留它？**

**建议：** 方案应明确 `_compute_config_hash()` 是否应包含 API key：
- **移除** API key 的理由：API key 轮换不应使 VLM 缓存失效（缓存内容与认证无关）
- **保留** API key 的理由：不同 API key 可能对应不同模型/配额，缓存应失效

两者皆可，但不能"声称已移除但实际包含"。建议统一为"保留 API key"（在当前实现基础上不修改），方案文案应与实现对齐。

---

### N4: Tier 1 的 BookPipeline 集成存在隐式假设

**严重程度：中等**

方案 §6.2 中 Tier 1 的执行路径：
```python
book_result = _run_book_engine(tier1_engines[0], pdf_path)
```

这里的 `_run_book_engine()` 假设 `engine.adapter.run_book(pdf_path)` 能够"只运行选定引擎的 BookPipeline"。但当前 `_run_real()` 的 `BookPipeline.process_book()` 是**一次运行所有已配置引擎**（paddleocr、rapidocr、mineru、unirec 等同时跑），无法单独运行一个引擎。

如果 Orchestrator 期望 Tier 1 只跑选中的引擎，需要：
1. 修改 BookPipeline 的配置以只启用选定引擎（通过 `engine_configs` 传递），或
2. 接受 Tier 1 实际运行全部引擎，仅取选中引擎的输出用于 GlyphVerifier 验证

方案应明确其意图。建议用选项 2（Tier 1 实际运行全部，但 GlyphVerifier 验证的是选中引擎的输出），这与当前 BookPipeline 的架构最小冲突。

---

### N5: 冷启动的 prior vs default 不一致

**严重程度：低**

方案中同时定义了：
- `GLYPH_PASS_RATE_DEFAULT = 0.5`（EngineStats 首次默认值）
- `prior = 0.7`（贝叶斯公式中的全局先验）

贝叶斯平均首次退化公式为 `prior × (1 / AVG_LATENCY_DEFAULT_MS)`，所以实际生效的是 prior=0.7，而非默认值 0.5。这两个值的不同缺乏文档解释。建议统一为 0.5 或 0.7，并在注释中说明选择理由。

---

## 建议项复查（Round 1 S1-S15）

| 编号 | 建议内容 | 修复状态 | 备注 |
|------|---------|---------|------|
| S1 | EngineStats 只存累加值 | ✅ 已采纳 | §3.1 明确"派生值在访问时计算" |
| S2 | 增加 `glyph_unknown_count` | ✅ 已采纳 | §3.1 EngineStats 加入 |
| S3 | `last_seen` 用 `time.time()` | ✅ 已采纳 | §3.1 明确使用挂钟时间 |
| S4 | benchmark 持久化格式 | ✅ 已采纳 | NDJSON + 原子写入 |
| S5 | 调度顺序明确 | ✅ 已采纳 | §4.1 六步流程清晰 |
| S6 | Top-N 可配置 | ✅ 已采纳 | Config 字段已定义 |
| S7 | 检测器优先级表 | ✅ 已采纳 | §5.2 完整定义 |
| S8 | 性能预算 | ✅ 已采纳 | < 50ms + 初始化加载 |
| S9 | `details` 结构化 | ✅ 已采纳 | `key=val;key=val` 格式 |
| S10 | 并行/串行矛盾 | ✅ 已采纳 | 默认串行，opt-in 并行 |
| S11 | Tier 入口预算检查 | ✅ 已采纳 | 伪代码每 Tier 入口有检查 |
| S12 | pages_text → PageResult 转换 | ✅ 已采纳 | 标注了转换步骤（实现细节待补充） |
| S13 | 异常继承层次 | ✅ 已采纳 | `SchedulerError(OcrError)` + `AllEnginesFailedError(RuntimeError)` |
| S14 | Config snake_case | ✅ 已采纳 | `max_tier1_engines`, `total_timeout_s` 等 |
| S15 | benchmark 子命令输入输出 | ✅ 已采纳 | status/history/run/reset |

**全部 15 项建议均已采纳。** 方案在细节完善度上显著优于初版。

---

## 实施顺序风险评估

### 缺少的依赖项

以下依赖项在方案中引用但未在 Phase 1 任务列表中体现：

| 缺失项 | 应该在哪一步创建 | 如果缺失的后果 |
|--------|----------------|--------------|
| `kzocr/engines/egress.py` (validate_url) | Phase 1 末尾或 Phase 2 开头 | Phase 3 orchestrate_book() 中 `validate_url()` 调用失败 |
| `resources/toxic_herbs.json` (空桩) | Phase 2 前 | ToxinDoseDetector 初始化异常 |
| `resources/confusion_set.json` (空桩) | Phase 2 前 | ConfusionSetDetector 初始化异常 |
| `kzocr/engine/types.py` 的 EngineRegistration 等新类型 | Phase 1.2 | Phase 2 调度器无法编译 |

### 建议

在 Phase 1 任务 1.5（benchmark NDJSON 持久化）之后、1.6 之前插入一个"资源文件就绪"任务：
```
1.5b: 创建缺失的资源桩文件（toxic_herbs.json、confusion_set.json、rare_allowlist.json 骨架）
1.5c: 创建 egress.py 骨架（validate_url 空实现 + allowlist 初始化）
```

---

## 边界情况补充检查

| 场景 | 修订版处理 | 评估 |
|------|----------|------|
| Tier 1 引擎失败 → 降级到 Tier 2 | `book_result = None` → tier1_pages 全部为 None → 进入 Tier 2 | ✅ 正确 |
| Tier 1 局部失败（书级成功，部分页验证失败） | 失败页走 Tier 2/3 | ✅ 核心功能 |
| Tier 1 全部引擎第一候选失败，不尝试第二候选 | `tier1_engines[0]` 失败 → 直接回调页级降级 | ⚠️ N4：需明确设计意图 |
| D3 VLM 缓存命中 | "缓存优先于调度器，不计 benchmark" | ✅ 正确 |
| `allow_cloud_vision=False` + 只有云引擎状态可用 | Scheduler 过滤掉 `requires_network=True` 的引擎 → 返回空 | ⚠️ 应记录 WARNING 日志 |
| egress allowlist 为空（白名单无条目） | `validate_url()` 应拒绝所有 URL | ⚠️ 依赖 N1（egress.py 实现） |
| benchmark 目录磁盘满 | NDJSON 写入失败 → registry.persist_benchmarks() 异常 | ⚠️ 未显式处理，建议增加 IOError 捕获 |
| 引擎配置引用错误的环境变量名 | `engine.config["api_key_env"]` 指向不存在的变量 → 运行时错误 | ⚠️ E2 select_candidates 中无此校验 |

---

## 迁移风险补充评估

### `run_engine()` 委派模式

当前方案 §7.2 的委派模式策略是合理的。关键在于：
- `use_mock=1` → 直接 build_mock_book（保持短路）
- `use_vlm=1` → 映射为 `disabled_tiers=[1]`（等价于不使用 Tier 1 书级引擎）
- 默认 → 走 `orchestrate_book()`

**潜在问题：** `use_vlm=1` 映射为 `disabled_tiers=[1]` 假设 VLM 引擎在 Tier 2/3。但当前 VLM 路径（`_run_vlm()`）使用了 PaddleOCR-VL-1.6（Tier 3）和 SenseNova（Tier 2），且 VLM 路径包含页级缓存、跨页合并等逻辑。Orchestrator 需要确保这些功能在 `disabled_tiers=[1]` 模式下也包含。建议在 Phase 2 的集成测试中覆盖 `use_vlm=1` 场景。

### `ProbeResult.keys` 的 breaking change

见 N2。需要 grep 确认影响范围。

---

## 测试策略评估

### 测试文件的充分性

| 测试文件 | 用例数 | 评估 |
|---------|-------|------|
| `test_registry.py` | ≥ 8 | ✅ 充分 |
| `test_scheduler.py` | ≥ 8 | ✅ 充分 |
| `test_verifier.py` | ≥ 10 | ✅ 充分 |
| `test_orchestrator.py` | ≥ 6 | ✅ 充分 |
| `test_orchestrator_integration.py` | ≥ 8 (8 条路径) | ✅ 充分，参数化覆盖好 |
| `test_regression.py` | ≥ 5 | ✅ 关键回归保护 |

### 共享 fixture

`conftest.py` 定义了 3 个 fixture（`mock_all_engines_available`、`mock_only_tier1_engines`、`sample_engine_stats`），可支持大部分测试。建议增加：
- `mock_empty_registry` — 零可用引擎，测试空场景
- `mock_cold_start_stats` — 所有 `total_calls=0` 的 EngineStats，测试冷启动排序

### 遗漏的测试场景

1. **benchmark 持久化 IO 异常：** `test_registry.py` 应包含 `test_benchmark_save_handles_disk_full` 或类似 IO 错误处理测试
2. **`ProbeResult.keys` 类型变更兼容性：** `test_regression.py` 应包含 `test_probe_result_keys_backward_compat` 确认旧调用方不受影响
3. **多 Tier 1 候选的 fallback：** `test_orchestrator_integration.py` 应验证 `tier1_engines[0]` 失败后是否正确降级

---

## 最终裁决

| 维度 | 评分 | 说明 |
|------|------|------|
| B1 冷启动 | ✅ 已修复 | 贝叶斯平滑 + 轮询，prior 与默认值有微小不一致 (N5) |
| B2 engine.run | ✅ 已修复 | EngineRunner Protocol + 两级流水线，核心改进 |
| B3 异常处理 | ✅ 已修复 | try/except 包裹，Tier 1 record 遗留 (N1 内) |
| B4 BookPipeline | ✅ 条件性修复 | 两级流水线，单书级候选需文档化 (N4) |
| B5 资源依赖 | ⚠️ 部分 | 架构正确但 3 个数据文件不存在（需 Phase 1 创建桩文件） |
| B6 时效性 | ✅ 已修复 | 半衰期 7 天的指数衰减 |
| 新问题 N1-N5 | — | 3 个中等 + 2 个低风险 |

**裁决：条件通过 (CONDITIONALLY APPROVED)**

条件清单（Phase 1 实施前必须解决）：
1. 确认 `ProbeResult.keys` 的 breaking change 影响范围（N2）
2. Phase 1 增加 egress.py 骨架和资源桩文件创建任务（N1 + B5残留）
3. 明确 Tier 1 单候选设计意图，并在伪代码/文档中注释（N4）

建议项（实施过程中关注）：
4. Tier 1 异常分支增加 `registry.record()`（N1子项）
5. `prior` 与 `GLYPH_PASS_RATE_DEFAULT` 的一致性（N5）
6. `_compute_config_hash()` 方案文案与实现对齐（N3）

推荐立即进入 Phase 1 实施（数据模型 + 注册中心 + 资源桩文件），同时在 Phase 1 期间解决前述条件。
