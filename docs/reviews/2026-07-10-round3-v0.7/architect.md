# KZOCR v0.7 自适应 OCR 引擎编排层 — 架构评审报告（round 3 — 详细设计）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.7-DETAILED.md`（1813 行，12 节）
- **参考实现**：`kzocr/engine/run.py`、`kzocr/engine/types.py`、`kzocr/config.py`、`kzocr/security/egress.py`、`kzocr/engines/leakage.py`、`kzocr/engines/hierarchy.py`
- **前两轮评审**：`docs/reviews/2026-07-10-round1-v0.7/architect.md`、`docs/reviews/2026-07-10-round2-v0.7/architect.md`

---

## 总体判断：**有条件通过（Conditional APPROVED）** — 详细设计质量显著提升，仍含 3 项实施前需修复的问题 + 7 项实施期关注

详细设计版本相比第二轮规划方案有质的飞跃：所有数据类均已给出完整 Python 定义、编排主循环提供了可在代码评审中逐行对应执行的伪代码、测试用例已枚举到具体场景粒度（15+ 个参数化路径，每文件 10-17 个用例）、迁移策略包含兼容层和阶段化依赖图。

代码级验证确认第二轮指出的 4 项修复（N1-N4）已全部修正，2 项溢出风险（R2/R4）已落实对应实现代码。但详细设计层次浮现出 **3 个新的实施前阻塞性问题**（C2、C4+C5 组合、C6）和 **6 个需在实施中密切关注的次生风险**（C1、C3、C7、C8、C9、C10）。以下按 12 节逐节评审。

---

## 1. 数据类定义（§1）— 评审

### 1.1 AdapterMeta 扩展（§1.1）

**现状**：当前 `types.py:147-157` 的 `AdapterMeta` 确无 `tier`/`batch_capable`/`probe` 三个字段，设计方案添加正确。`AdapterKind` 已存在，`EngineStatus`/`GlyphStatus` 符合约定。

**评价**：✅ 完整妥当。三个扩展字段的职责划分清晰：tier 是引擎固有属性而非运行时状态，probe 是探测方法描述。

### 1.2 EngineRegistration（§1.2）

**评价**：✅ 结构合理。

| 字段 | 评价 |
|------|------|
| `meta: AdapterMeta` | 正确，元信息聚合 |
| `config: dict` | ⚠️ 类型过于宽松。设计意图是"只存环境变量名引用"，但 `dict` 类型不提供编译期防护。建议在 Phase 1 中用 TypedDict 或 `@dataclass` 定义 `EngineConfig` 结构 |
| `adapter: Callable \| None` | ✅ 正确——引用 EngineRunner 实例 |
| `__repr__` 掩码 | ✅ 必要的安全保护 |

### 1.3 EngineStats（§1.3）

**评价**：✅ 数据一致性设计优良（原始累加值 + 实时派生属性），贝叶斯平均公式正确。

**要点**：
- `last_seen` 使用 `time.time()` → ✅ 跨进程持久化正确
- C=7, prior=0.7 → ✅ 参数合理
- `decay()` 使用指数衰减而非半衰期模式 → ✅ 设计中有明确说明

### 1.4 EngineRegistry（§1.4）

**评价**：✅ 线程安全实现，`record()` 方法完整。

**关注**：
- `persist_benchmarks()` 和 `load_benchmarks()` 标记为 `...`（存根）。这是 Phase 1 核心依赖，建议在详细设计中补充完整接口契约（参数/返回值/异常）而不只是在 §8 中补充实现逻辑。
- `record()` 中 `total_pages` 只在 `success=True` 时递增。但 `success` 的判断逻辑未在编排循环伪代码中显式定义——`_record_engine_usage()` 辅助函数的缺失使得 `success=True/False` 的边界条件不透明（见 §7 评审）。

### 1.5 Budget（§1.5）

**评价**：✅ 正确修复了第二轮 N4（`_exhausted` 恒为 False）问题。

**新问题 — ⚠️ 函数式 mismatch**：`Budget` 类提供了 `check_time_budget(elapsed_s)` 方法，但编排主循环（§7.1）不使用此方法——而是直接内联检查 `elapsed > config.total_timeout_s` 后调用 `budget.exhaust()`。`check_time_budget()` 从未被调用。建议：
1. 要么删除未使用的 `check_time_budget()` 方法
2. 要么让编排循环使用 `budget.check_time_budget()` 替代内联检查

当前状态是 **死代码 + 重复逻辑**，应在 Phase 1 中清理。

### 1.6 辅助类型（§1.6）— PageInput / PageLayout

**评价**：✅ 两个类型定义完整，可以支撑编排循环的逐页处理。

**问题 — 🔴 C9（重复定义）**：`PageInput` 和 `PageLayout` 在 §1.6 和 §6.2 **完全重复定义了两次**。虽然在实施中不会导致代码重复（只需定义一次），但评审时已造成混淆——两次定义是否完全一致？确认为一致，但文档中应消除重复，只在一个位置定义并引用。

**关注**：`PageInput.img` 类型标注为 `"np.ndarray"`（字符串前向引用）。`numpy` 是运行时依赖（已确认 `numpy==1.26.4` 可用），但类型标注在 `from __future__ import annotations` 下工作正常。✅

---

## 2. EngineRegistration 冷启动策略（§2）— 评审

### 🔴 C1（矛盾）：轮询采样的 `last_seen` 参与衰减

**位置**：§2.3 vs §1.3

**矛盾描述**：
- §2.3 明确声明："轮询采样数据不参与衰减：轮询调用的 `last_seen` 不更新（或单独计数）"
- 但 `EngineRegistry.record()`（§1.4:228-246）**总是更新** `last_seen = now`
- 编排循环（§7.1）中对 Tier 2/Tier 3 的每次引擎调用都调用 `_record_engine_usage()`（不存在，但应会调用 `registry.record()`）
- 轮询采样也是通过 `select_candidates()` 的分支进入编排循环的，其调用链路并无"不更新 `last_seen`"的特殊路径

**影响**：轮询采样会导致目标引擎的 `last_seen` 更新，使 `decay()` 返回更高值（衰减更少），从而**人为提升**轮询引擎的评分——这是"探索"的预期行为，但违背了设计文档"不参与衰减"的声明。

**建议**：两种修复方案选一：
1. **修改声明**：承认轮询采样也更新 `last_seen`（这实际上有利于探索——新鲜度提升使引擎更有机会被选中）。在文档中修改 §2.3 的描述即可。
2. **实现隔离**：在 `EngineStats` 中增加 `poll_calls` / `poll_last_seen` 独立计数，`decay()` 不感知轮询时间戳。但这会增加复杂度，**不推荐**。

**建议方案 1，即修改声明以匹配行为。这是最小变更。**

---

## 3. API key 安全设计（§3）— 评审

### 3.1-3.3 设计

**评价**：✅ 设计合理。
- `config` 存储环境变量名引用而非明文 → ✅
- `ProbeResult.keys` 改为 `dict[str, bool]` → ✅ （当前 `types.py:138` 为 `dict[str, str]`，需修改）
- `_compute_config_hash()` 移除 API key → ✅

### 3.4 完整性保护

**评价**：✅ 文件权限 700 + 可选 HMAC。HMAC 为可选实现——实施期按需补齐即可。

**建议**：`_compute_config_hash()` 移除 API key 后，仍保留 `sensenova_model` 和 `sensenova_base_url` 作为 hash 输入。这是正确的——换模型应使缓存失效，仅换 API key 不应使缓存失效。确认当前 `run.py:372-381` 的实现与设计一致。

---

## 4. 调度器设计（§4）— 评审

### 4.1 `select_candidates()` 执行顺序

**评价**：✅ 9 步执行顺序合理，每一步都有明确的过滤/排序逻辑。

### 4.2-4.3 权重公式与 `domain_adjust()`

**评价**：✅ 公式正确，加法偏移优于乘法偏移。

**关注**：`PageInfo` 定义为 `@dataclass`（§4.3:568-575），但 `select_candidates()` 签名中 `page_info: PageInfo` 的类型提示只在 §4.1 的签名中出现，而未在 §7 编排主循环中体现调用方式。`_make_page_info(page_num, page_layout)` 是辅助函数——需在 Phase 2 实现中定义。

### 🔴 C3（缺失定义）：`EngineScheduler` 类不存在

**位置**：§7.1:1077

**问题**：编排主循环使用：
```python
scheduler = EngineScheduler(config)  # E2
```
但 `EngineScheduler` 类在 **整个设计文档中从未定义**。`select_candidates()` 是模块级函数（§4.1），不是类方法。这是一个**架构不一致**——编排主循环期望一个类实例，但调度器设计为函数式 API。

**建议**：两种修复选一：
1. **删除 `EngineScheduler` 类**：令 `scheduler` 为模块引用（`from kzocr.scheduler import select_candidates`），直接调用 `select_candidates(...)`。最小变更。
2. **定义 `EngineScheduler` 类**：将 `select_candidates()` 作为其方法，配置在 `__init__` 中注入。更适合长期的配置管理。

**建议方案 2**，因为 `SchedulerConfig` 已经是嵌套 dataclass，自然可以作为 `EngineScheduler.__init__` 的入参。

### 4.4 `_get_max_engines_for_tier`

**评价**：✅ 实现清晰，默认值 Tier 1=2, Tier 2=1, Tier 3=1 合理。

### 4.5 egress 校验

**评价**：✅ 导入路径已修正为 `kzocr.security.egress`（修复第二轮 N3）。`kzocr/security/egress.py:90` 确认 `validate_url()` 存在且签名匹配。

---

## 5. Detector 协议和 GlyphVerifier（§5）— 评审

### 5.1-5.4 设计

**评价**：✅ Detector 插件架构清晰，优先级排序合理（安全→泄漏→异常→混淆→知识库），短路逻辑定义完整。

### 🔴 C8（缺失能力）：CharCountSpikeDetector 缺少邻居页上下文

**位置**：§5.3 CharCountSpikeDetector

**问题**：`CharCountSpikeDetector.check()` 签名是 `(text, context)`，其中 `DetectorContext` 包含 `page_num`、`book_type`、`engine_label`、`resources: dict`，但不包含邻居页的文本信息。

但是字符数尖峰检测**需要**与邻居页比较（超过邻页中位数 × 3）。当前 `DetectorContext.resources` 可以携带历史页数据，但设计未明确其存储结构和更新机制。

**当前代码**：`kzocr/engines/hierarchy.py` 实现了 `check_hierarchy_anomaly()` 函数——在现有 VLM 流程中是全量处理（所有页完成后一次性分析），而非逐页增量式。

**影响**：如果 GlyphVerifier 按 §5.4 设计逐页调用，`CharCountSpikeDetector` 无法工作——它不知道之前页的字符数。

**建议**：
- 在 `DetectorContext.resources` 中明确定义 `char_count_history: list[int]` 字段，由编排循环在每次 verify 调用前填充
- 或修改 `CharCountSpikeDetector` 为**惰性检测器**：收集所有页的字符数，在全书完成后统一分析（但这样不符合逐页 verify 的模型）
- 或在 Phase 2 实施时将 `CharCountSpikeDetector` 降级为**仅运行在全量分析模式**（在全书完成后、persist_benchmarks 之前执行）

**建议方案 1**：将 `DetectorContext.resources` 的契约规范化，让编排循环维护 `char_count_history` 并在每页 verify 时传入。

### 5.5 性能预算

**评价**：✅ `< 50ms / 次 verify()` 的目标合理。`details` 结构化格式为 `key=val;key=val` 也明确了与下游 review_manifest 的集成。

### 5.6 review_manifest

**评价**：✅ 数据结构完整。`ReviewIssue.issue_type` 的 `Literal["glyph", "dosage", "herb", "layout"]` 涵盖了主要问题类型。

---

## 6. EngineRunner 协议（§6）— 评审

**评价**：✅ 协议定义简洁，两级接口（`run_page` / `run_book`）覆盖了书级和页级两种引擎模式。

**引擎适配映射表（§6.3）**：清晰。

**BookPipelineAdapter（§6.4）**：设计正确——`run_book()` 委托 `BookPipeline.process_book()`，`run_page()` 抛出 `NotImplementedError`。

---

## 7. 编排主循环伪代码（§7）— 评审

### 🔴 C6（延迟统计错误）：Tier 1 延迟被均摊到每页

**位置**：§7.1:1100, 1139

**问题**：
```python
t0 = time.monotonic()
tier1_result = _run_book_engine(tier1_candidates[0], pdf_path)
t1_elapsed = int((time.monotonic() - t0) * 1000)
# ... 然后在 1139 行：
page_trace_records.append(EngineCallRecord(
    page=page_num, tier=1,
    engine=...,
    latency_ms=t1_elapsed,  # ← 全书耗时而非单页耗时
    ...
))
```

`t1_elapsed` 是**全书**处理时间（如 120s），但被写入**每页**的 `latency_ms`。对于一个 50 页的书籍，每页的延迟记录将显示 120000ms 而非约 2400ms。这是**延迟统计严重失真**。

**影响**：Benchmark NDJSON 中的 Tier 1 延迟数据不可信，会影响 Tier 1 引擎的贝叶斯评分（`avg_latency_per_page_ms` 被严重高估）。

**建议**：
- 方案 A（推荐）：将全书耗时除以页数，作为每页的均摊延迟：
  ```python
  latency_per_page = t1_elapsed // len(tier1_result.pages) if tier1_result and tier1_result.pages else t1_elapsed
  ```
- 方案 B：Tier 1 不记录 per-page 延迟，只在书级别统计（改动较大，不推荐）

### 7.2 `_build_pages_result()` 

**评价**：✅ 正确解决了第二轮 R2（`pages_text → PageResult[]` 转换缺失）。复用 Tier 1 的结构化数据是最优解。

### 7.3 `_run_single_engine_with_timeout()`

**评价**：✅ 使用 `ThreadPoolExecutor` 实现超时保护是合理的轻量方案。注意：`concurrent.futures` 的 `future.result(timeout)` 在超时时不会终止线程——线程会继续运行（僵尸线程）。对于 Tier 3 本地 LLM，这意味着一个挂死的 LLM 调用会留下一个僵尸线程。这在 v0.7 中可接受（默认串行，数量可控），但需在文档中注明此限制。

### 7.5 B6 双闸

**评价**：✅ 页数闸 + 时间闸 + 单页超时三重保护，边界清晰。

### 7.6 D3 VLM 缓存集成

**评价**：✅ 正确解决了第二轮 R4。缓存命中不计 benchmark 的逻辑清晰。

---

## 8. Benchmark NDJSON 格式（§8）— 评审

### 🔴 C2（实施前修复）：`load_benchmarks()` 不重建 `total_pages`

**位置**：§8.4:1423-1431

**问题**：`load_benchmarks()` 重建 `EngineStats` 时，更新了 `total_calls`、`total_latency_ms`、`glyph_pass_count`/`fail_count`/`unknown_count`、`last_seen`、`last_error`，但**从不递增 `total_pages`**。

结果：从持久化加载后，`EngineStats.total_pages` 始终为 0，使得：
- `avg_latency_per_page_ms = total_latency_ms / total_pages = total_latency_ms / 0` → 返回 `AVG_LATENCY_DEFAULT_MS`（10s）
- 所有引擎的延迟评分退化为冷启动默认值
- **Benchmark 持久化机制形同虚设**

**修复**：在 `load_benchmarks()` 的事件循环中，当 `glyph_status == "PASS"` 时递增 `total_pages`：
```python
if glyph_status == "PASS":
    stats.total_pages += 1
```

这与 `record()` 方法（§1.4:236）的 `success=True → total_pages += 1` 逻辑对应。

### 8.5 容量管理

**评价**：✅ 100MB 自动截断、90 天保留、50000 行限制，三个运维边界均合理。

### 8.6 数据格式演进

**评价**：✅ `dict.get(field, default)` 向后兼容策略。确认可行。

---

## 9. Config 新增字段（§9）— 评审

### 🔴 C4 + C5（组合阻塞）：`disabled_tiers` 字段缺失 + API 签名不匹配

**位置**：§9.1 SchedulerConfig 定义 vs §12.1 迁移代码

**问题 1 — C4：字段缺失**
`SchedulerConfig`（§9.1:1456-1483）定义了 12 个字段，但**不包含 `disabled_tiers`**。然而 §12.1 迁移代码直接使用：
```python
config_overrides = SchedulerConfig(disabled_tiers=[1])  # ← 字段不存在
```

**问题 2 — C5：类型不匹配**
§12.1:
```python
return orchestrate_book(pdf_path, book_code, config, config_overrides)
# config_overrides 类型为 SchedulerConfig
```
但 `orchestrate_book()` 签名（§7.1:1048-1053）的第四个参数是 `overrides: EngineOverrides | None`，不是 `SchedulerConfig`。

**影响**：迁移代码无法通过编译——既传入了不存在的关键字参数，又传错了参数类型。

**建议修复**：
1. 在 `SchedulerConfig` 中增加 `disabled_tiers: list[int] = field(default_factory=list)` 字段
2. 修改 `orchestrate_book()` 使其接受 `SchedulerConfig` 覆盖（或修改迁移代码使用 `EngineOverrides`）
3. 推荐方案：**保持 `orchestrate_book()` 签名不变**，改用 `EngineOverrides` 表达禁用 Tier：
   ```python
   if config.use_vlm:
       overrides = EngineOverrides(tier_order=[2, 3])  # 跳过 Tier 1
       return orchestrate_book(pdf_path, book_code, config, overrides)
   ```

---

## 10. CLI 扩展（§10）— 评审

### ⚠️ C7（设计间隙）：`--tier-order` CLI 参数无实际效果

**位置**：§10.1 vs §7.1 vs §4.1

`--tier-order` 参数在 CLI 层解析为 `EngineOverrides.tier_order`（§10.3:1570），但：
- `select_candidates()`（§4.1）不使用 `tier_order`
- `orchestrate_book()`（§7.1）硬编码 Tier 1 → 2 → 3 顺序

结果：`--tier-order "1,3,2"` 被解析但**永不生效**。所有降级路径仍然是 1 → 2 → 3。

**建议**：要么在 `orchestrate_book()` 中实现 tier_order 驱动的降级顺序，要么在 CLI 文档中标注为"预留，v0.8 实现"。**后者更现实**，因为 v0.7 的核心是调度器而非自定义降级顺序。

### 10.2 benchmark 子命令

**评价**：✅ CLI 输出格式明确。`kzocr benchmark list` 的表格格式涵盖了所有关键统计量。

---

## 11. 测试策略（§11）— 评审

### 总体评价

**评价**：✅ 覆盖全面。5 个新增测试文件，总计 ≥ 51 个测试用例，8 种参数化编排路径。

### 具体建议

| 功能 | 现状 | 建议 |
|------|------|------|
| `render_pages()` 流式生成器 | ❌ 无测试 | Phase 1 应补充 `test_render_pages.py`：验证流式特性（非全量物化）、验证 PageInput 字段完整性、验证多页 PDF 的 yield 顺序 |
| `BookPipelineAdapter` | ❌ 无测试 | Phase 1 应补充测试：`run_book()` 委托验证、`run_page()` 抛出 NotImplementedError |
| Benchmark 100MB 截断 | ❌ 无对应测试 | 建议补充 `test_benchmark_truncation` |
| `--tier-order` 参数无效果 | ❌ 未覆盖 | 测试应验证 CLI 解析正确 + 确认当前不生效（预留标记） |
| 编排循环 `_run_single_engine_with_timeout` 超时 | ✅ test_orchestrator.py:12 | 已覆盖引擎崩溃续行，但需区分"异常崩溃"和"挂死超时"两条路径 |

### 集成测试检查清单（§11.9）

确认：5 个集成场景覆盖合理。"probe→registry 真实流程"标记为"需补充"——建议在 Phase 3 中补充。

---

## 12. 迁移策略（§12）— 评审

### 12.1 `run_engine()` 委派模式

**评价**：✅ 委派模式清晰。Map 逻辑：
| 旧配置 | 映射 | 评价 |
|--------|------|------|
| `use_mock=True` | 短路径返回 mock | ✅ 保留 |
| `use_vlm=True` | `disabled_tiers=[1]` | ⚠️ 见 C4+C5 |
| `require_real=True` | `tier_limit=1` | ⚠️ 同上 |

### 12.2-12.3 配置兼容层

**评价**：✅ 废弃时间线合理。v0.7 兼容 → v0.8 移除（带警告），给用户一个版本的迁移窗口。

### 12.4 实施阶段依赖图

**评价**：✅ Phase 1→2→3 的划分逻辑正确，每个阶段都有对应的测试文件作为交付标准。1813 行文档中有 51+ 测试用例的详细规格，实施团队可以逐用例推进。

---

## 从详细设计到代码实现的差距评估

### 已明确、可直接编码的项（> 80% 完成度）

| 项 | 文件 | 评估 |
|----|------|------|
| `AdapterMeta` 扩展 | `types.py` | 3 个新字段，无行为修改 |
| `EngineRegistration` | `scheduler/registry.py`（新） | 完整定义 |
| `EngineStats` | `scheduler/registry.py`（新） | 完整定义 + 公式 |
| `EngineRegistry` | `scheduler/registry.py`（新） | 核心方法完整，persist/load 需细化 |
| `Budget` | 待定 | 完整定义 |
| `PageInput` / `PageLayout` | `types.py` | 完整定义 |
| `EngineOverrides` | 待定 | 完整定义 |
| `EngineRunner` 协议 | 待定 | 完整定义 |
| `GlyphVerifier` + 5 检测器 | `verifier.py`（新） | 检测器逻辑完整 |
| `ProbeResult.keys` 类型变更 | `types.py` | `str` → `bool` |
| `Config.scheduler` 嵌套 | `config.py` | 字段+映射表完整 |
| CLI 扩展 | `cli.py` | 参数+解析逻辑完整 |
| Benchmark NDJSON save | 待定 | 行级追加逻辑清晰 |
| `_build_pages_result()` | 待定 | 逻辑完整 |

### 未完全明确的决策项（实施中需细化）

| 项 | 当前状态 | 需要的决策 |
|----|---------|-----------|
| `EngineScheduler` 类 vs 函数 | §7 引用类，§4 定义函数 | 确定使用类封装还是函数式 API（见 C3） |
| `_safe_select_candidates()` 包装层 | §7 调用但未定义 | 定义异常处理逻辑（空注册表、pinned 不可用等） |
| `_record_engine_usage()` | §7 调用但未定义 | 确定 `success=True` 的判断标准（仅 PASS/RARE？） |
| `_make_page_info()` | §7 调用但未定义 | 从 `page_num` + `config` 构造 `PageInfo` 的规则 |
| `_init_detectors()` | §7 调用但未定义 | 检测器资源加载策略（一次性 vs 惰性） |
| `_write_trace()` | §7 调用但未定义 | trace 格式（JSON/CSV？）与目录结构 |
| `_run_book_engine()` / `_run_page_engine()` | §7 调用但未定义 | EngineRegistration → EngineRunner 调用路径 |
| `render_pages()` 流式生成器 | §1.6 签名已定义，实现未定 | 从 `_run_vlm()` 提取 PDF 渲染逻辑的具体方法 |
| `persist_benchmarks()` 批量 flush | §1.4 标记 `...` | Flush 触发条件（仅书完成时？） |
| Benchmark 100MB 截断策略 | §8.5 描述但无代码 | 是截断最老 50% 还是滚动删除？工具函数？ |
| `disabled_tiers` 字段 | §12 使用但 §9 缺失 | 最终方案（见 C4+C5 建议） |
| `--tier-order` 生效时机 | §10 解析但 §7 不使用 | v0.7 实现还是预留到 v0.8（见 C7 建议） |

**评估**：约 **80% 的数据类和 API 签名**可直接编码，约 **60% 的编排逻辑**可直接编码（循环结构清晰但有 7 个辅助函数待定义），约 **40% 的持久化和边界处理**仍在存根状态。总体实施就绪度约为 **70%**。

---

## 阻塞项清单

### 🔴 C2 — `load_benchmarks()` 不重建 `total_pages`（§8.4）
**严重度**：**高**。Benchmark 持久化机制失效，所有引擎的延迟评分在进程重启后退化为冷启动默认值。
**修复**：在 §8.4 的事件循环中增加 `if glyph_status == 'PASS': stats.total_pages += 1`。
**影响范围**：Phase 1（registry.py + test_registry.py）。

### 🔴 C4+C5 — `disabled_tiers` 字段缺失 + 迁移代码 API 不匹配（§9.1, §12.1）
**严重度**：**高**。迁移代码无法编译，旧配置 `use_vlm` 和 `require_real` 的委派映射失效。
**修复**：二选一——
- 方案 A：在 `SchedulerConfig` 增加 `disabled_tiers` 字段 + 修改 `orchestrate_book()` 签名
- **方案 B（推荐）**：删除迁移代码中对 `SchedulerConfig(disabled_tiers=...)` 的引用，改用 `EngineOverrides` 表达：
  ```python
  if config.use_vlm:
      overrides = EngineOverrides(tier_order=[2, 3])  # 跳过 Tier 1
      return orchestrate_book(pdf_path, book_code, config, overrides)
  if config.require_real:
      overrides = EngineOverrides(tier_limit=1)  # 只走 Tier 1
      return orchestrate_book(pdf_path, book_code, config, overrides)
  ```
**影响范围**：Phase 3（run.py 委派改造）。

### 🔴 C6 — Tier 1 全书延迟被写入每页记录（§7.1）
**严重度**：**高**。Benchmark 数据失真，Tier 1 引擎的 `avg_latency_per_page_ms` 被严重高估。
**修复**：`t1_elapsed // len(tier1_result.pages)` 均摊到每页。
**影响范围**：Phase 3（orchestrator.py）。

---

## 建议项清单

| 编号 | 建议 | 优先级 | 对应章节 |
|------|------|--------|---------|
| S1 | 修改 §2.3 声明：承认轮询采样更新 `last_seen`，与 §1.4 行为一致 | P1（文档修正） | §2.3 / C1 |
| S2 | 消除 `PageInput` 和 `PageLayout` 的重复定义，只在一个位置定义 | P1（文档修正） | §1.6 / §6.2 / C9 |
| S3 | `EngineScheduler` 类 vs 函数二选一，推荐选择类封装方案 | P1（决策） | §4 / §7 / C3 |
| S4 | 删除 `Budget.check_time_budget()` 或让编排循环使用它 | P1（实施前清理） | §1.5 |
| S5 | 定义 `DetectorContext.resources` 中 `char_count_history` 的契约 | P2（实施期） | §5.3 / C8 |
| S6 | 标注 `--tier-order` 为"v0.8 预留"或在编排循环中实现 | P2（边界标注） | §10.1 / C7 |
| S7 | 补充 `test_render_pages.py` 和 `BookPipelineAdapter` 的测试 | P2（测试补充） | §11 |
| S8 | 明确 `_record_engine_usage()` 中 `success=True` 的定义规则 | P2（实施前决策） | §7 |
| S9 | 补充 7 个辅助函数 (`_safe_select_candidates` 等) 的接口契约到设计文档中 | P2（文档补充） | §7 |
| S10 | 标注 `_run_single_engine_with_timeout` 的僵尸线程行为 | P3（文档注释） | §7.3 |

---

## 总结

| 评审维度 | 第二轮裁决 | 第三轮裁决 |
|---------|-----------|-----------|
| 总体 | 通过（4 项修复） | **有条件通过（3 项阻塞修复 + 10 项建议）** |
| 数据类定义 | ⚠️ 仅骨架 | ✅ 完整定义（含重复定义需清理） |
| 冷启动策略 | ✅ Bayesian Average | ✅ 实现完整，轮询/衰减声明需修正 |
| API key 安全 | ✅ 原则正确 | ✅ 细化到 hash 输入和探测结果类型 |
| 调度器设计 | ✅ 公式合理 | ✅ 执行顺序完整，`EngineScheduler` 类缺失 |
| Detector/GlyphVerifier | ✅ 协议定义 | ✅ 5 个检测器具体实现，邻居上下文缺失 |
| EngineRunner | ✅ 协议定义 | ✅ 两级接口 + 适配映射表 |
| 编排主循环 | ⚠️ 伪代码 | ✅ 完整可执行伪代码（3 个辅助函数待定义 + 1 个延迟 Bug） |
| Benchmark 持久化 | ⚠️ 设计阶段 | ✅ 格式完整，`total_pages` 不重建问题 |
| Config | ✅ SchedulerConfig | ✅ 完整映射表，`disabled_tiers` 缺失 |
| CLI | — | ✅ 参数完整，`--tier-order` 无效 |
| 测试策略 | ⚠️ 场景数不足 | ✅ 51+ 具体用例，8 种参数化路径 |
| 迁移策略 | ✅ 委派模式 | ✅ 兼容层 + 时间线，API 签名不匹配问题 |
| 代码级验证 | ⚠️ 4 项不匹配 | ✅ 9 项验证中 6 项匹配，3 项新发现 |

**文档成熟度**：从第二轮规划方案（约 300 行骨架）到第三轮详细设计（1813 行，含可执行伪代码 + 数据类定义 + 测试用例枚举）的提升是实质性的。12 节中约 8 节已达到"可直接编码"的细度。

**实施前必须修复**：
1. **C2**：`load_benchmarks()` 重建 `total_pages`
2. **C4+C5**：`disabled_tiers` 字段 + 迁移代码 API 匹配
3. **C6**：Tier 1 延迟均摊到页

修复上述 3 项后即可按 Phase 1→2→3 顺序进入实施。建议 Phase 1 Sprint 1 优先处理 C2（`load_benchmarks`）、S1（文档修正）、S2（重复定义消除），Phase 2 中处理 C8（邻居页上下文），Phase 3 处理 C4+C5+C6+C7。

---

## 附录：代码级验证摘要

| 验证项 | 方案声称 | 代码实际 | 一致性 |
|--------|---------|---------|--------|
| `AdapterMeta` 缺 tier/batch_capable/probe | 需扩展 | ✅ `types.py:147-157` 确认缺失 | 一致，需添加 |
| `GlyphStatus` 已存在 | "PASS"/"RARE"/"UNKNOWN"/"FAIL"/"UNCERTAIN" | ✅ `types.py:13` 已定义 | 一致 |
| `ProbeResult.keys` 当前为 `dict[str, str]` | 需改为 `dict[str, bool]` | ✅ `types.py:138` 确认 | 一致，需修改 |
| `BookResult` 无 uncertain_pages/engine_trace | 需扩展 | ✅ `types.py:172-192` 确认 | 一致，需添加 |
| `validate_url()` 在 `kzocr.security.egress` | 导入路径正确 | ✅ `security/egress.py:90` 确认 | 一致 |
| `check_hierarchy_anomaly()` 在 `hierarchy.py` | CharCountSpike 复用逻辑 | ✅ `engines/hierarchy.py` 确认 | 一致 |
| `render_pages()` 存在 | §1.6 签名定义 | ❌ 代码库中不存在 | **需 Phase 1 新建** |
| `_compute_config_hash()` 含 API key | 需移除 API key | ⚠️ `run.py:372-381` 含 `sensenova_api_key` | 需修改 |
| PDF 渲染内联于 `_run_vlm()` | 需提取 | ✅ `run.py:538-700` 确认 | 一致，需提取 |
| `ThreadPoolExecutor` 可用 | 超时保护 | ✅ Python 标准库 | 一致 |
| `numpy` 可用 | `np.ndarray` 类型 | ✅ 1.26.4 | 一致 |
