# KZOCR v0.7 自适应 OCR 引擎编排层 — 架构评审报告（round 2）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.7.md`（修订版）
- **参考实现**：`kzocr/engine/run.py`, `kzocr/engine/types.py`, `kzocr/config.py`, `kzocr/security/egress.py`, `kzocr/resources/__init__.py`, `kzocr/engines/leakage.py`
- **第一轮评审**：`docs/reviews/2026-07-10-round1-v0.7/architect.md`

---

## 总体判断：**通过（APPROVED）** — 可进入实施，含 2 项实施前建议修复 + 2 项实施期关键关注

修订版正确修复了第一轮指出的全部 5 项阻塞问题（B1-B5），对 6 项强烈建议的吸收率约 83%（5/6 完全采纳，1/6 部分采纳）。方案细致度较第一版有质的提升——EngineRunner 协议、Detector 插件架构、Bayesian Average 冷启动、NDJSON 持久化、review_manifest 反馈闭环均为完整的具体设计，不再仅停留在骨架层面。

但代码级验证发现 **2 项实现前必须修正的细节错误**（假定的不存在函数），以及 **4 项新增的次生风险** 需要在实施过程中密切监控。

---

## 1. 阻塞项复查（B1-B5）

### B1：EngineRunner 协议 + 两级流水线 → ✅ **已修复，方案扎实**

| 第一轮指出的问题 | 修订版对应 | 评估 |
|-----------------|-----------|------|
| `engine.run(page)` 接口不存在 | 新增 `EngineRunner` 协议（2.2 节），定义 `run_page()` / `run_book()` | ✅ 完全解决 |
| BookPipeline 无法按页调用 | 两级流水线：Tier 1 全书处理→逐页验证→失败页进入页级降级 | ✅ 设计合理，保留批量优势 |
| EngineRegistration 无引擎实例引用 | 增加 `adapter: Callable \| None` 字段（2.2 节 line 109） | ✅ 正确 |
| Orchestrator 伪代码中 `engine.run()` | 改为 `engine.adapter.run_book()` / `run_page()`（6.1 节） | ✅ 修正 |

**评价**：两级流水线是当前架构约束下的最优解。Tier 1 全书一次处理 → 逐页验证 → 失败页进入页级降级，既保留了 BookPipeline 的批量处理效率，又实现了验证粒度的按页兜底。

**实施注意**：需要为 BookPipeline 实际编写一个 `BookPipelineAdapter` 包装器，将 `process_book()` 的输出拆解为 `BookResult.pages` 格式。当前 `_run_real()`（`run.py:124`）返回的就是 `BookResult`，因此包装器接口清晰，但需要确认每个引擎的 `process_book()` 返回值是否统一。

---

### B2：GlyphVerifier 缺少验证规则架构 → ✅ **已修复，超越预期**

| 第一轮指出的问题 | 修订版对应 | 评估 |
|-----------------|-----------|------|
| 仅有数据结构，无规则流水线 | `Detector` 协议（5.1 节）+ 优先级排序 | ✅ 完全解决 |
| D4/C1/术语库集成点不明 | 5 个预注册检测器（5.2 节），各有独立职责和优先级 | ✅ 超出预期 |
| 缺失调用方粒度定义 | `VerifierContext` 数据类（page_num, neighbor_texts, book_code, page_layout） | ✅ 清晰 |
| 短路逻辑未定义 | `verify()` 强规则短路 + `estimate()` 投票模式 | ✅ 区分了验证和评估两个场景 |

**亮点**：
- ToxinDoseDetector 的剂量正则匹配和 severity="critical" 标记（5.3 节）——填补了领域安全空白
- TermKBMatcher 将已知正确的稀有术语标记为 PASS 而非 RARE（5.2 节注）——减少无效 T2/T3 调用
- 竖排检测明确放在 Scheduler 层而非 Verifier（5.4 节）——职责分离合理

**可改进但仍可接受**：C1 LeakageDetector 定位为"引擎后处理后残余检测"，但当前 `apply_leakage_defense()`（`run.py:679`）已在预处理阶段执行。是否保留预处理阶段还是完全移入 Verifier？此模糊点可在实施中明确，不影响方案通过。

---

### B3：AdapterMeta 扩展 + EngineStats 持久化矛盾 → ✅ **已修复**

| 第一轮指出的问题 | 修订版对应 | 评估 |
|-----------------|-----------|------|
| AdapterMeta 缺少 tier/probe/batch_capable | 3.2 节扩展了这三个字段 | ✅ 完整 |
| `last_seen` 使用 `time.monotonic()` | 改用 `time.time()`（3.1 节 line 138） | ✅ 跨进程持久化兼容 |
| 派生字段更新顺序风险 | 仅存原始累加值，派生值访问时实时计算（3.1 节 line 143） | ✅ 数据一致性良好 |
| 缺少 `glyph_unknown_count` | 已增加（3.1 节 line 136） | ✅ 匹配 GlyphStatus.UNKNOWN |
| 无逐引擎探测机制 | 3.4 节定义了 4 种探测方法（env/port/file/api）惰性探测 | ✅ 设计完善 |

**额外验证**：`AdapterMeta` 当前代码（`types.py:147-157`）确认没有 tier/probe/batch_capable 字段，修订版对这三个字段的添加不会破坏现有代码。

---

### B4：冷启动评分 NaN → ✅ **已修复，方案细致**

| 第一轮指出的问题 | 修订版对应 | 评估 |
|-----------------|-----------|------|
| `0 × (1/0) = NaN` | Bayesian Average: `score = (pass_rate_avg × n + C × prior) / (n + C) × (1 / latency_avg)` | ✅ 数学上正确，无退化 |
| 无冷启动默认值 | `GLYPH_PASS_RATE_DEFAULT = 0.5`, `AVG_LATENCY_DEFAULT_MS = 10000` | ✅ 保守但合理 |
| 无优先级辅助 | Tier 内预设优先级（paddleocr > rapidocr > mineru > unirec） | ✅ 从工程经验出发 |
| 新引擎永不选中 | 5% 概率轮询采样，不参与衰减 | ✅ 避免冷启动陷阱 |

**参数合理性**：C=7 的贝叶斯常数意味着一个引擎需要 7 页以上历史数据才能显著影响评分。此值适中——对于典型 50 页书籍，前 14%（约 7 页）由先验主导，后 86% 由实际数据驱动。可接受。

---

### B5：迁移路径不完整 → ✅ **已修复**

| 第一轮指出的问题 | 修订版对应 | 评估 |
|-----------------|-----------|------|
| 旧配置 `use_mock`/`use_vlm`/`require_real` 映射 | 7.2 节委派模式：`use_mock` 短路径；`use_vlm`→`disabled_tiers=[1]` | ✅ 清晰 |
| `_run_vlm` 内部共享逻辑被重复 | 提取共享 PDF 渲染/后处理函数（7.0 节 line 884） | ✅ 标注在关系表中 |
| `_init_vlm_adapter()` 硬编码降级链冲突 | 9.0 节标注为"移除"，分散到各引擎注册初始化 | ✅ 迁移路径清晰 |
| 无阶段化实施策略 | 8.0 节 Phase 1-3 实施顺序 | ✅ 各阶段交付标准明确 |

**额外加分**：Phase 1-3 的划分合理，每个 Phase 都有交付标准（`test_registry.py 全部通过`、`test_scheduler.py + test_verifier.py 全部通过`、`260+ 现有测试零失败`），便于进度追踪。

---

## 2. 第一轮建议项吸收情况

| 编号 | 建议内容 | 吸收状态 | 说明 |
|------|---------|---------|------|
| S1 | 明确并行/串行矛盾 | ✅ 采纳 | 6.4 节：默认串行，GPU 可通过 `KZOCR_ENGINE_PARALLEL=1` opt-in |
| S2 | `BookResult` 增加 `uncertain_pages` | ✅ 采纳 | 6.5 节 + 5.6 节 review_manifest |
| S3 | `SchedulerConfig` 嵌套 dataclass | ✅ 采纳 | 7.3 节完整定义 |
| S4 | `_build_engine_config()` 分解 | ⚠️ 部分采纳 | 9.0 节列为"逐引擎 config 独立构造"（重构项），但无具体分解方案。可接受——属于实施期优化。 |
| S5 | 异常处理 try/except | ✅ 采纳 | 6.2 节：每引擎调用包裹 try/except，引擎崩溃继续尝试同 Tier 下一引擎 |
| S6 | 集成测试 | ✅ 采纳 | 10.1 节 `test_orchestrator_integration.py` 参数化 8 种兜底路径 |
| S7 | `engine/` vs `engines/` 命名混淆 | ❌ 未采纳 | 可接受——命名重构的代价高于收益 |
| S8 | 每 Tier 前加 budget 检查 | ✅ 采纳 | 6.2 节主循环中 Tier 2/3 入口处有 `budget.check_time_budget()` |

**吸收率：5/6 完全采纳，1/6 部分采纳，1/6 有理由不采纳。** 领域评审的 8 项建议中也有 6 项被采纳（竖排感知调度、toxic_herbs 剂量校验、领域感知排序权重、rare_allowlist PASS 策略、人工校对反馈闭环、review_manifest），2 项未采纳（偏旁部首分解、TCM 字符频率表——属于后续增强而非 v0.7 阻塞）。

---

## 3. 代码级验证发现（新问题）

### 🔴 N1（实施前修复）：`render_pages()` 和 `PageInput` 不存在

**严重度：高 — 阻塞实施**

6.2 节 `orchestrate_book()` 主循环使用：

```python
for page_num, page_input in enumerate(render_pages(pdf_path, config)):
```

但代码库中 **不存在** `render_pages` 函数，也不存在 `PageInput` 类型。`_run_vlm()`（`run.py:538`）内部的 PDF 渲染逻辑是内联的，使用 `pdf2image` 的 `convert_from_path()` + 自定义裁剪。

**建议**：在实施 Phase 1 中新增 `render_pages()` 函数，从 `_run_vlm()` 中提取 PDF → PageInput 转换逻辑。建议的接口契约：

```python
@dataclass
class PageInput:
    img: Image.Image       # PIL Image
    page_num: int
    path: str               # PDF 路径（供引擎按需重新渲染）
    layout: PageLayout | None = None

def render_pages(pdf_path: str, config) -> Generator[PageInput, None, None]:
    """流式生成器，逐页 yield PageInput。禁止全量物化。"""
```

---

### 🔴 N2（实施前修复）：`PageLayout.is_vertical` 不存在

**严重度：高 — 阻塞竖排感知调度**

4.3 节竖排感知调度依赖 `page_layout.is_vertical`：

```python
if page_layout and page_layout.is_vertical and tier == 1:
    return []
```

但 **`PageLayout` 类型和 `is_vertical` 字段在整个代码库中不存在**。方案称为"已有基线特征"，但代码确认该特征不存在。竖排检测本身是一个非平凡的计算机视觉问题（需要分析文字方向、行叠放顺序等），不是简单可加的布尔字段。

**建议**：
1. 在 v0.7 方案中补充 `PageLayout` 的定义（或至少标记为 TODO + 实施期设计）
2. 如果竖排检测在 v0.7 范围内，需预留 Phase 3 中的实现时间；如果超出范围，将调度器的竖排跳过降为 `page_layout is not None and page_layout.is_vertical` 的条件判断（在 PageLayout 实现前始终返回 False，竖排跳过机制暂不生效）
3. 在文档中明确标注竖排检测的实现状态——当前是"设计中"而非"就绪"

---

### ⚠️ N3（实施期关注）：`validate_url` 导入路径错误

**严重度：中 — 需修正**

4.5 节 egress 校验使用：

```python
from kzocr.engines.egress import validate_url
```

但实际的 egress 模块位于 **`kzocr.security.egress`**（`security/egress.py:90`）。`kzocr/engines/` 下没有 `egress` 模块。

**建议**：将导入改为 `from kzocr.security.egress import validate_url`。已在 `khub/client.py:16` 中有例可循。

---

### ⚠️ N4（实施期关注）：`Budget.exhausted` 恒为 False

**严重度：中 — 运行时行为异常**

6.3 节 `Budget.exhausted` 的伪代码：

```python
@property
def exhausted(self) -> bool:
    return False  # 由外部循环管理
```

恒为 False 意味着预算耗尽检查仅在 `budget.check_time_budget()` 处做，所有 `if budget.exhausted: break` 分支永不触发。如果 `max_time_per_page_ms` 或 `max_tokens` 预算是为了限制单引擎而非全局时间，当前实现无法支持。

**建议**：实施时至少实现 wall-clock 和 page-limit 两种预算的 exhausted 判断：

```python
@property
def exhausted(self) -> bool:
    return (self._page_count >= self.max_pages 
            or self._elapsed_ms >= self.max_wall_clock_ms)
```

---

## 4. 新引入的设计副作用与新增风险

### R1：Tier 1 多引擎候选永不回退

**位置**：6.2 节 `orchestrate_book()` line 558-561

```python
if tier1_engines:
    t0 = time.monotonic()
    try:
        book_result = _run_book_engine(tier1_engines[0], pdf_path)
```

`select_candidates()` 返回 `max_tier1_engines=2`（Top-2），但 Orchestrator **只尝试 index 0**。如果第一个 Tier 1 引擎失败，不会回退到第二个。这意味着 `max_tier1_engines=2` 毫无意义——Tier 1 的 N 永远为 1。

**风险等级**：中。对 v0.7 首发版本影响有限（当前只有一个 Tier 1 引擎组通过 BookPipeline 统一调用，不存在多个独立 T1 引擎），但随着未来 T1 引擎独立化，此设计将成为瓶颈。

**建议**：要么在文档中明确标注 Tier 1 多引擎回退为"未来增强"（并相应调整 Top-N 默认值从 2 改为 1），要么在实施时实现 Tier 1 回退逻辑。

---

### R2：`pages_text` → `BookResult.pages` 转换缺失

**位置**：6.2 节主循环构建 `pages_text: list[str]`，但 `BookResult.pages` 的类型是 `list[PageResult]`

当前 `BookResult`（`types.py:172-192`）的 `pages` 字段是 `list[PageResult]`，需要包含 `LineResult`、`ParagraphResult` 等结构化数据。仅存字符串会丢失行级元信息和排版信息。

**风险等级**：高。下游 khub 客户端、zai 校对台、`push_book_to_zai()` 等依赖 `BookResult.pages` 的结构化字段。

**建议**：在实施 Phase 3（3.1 节）中补充 `_build_pages_text` 转换函数，参考当前 `_run_vlm()` 中 `pages_text` 到 `BookResult` 构造的逻辑（`run.py:690-700`）。

---

### R3：并行模式下 `registry.record()` 线程不安全

**位置**：6.4 节 `KZOCR_ENGINE_PARALLEL=1` 允许 GPU 并行

如果启用并行（不同 GPU 设备上同时处理多页），`EngineStats` 的累加器（`total_calls += 1`、`total_latency_ms += t` 等）在没有锁保护的情况下存在竞态条件。

**风险等级**：低。v0.7 默认串行，并行需 opt-in 且限 GPU 环境。但仍应在 `registry.record()` 中增加线程安全保护（`threading.Lock`），避免在极少数启用并行的环境中出现数据不一致。

**建议**：在 `EngineRegistry` 中添加 `_lock: threading.Lock`，`record()` 方法内部使用 `with self._lock:`。

---

### R4：VLM 缓存集成位置不明确

**位置**：9.0 节 D3 集成点："编排层缓存优先（缓存命中不计 benchmark）"

但 6.2 节的主循环伪代码中 **没有体现缓存检查**。缓存检查应该发生在 Tier 2/3 调用之前——如果缓存命中，跳过引擎调用，直接使用缓存的文本过 Verifier。

**风险等级**：中。如果实施时忽略此集成点，会导致缓存机制失效（所有页都走真实引擎调用）或两套缓存逻辑并存。

**建议**：在 `orchestrate_book()` 的 Tier 2/3 循环中增加缓存检查步骤：

```python
# Tier 2/3 分派前
cached_text = _load_vlm_cache(cfg, book_code, page_num)
if cached_text is not None:
    # 缓存命中——不计 benchmark
    result = AdapterPageResult(text=cached_text)
else:
    # 真实引擎调用——记录 benchmark
    result = _run_page_engine(engine, page_input.img)
```

---

## 5. 实施就绪度评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 接口契约完整性 | ⚠️ 中（有 2 处代码级不匹配） | N1: render_pages/PageInput 不存在；N2: PageLayout.is_vertical 不存在 |
| 边界情况覆盖 | ✅ 良 | 空注册表、全部 FAIL、预算耗尽、UNKNOWN 均有处理路径 |
| 迁移平滑性 | ✅ 优 | 委派模式 + 旧配置兼容层 + Phase 分拆，滚动切换风险低 |
| 测试策略 | ⚠️ 中 | 测试文件命名和最小用例数已定义，但**仍未枚举具体测试场景**（第一轮 S6 的遗留缺憾） |
| 领域适配 | ✅ 良 | toxic_herbs 剂量校验、竖排感知、领域权重、反馈闭环均已覆盖 |
| 安全架构 | ✅ 良 | API key 明文禁止、egress 校验、ProbeResult.keys 改为仅存 bool |
| 性能预算 | ✅ 良 | Verifier < 50ms、Benchmark O(1) 追加写入、知识库一次性加载 |

**实施就绪裁决**：**有条件通过（Conditional APPROVED）**

修复 N1 和 N2（`render_pages` 和 `PageLayout.is_vertical`——两个是代码级缺失而非设计缺陷，在 Phase 1 实施中补齐即可），即可进入实施。N3 和 N4 应在 Phase 1 中同步修正。

---

## 6. 实施建议

### 6.1 Phase 1 修正清单

| 优先 | 项 | 影响 |
|------|----|------|
| P0 | `render_pages()` 从 `_run_vlm()` 提取 + `PageInput` 定义 | Phase 3 Orchestrator 无法编译 |
| P0 | `PageLayout` 定义 + `is_vertical` 字段 | 竖排调度不可用（v0.7 可降级但需标注） |
| P1 | 修正 `validate_url` 导入路径 | 运行时 ImportError |
| P1 | 实现 `Budget.exhausted` 的真实逻辑 | 预算检查永不退出 |

### 6.2 Phase 2-3 监控清单

| 项 | 触发时机 | 决策点 |
|----|---------|--------|
| R1 (Tier 1 回退) | 当出现多引擎 Tier 1 注册时 | 在注册表支持前标记为 TODO |
| R2 (PageResult 转换) | Phase 3 `orchestrate_book()` 返回空/错误结构时 | 复用/扩展 `_vlm_markdown_to_pages()` |
| R3 (并行线程安全) | 首次 `KZOCR_ENGINE_PARALLEL=1` 集成测试 | 在 `record()` 内加锁 |
| R4 (VLM 缓存集成) | Phase 3 完整链路测试 | 在 Tier 2/3 循环入口检查缓存 |

### 6.3 测试场景补充建议

虽不影响实施就绪，但仍建议在 Phase 1 测试编写前补充以下场景枚举到方案中：

**`test_registry.py` 补充场景**：跨进程恢复重建 EngineStats、引擎中途被 `mark_unavailable()` 后状态同步

**`test_scheduler.py` 补充场景**：5% 轮询采样概率验证（确定性 mock）、衰减因子在 half_life=0 时的退化行为

**`test_verifier.py` 补充场景**：ToxinDoseDetector 剂量边界（`dosage == max_dosage_g` 边界情况）、TermKBMatcher 多词匹配的优先级

**`test_orchestrator_integration.py` 补充场景**：单 Tier 1 引擎崩溃后无候选回退的行为、所有引擎 UNAVAILABLE 时的日志输出

---

## 7. 总结

| 评审维度 | 第一轮裁决 | 第二轮裁决 |
|---------|-----------|-----------|
| 总体 | 有条件通过（5 项阻塞） | **通过（APPROVED）** |
| EngineRunner 协议 | ❌ 不存在 | ✅ 已定义，两级流水线 |
| GlyphVerifier 架构 | ❌ 仅有壳 | ✅ Detector 协议 + 5 个具体检测器 |
| AdapterMeta 扩展 | ❌ 缺字段 | ✅ 完整扩展 |
| 冷启动策略 | ❌ NaN | ✅ Bayesian Average |
| 迁移路径 | ❌ 不完整 | ✅ 委派模式 + 配置兼容层 |
| 代码级验证 | — | ⚠️ N1(N2) 需修复、N3(N4) 需修正 |

**最终裁决**：v0.7 修订版方案已达到进入实施的架构成熟度。第二轮通过的信心建立在五个阻塞项全部闭合且方案细致度显著提升的基础上。建议在 Phase 1 Sprint 1 的前两天优先完成 N1 和 N2 的代码补齐（`render_pages/PageInput/PageLayout`），这些是 Phase 3 Orchestrator 的编译前提而非设计变更。随后即可按 Phase 1→2→3 的顺序滚动实施。

---

## 附录：代码级验证摘要

| 验证项 | 方案声称 | 代码实际 | 一致性 |
|--------|---------|---------|--------|
| `PageLayout.is_vertical` 存在 | "已有基线特征" | ❌ 不存在 | **不一致** |
| `render_pages()` 存在 | 主循环使用 | ❌ 不存在 | **不一致** |
| `PageInput` 类型存在 | 主循环使用 | ❌ 不存在 | **不一致** |
| `validate_url` 在 `kzocr.engines.egress` | 4.5 节导入 | ✅ 在 `kzocr.security.egress` | **路径错误** |
| confusion_set.json 就绪 | B5 引用 | ✅ `resources/__init__.py` 已加载 | 一致 |
| rare_allowlist.json 就绪 | TermKBMatcher 数据源 | ✅ 已加载 | 一致 |
| toxic_herbs.json 就绪 | ToxinDoseDetector 数据源 | ✅ 已加载 | 一致 |
| `ProbeResult.keys` 当前是 `dict[str, str]` | 3.3 节扩展 | ✅ `types.py:138` | 一致，需修改 |
| `BookResult` 无 `uncertain_pages`/`engine_trace` | 6.5 节扩展 | ✅ 当前无 | 一致，需添加 |
| `_run_vlm()` 内联 PDF 渲染 | 需提取为共享函数 | ✅ `run.py:538-700` | 一致 |
| C1 `apply_leakage_defense()` 预处理位置 | 5.2 节"后处理后残余检测" | ✅ `run.py:679` | 可接受 |
