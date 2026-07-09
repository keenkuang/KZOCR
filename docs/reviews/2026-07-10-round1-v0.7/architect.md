# KZOCR v0.7 自适应 OCR 引擎编排层 — 架构评审报告（round 1）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.7.md`
- **参考实现**：`kzocr/engine/run.py`, `kzocr/engine/types.py`, `kzocr/config.py`, `kzocr/engines/errors.py`, `kzocr/engines/hierarchy.py`, `kzocr/engines/leakage.py`, `kzocr/engines/_common.py`, `kzocr/resources/confusion_set.json`

---

## 总体判断：**有条件通过（Conditional Pass）**

v0.7 方案的方向正确——将硬编码 if-else 引擎选择重构为注册中心+调度器+验证器的编排模型，与当前代码库中长期积累的 EngineRouter 草稿（`docs/plans/ocr-engine-unification.md`）一脉相承。但方案在 **5 处关键细节上过于简略**，若不修正在实施中会引发显著的架构退化风险。以下逐项展开。

---

## 1. E1 — EngineRegistry（注册中心）

### 1.1  `AdapterMeta` 不足以承载注册信息

**位置**：计划 `types.py:152`（现有 `AdapterMeta`），`registry.py` `EngineRegistration` 定义

`EngineRegistration` 引用了 `meta: AdapterMeta`，但当前的 `AdapterMeta`（`types.py:146-157`）：

```python
@dataclass
class AdapterMeta:
    name: str
    label: str
    kind: AdapterKind = "page"       # page / book
    supports_confidence: bool = True
    supports_context: bool = False
    min_vram_gb: float = 0.0
    default_enabled: bool = True
    requires_gpu: bool = False
    requires_network: bool = False
```

**缺失字段**：
- **`tier: int`** — 引擎的 Tier 归属（1/2/3）。目前 Tier 信息仅出现在计划的表格中，没有数据模型承载。`select_candidates()` 按 tier 筛选时需要这个字段。
- **`probe_method: str | Callable`** — 如何探测该引擎可用（例如：`"port:18080"`, `"api_key:sensenova"`, `"gpu:cuda:2GB"`）。目前 `ProbeResult` 是一个全局平坦结构，没有逐引擎探测能力。
- **`batch_capable: bool`** — 引擎是否支持书级输入（BookPipeline 模式）还是仅页级（VLM 模式）。这对 E4 编排主循环至关重要（见 4.2）。

**建议**：在 `AdapterMeta` 中增加 `tier: int = 1` 和 `probe: dict[str, Any] = field(default_factory=dict)`，或将 `tier` 直接放入 `EngineRegistration`（因为 tier 是运行时调度属性而非适配器元属性）。

### 1.2 `EngineStats.last_seen` 使用 `time.monotonic()` 与持久化矛盾

**位置**：计划 `registry.py:78` — `last_seen: float = 0.0  # time.monotonic()`

`time.monotonic()` 的结果在进程重启后失去意义，但计划同时声明了 benchmark 持久化（`KZOCR_BENCHMARK_DIR` 和 `kzocr benchmark` CLI）。这意味着 `last_seen`、`avg_latency_per_page_ms`、`glyph_pass_rate` 等统计信息需要跨进程持久化。

**问题**：重启后 `monotonic() ≈ 0`，导致 `last_seen` 无法判断"多久前出过错"——比如一个 5 分钟前限流的引擎重启后看起来像从未限流过。

**建议**：
1. `last_seen` 使用 `time.time()`（挂钟时间）而非 `time.monotonic()`，使其可持久化、可比较。
2. 明确 `EngineStats` 的生命周期：内存态（当前进程）+ 持久态（`KZOCR_BENCHMARK_DIR` 下的 JSON/CSV）。进程启动时从持久态加载，运行时更新内存态，退出时或定期写回。
3. 如果统计是内存-only（不持久化），则从文档中移除 `KZOCR_BENCHMARK_DIR` 和 `kzocr benchmark` CLI，避免虚假承诺。

### 1.3 `probe_engines()` 缺乏逐引擎探测机制

**位置**：计划 `registry.py` `probe_engines()` 描述

当前 `ProbeResult`（`types.py:131-139`）是一个预先收集的全局探测快照：

```python
@dataclass
class ProbeResult:
    gpu: bool = False
    vram_gb: float = 0.0
    ports: dict[str, bool] = {}
    keys: dict[str, str] = {}
    allow_cloud_vision: bool = False
```

v0.7 需要逐引擎探测（"检查端口、API key、GPU、模型文件"），但 `ProbeResult` 要求所有探测信息预先收集好。

**问题**：对于 10 个候选引擎，`probe_engines()` 要么全部探测（慢），要么依赖 `ProbeResult` 中已有信息（不够精确）。例如：PaddleOCR-VL 需要确认端口 18080 可连，但 `ProbeResult.ports["18080"]` 来自全局扫描；SenseNova 需要 API key 有效性的实时验证。

**建议**：
1. 定义逐引擎探测函数表：`EngineRegistration` 中的 `probe: dict` 描述如何探测，或实现 `EngineProber` 接口。
2. 将 `ProbeResult` 作为缓存/输入源，而非唯一探测手段。`probe_engines()` 先查 `ProbeResult`，再加实时验证（如端口 connect）。
3. 考虑惰性探测：仅在被 `select_candidates()` 选为候选时才实际探测。

---

## 2. E2 — EngineScheduler（调度器）

### 2.1 评分公式缺少冷启动策略

**位置**：计划 `scheduler.py:108` — `glyph_pass_rate × (1/avg_latency)`

新注册的引擎 `glyph_pass_rate = 0` 且 `avg_latency = 0`，导致 `0 × ∞ = NaN`，永远不会被选中。这是典型的冷启动问题。

**建议**：采用贝叶斯平均（Bayesian Average）平滑起步值：

```
score = (avg_pass_rate × n + C × prior) / (n + C) × (1 / avg_latency)
```

其中：
- `n` = 该引擎的历史调用次数（`total_calls`）
- `C` = 常数（建议 5~10），控制先验权重
- `prior` = 所有引擎的平均通过率（全局先验），或硬编码 0.7（保守默认）
- 引擎无历史数据时退化到 `prior × (1/默认延迟)`，确保新引擎有被选中的机会

### 2.2 `select_candidates()` 需要定义 N 的含义与多引擎策略

**位置**：计划 `scheduler.py:114` — `N 可配置，默认 2`

"N=2" 的语义不明确：
- 是 **顺序尝试**（先引擎 A，若失败再引擎 B）？当前 E4 伪代码是顺序模式。
- 还是 **并行执行**（A 和 B 同时跑，取先完成的）？计划"目标"列声明了"多引擎并行"。
- 或者 **投票共识**（A 和 B 都跑，结果比对）？

**问题**：并行和顺序是完全不同的编排模型。顺序模式中 `N > 1` 意味着额外延迟；并行模式需要连接池、超时、并发控制。

**建议**：
1. 在 `select_candidates()` 的返回中附带策略标志：`list[tuple[EngineRegistration, Literal["parallel", "sequential", "consensus"]]]`。
2. 或至少在文档中明确 v0.7 的范围是顺序降级，并行/共识作为后续版本。
3. 最大并发数建议从配置移至 `EngineRegistration` 字段（`max_concurrency: int = 1`），因为不同引擎的并发能力不同（云端有速率限制，本地无）。

### 2.3 预算检查与现有 `KZOCR_TOTAL_TIMEOUT` 的集成缺位

**位置**：计划 `scheduler.py:111`

现有 `_run_vlm()`（`run.py:585`）在逐页循环中检查 `elapsed > total_timeout`。新方案中 `Budget` 对象应该：
- 在 **每页尝试之前** 检查剩余时间（避免启动一个慢引擎后发现已超时）
- 在 **每引擎尝试之间** 检查（Tier 1 用掉 90% 时间后，Tier 2/Tier 3 应跳过）
- 记录各引擎的时间消耗，用于后续调度决策

**建议**：明确 `Budget.check()` 的调用时机：E4 伪代码中每个 `verdict.status in ("FAIL", "UNKNOWN")` 分支前应加 `budget.check()`。

---

## 3. E3 — GlyphVerifier（字形验证器）

### 3.1 设计过于简略 — 缺少验证规则引擎

**位置**：计划 `verifier.py`

方案仅给出了 `GlyphVerdict` 数据类和一个 TODO 列表（D4 → UNCERTAIN, C1 → FAIL, 药材名 → PASS/RARE, 混淆集 → UNKNOWN），没有任何验证逻辑的骨架。

**关键缺失**：

| 缺失项 | 风险 |
|--------|------|
| **验证流水线架构** — 规则按优先级执行还是全部执行？短路的条件？ | 实现者不知道何时终止验证 |
| **术语知识库接口** — `kzocr/resources/` 下有 `confusion_set.json`，但没有术语知识库的加载/查询接口 | 引入新的文件加载逻辑到验证器 |
| **形似混淆集匹配算法** — 精确匹配？编辑距离？n-gram？上下文感知？ | 影响准确率与性能 |
| **调用方粒度** — `verifier.check(result.text, page.context)` 的签名太泛，`page.context` 是什么？ | 接口模糊，无法单元测试 |

**建议**（阻塞级）：
在 `verifier.py` 中至少定义：

```python
class VerifierRule(Protocol):
    """一条验证规则，接收文本和上下文，返回 Verdict 或 None（无意见）。"""
    def check(self, text: str, context: VerifierContext) -> GlyphVerdict | None: ...

class GlyphVerifier:
    def __init__(self, rules: list[VerifierRule]):
        self.rules = rules
    
    def verify(self, text: str, context: VerifierContext) -> GlyphVerdict:
        for rule in self.rules:
            verdict = rule.check(text, context)
            if verdict is not None and verdict.status in ("PASS", "FAIL"):
                return verdict  # 强规则短路
        # 全部通过无明确意见 → UNKNOWN
        return GlyphVerdict(status="UNKNOWN", confidence=0.5, details="No rule matched")
    
    def estimate(self, text: str, context: VerifierContext) -> GlyphVerdict:
        """非短路模式：所有规则投票，取加权结果。"""
        ...
```

这为 D4/C1/术语库/混淆集各提供一条 `VerifierRule` 实现，每条可独立测试。

### 3.2 C1 泄漏检测作为验证器存在循环依赖

**位置**：计划 "C1 leakage detection → 标记 FAIL"

`apply_leakage_defense()`（`leakage.py:149`）目前是一个**防御性后处理**：它截断被泄漏污染的页面文本。如果在 GlyphVerifier 中再次将 C1 作为验证器，会出现：

1. C1 已在 `_run_vlm()` 的跨页合并前执行（`run.py:679`：`pages_text = apply_leakage_defense(pages_text, baseline)`）
2. GlyphVerifier 再次应用 C1 → 重复检测，消耗资源
3. C1 已经**修改了文本内容**，验证器拿到的不是原始 VLM 输出

**建议**：
- 明确 C1 在编排流水线中的位置：它属于**预处理阶段**（在引擎输出进入验证器之前），还是**验证阶段**？
- 如果 C1 已经在预处理阶段执行了截断，验证器的 C1 规则只需要检测**残余泄漏**（即截断不彻底的情况），而非完整泄漏检测。
- 更好的做法：C1 作为**引擎后处理**（`engine.postprocess`），在结果交给验证器之前先清理。验证器只做字形/语义验证，不做泄漏防御。

### 3.3 D4 层级异常与验证器的集成点不明确

**位置**：计划 "D4 字符数尖峰检测 → 标记 UNCERTAIN"

`check_hierarchy_anomaly()`（`hierarchy.py:67`）接收 `list[str]`（全页文本），返回 `list[HierarchyAnomaly]`。但在 E4 编排循环中，验证器是**逐页逐引擎**调用的：

```python
verdict = verifier.check(result.text, page.context)
```

**问题**：
- D4 需要**邻居页的上下文**才能判断当前页是否有字符数尖峰。逐页调用 `verifier.check()` 时没有邻居信息。
- D4 的 `check_hierarchy_anomaly()` 是批量操作（一次分析所有页），而验证器是单页操作。

**建议**：
D4 要么提升为**EngineScheduler 的后处理阶段**（在所有页跑完后批量分析），要么将 `VerifierContext` 设计为携带邻居信息。推荐前者——因为 D4 本身就是跨页分析，放在验证器的逐页调用中语义不匹配。

---

## 4. E4 — Orchestrator（编排主循环）

### 4.1 `engine.run(page)` 没有对应的接口实现 — 阻塞级

**位置**：计划 `orchestrator.py:167` — `engine.run(page)`

当前代码库中没有任何 OCR 引擎实现 `.run(page)` 方法：

| 引擎 | 实际调用方式 | 粒度 |
|------|-------------|------|
| BookPipeline (kimi) | `pipeline.process_book(pdf_path, book_id)` | 书级 |
| PaddleOCRVl16Adapter | `adapter.recognize_page(img)` | 页级（需 numpy 图像） |
| SenseNovaAdapter | `adapter.recognize_page(img)` | 页级 |
| ShizhengptAdapter | `adapter.recognize_page(img)` | 页级 |
| Mock | `mock_book_result(book_code)` | 书级 |

**风险**：E4 伪代码假定了一个统一的 `Engine.run(page)` 接口，但这个接口不存在。书级引擎（BookPipeline）无法按页粒度调用——一次 `process_book` 会处理整本 PDF。

**建议**（阻塞级 — **必须修复**）：
1. 在 `kzocr/engine/types.py` 或 `kzocr/scheduler/__init__.py` 中定义一个抽象协议：

```python
class EngineRunner(Protocol):
    """引擎统一执行接口。"""
    def run_page(self, page: PageInput) -> AdapterPageResult: ...
    def run_book(self, pdf_path: str) -> list[AdapterPageResult]: ...  # 仅在 kind="book" 时支持
```

2. 为 BookPipeline 实现一个 `BookPipelineAdapter` 包装器，使其提供 `.run_book()` 方法（忽略 page 粒度的调用，改为拉取整本书的逐页结果）。

3. 如果 Tier 1 的 BookPipeline 无法按页调用（实际就是无法，因为它是书级管线），则 Tier 1 应该作为整体执行，然后再进入逐页的 Tier 2/Tier 3 降级。

### 4.2 书级引擎（Tier 1）与页级编排的不匹配

**位置**：计划 `orchestrator.py:159` — 三层 Tier 逐页循环

Tier 1 引擎（paddleocr, rapidocr, mineru, unirec）通过 kimi BookPipeline 调用，是**书级**处理。在 E4 的逐页循环中调用 `engine.run(page)` 会：

- **性能灾难**：每页启动一次 BookPipeline → N 次 PDF 渲染、N 次模型加载
- **结果碎片化**：BookPipeline 的输出（`final_markdown` + 结构化 pages）无法按页独立调用碎片化

**建议**（阻塞级 — **必须修复**）：
将编排模型重构为**两级流水线**：

```
         Tier 1（书级）：Tier 1 引擎整体处理 → 得到 BookResult
                ↓ 逐页取出，验证失败时进入页级降级
         Tier 2/3（页级）：逐页调用 VLM 引擎做页级补充 OCR
```

也就是：Tier 1 跑全书 → 对每一页的 `result.text` 过 GlyphVerifier → 验证失败的页进入 Tier 2/Tier 3 补充识别。这样 Tier 1 的批量优势不丢失。

### 4.3 `run_engine()` → `orchestrate_book()` 的迁移路径不完整

**位置**：计划 `engine/run.py` 变更描述

计划仅说 "`run_engine()` 改为调用 `orchestrate_book()`"，但没有说明：

1. **旧配置的兼容性**：`use_mock`, `use_vlm`, `require_real` 三个标志位如何映射到新调度策略？是否保留为快捷方式？
2. **KZOCR_USE_VLM=1 的等价行为**：当前 `use_vlm=1` 跳过 BookPipeline 直接跑 VLM。在调度器中这等价于"禁用 Tier 1，从 Tier 2 开始"。需要配置映射。
3. **Mock 模式**：`use_mock=1` 当前直接返回桩数据，不过任何管线。调度器中应注册为 `mock` 引擎（Tier 0 / special）。

**建议**：
在 `run_engine()` 中实现配置兼容层：

```python
def run_engine(pdf_path, book_code, config) -> BookResult:
    if config.use_mock:
        return build_mock_book(...)  # 保持快捷路径
    if config.use_vlm:
        # 临时兼容：仍走旧 _run_vlm 路径，但调度器就绪后改为：
        # scheduler_config = {"disable_tiers": [1]}
        # 仍需要 _run_vlm 内部逻辑（图像渲染），或直接从 _render_pages 开始
        ...
    return orchestrate_book(pdf_path, book_code, config)
```

但注意：即使 `use_vlm=1`，`_run_vlm` 内部有约 150 行的 PDF 渲染、裁剪、后处理、跨页合并逻辑，这些是引擎无关的共享计算。v0.7 必须将这些计算从 `_run_vlm` 中提取到编排层的共享函数中，否则 `_run_vlm` 和 `orchestrate_book()` 会有大量代码重复。

### 4.4 HumanGate 缺少与现有 `push_book_to_zai()` 的集成

**位置**：计划 `orchestrator.py:196-197`

计划将 `failed_pages[page_num]` 记录后 `continue`，但没有说明失败页如何推送到 zai 校对台。当前 `adapter/to_zai_prisma.py` 的 `push_book_to_zai()` 推送整本 `BookResult`，包含 `failed_pages` 字段。

**问题**：
- 验证器标记为 `UNKNOWN` 的结果页（通过了引擎但字形不确定），是**应该进入校对**的。目前 E4 伪代码对 `UNKNOWN` 的处理等同于 `FAIL`——继续尝试下一 Tier，直到最终进入 failed_pages。但如果所有 Tier 都返回 `UNKNOWN`（而非 `FAIL`），引擎认为结果"可能可用但不确定"，应该推送到校对而不是丢弃。
- 当前的 `BookResult.failed_pages` 是一个 `dict[int, str]`，只记录完全失败的页。缺少"不确定页"（`UNKNOWN`）的通道。

**建议**：
在 `BookResult` 中增加 `uncertain_pages: dict[int, GlyphVerdict]` 字段，记录通过引擎但字形验证未完全放行的页。这些页在 zai 校对台中应标记为"需人工复核"，而非完全丢弃。

---

## 5. 整体架构一致性

### 5.1 `kzocr/engine/` vs `kzocr/scheduler/` 职责划分

| 目录 | 当前职责 | 建议 |
|------|---------|------|
| `kzocr/engine/` | 数据模型（types.py）+ 驱动器（run.py）+ Mock | 保留数据模型，驱动器职责逐步移交 scheduler |
| `kzocr/engines/`（复数） | 引擎无关的工具（errors, leakage, hierarchy, ratelimit, _common） | **命名与 engine/ 极易混淆**，建议未来考虑重命名为 `kzocr/common/` 但现阶段不动 |
| `kzocr/scheduler/` (new) | 注册 + 调度 + 验证 + 编排 | ✅ 新增，职责清晰 |

**问题**：
1. `kzocr/engine/` 和 `kzocr/engines/` 仅一个字母之差，且都包含跨引擎逻辑。现有 `engines/_common.py` 中的 `adapter_to_line_result()` 和 `engines/errors.py` 中的重试策略在 v0.7 中会被 scheduler/ 大量引用。
2. `run_engine()` 在 `engine/run.py` 中，计划要改它去调用 `scheduler/orchestrator.py` 的 `orchestrate_book()`。这产生了**循环包引用**的风险：`engine/run` → `scheduler/orchestrator` → ？ → `engine/types`。好在此处是单向（`scheduler/` 引用 `engine/types`），但需要监控。

**建议**：
- 在 `__init__.py` 中严格控制导入：`scheduler/` 只能引用 `engine/types.py` 和 `engines/`（工具），不能反向引用 `engine/run.py`。
- 考虑将 `engines/_common.py` 中的 `adapter_to_line_result()` 迁移到 `engine/types.py` 或 `engine/convert.py`，减少 `scheduler/` 对 `engines/` 的跨包引用。

### 5.2 配置层膨胀风险

**位置**：计划 `config.py` 新增配置

当前 `config.py` 已有约 20 个配置项。v0.7 新增：
- `KZOCR_MAX_TIER1_ENGINES`
- `KZOCR_BENCHMARK_DIR`
- 可能还有 `KZOCR_MAX_TIER2_ENGINES`, `KZOCR_VERIFIER_STRICTNESS` 等

**问题**：配置项趋于散乱，无命名空间分组，无默认值策略文档。

**建议**：在 `config.py` 中引入配置分组注释或 `@dataclass` 嵌套：

```python
@dataclass
class SchedulerConfig:
    max_tier1_engines: int = 2
    max_tier2_engines: int = 1
    max_tier3_engines: int = 1
    benchmark_dir: str = "/tmp/kzocr/benchmark"

@dataclass
class Config:
    ...
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
```

环境变量映射对应为 `KZOCR_SCHEDULER_MAX_TIER1_ENGINES` 等，避免扁平的命名冲突。

### 5.3 测试策略中的集成测试缺口

**位置**：计划 `tests/test_orchestrator.py`

新增 4 个测试模块，但缺少：
- **集成测试**：`run_engine()` → `orchestrate_book()` 的端到端验证（含 mock 引擎注册）
- **降级链测试**：Tier 1 全部失败 → 自动降级到 Tier 2 → Tier 3
- **预算到期测试**：超时后是否正确跳过剩余页
- **benchmark 持久化测试**：重启后 stats 恢复

**建议**：新增 `tests/test_scheduler_integration.py`，用 mock 引擎注册 3 个 Tier，验证完整的编排循环。

---

## 6. 架构遗漏与风险

### 6.1 多引擎共识机制未设计

计划"目标"列声称"多引擎并行（同一页）"，但 E4 伪代码是**顺序降级**，不是并行共识。多引擎共识是传统 OCR 提升准确率的核心手段（如 3 个引擎投票）。

**风险**：如果 v0.7 的目标包含并行共识，E4 的设计需要根本性改动——从"找到第一个 PASS 就 break"变为"收集多个结果并投票"。这是两种完全不同的编排模型。

**建议**：明确 v0.7 的范围——如果是**顺序降级**（当前 E4 伪代码），删除"多引擎并行"的声明，推迟到 v0.8。如果是**并行共识**，重构 E4 设计。

### 6.2 使用 `except Exception: continue` 的隐式风险

**位置**：计划 `orchestrator.py:166` — `result = engine.run(page)`

如果 `engine.run(page)` 抛出意料之外的异常（非 `OcrError` 子类），当前伪代码没有捕获机制。参考当前 `_run_vlm`（`run.py:658`）有 `except Exception: continue`。

**建议**：E4 中每引擎调用应包裹 `try/except OcrError`，并有一个 `except Exception` 作为最后的防御，记录错误后继续下一引擎。

### 6.3 `_init_vlm_adapter()` 的迁移

`run.py:224-268` 的 `_init_vlm_adapter()` 包含 SenseNova → PaddleOCR-VL 的硬编码降级链。v0.7 的调度器应该接管这个降级逻辑。

**问题**：`_init_vlm_adapter()` 在 Tier 2 和 Tier 3 中哪个层？SenseNova 是云端 VLM（Tier 2），PaddleOCR-VL 是本地 VLM（计划中列为 Tier 3）。如果 Tier 2 和 Tier 3 的候选名单都需要这两个引擎，`_init_vlm_adapter()` 的硬编码降级链会干扰调度器的决策。

**建议**：将 `_init_vlm_adapter()` 的逻辑分散到各引擎的注册初始化中，SenseNova 注册为 Tier 2 候选，PaddleOCR-VL 注册为 Tier 3 候选（或根据配置也作为 Tier 2）。`_init_vlm_adapter()` 函数在 v0.7 实施完成后移除。

### 6.4 `EngineRegistration.config` 与现有 `_build_engine_config()` 的关系

`_build_engine_config()`（`run.py:69-106`）从环境变量构建 kimi BookPipeline 的 config 字典。新方案中每个引擎有独立的 `config: dict`。这两个配置模型是什么关系？

**风险**：如果 `EngineRegistration.config` 完全取代 `_build_engine_config()`，则需要为每个引擎定义独立配置映射。如果保留 `_build_engine_config()`，就有两套配置生成逻辑，增加维护成本。

**建议**：将 `_build_engine_config()` 分解为逐引擎的配置构建函数，每个引擎的 `EngineRegistration.config` 由对应的配置构建函数填充。

---

## 7. 阻塞项（必须修复后方可实施）

| 编号 | 严重度 | 位置 | 问题 | 修复方向 |
|------|--------|------|------|---------|
| **B1** | 阻塞 | E4, `orchestrator.py:167` | `engine.run(page)` 接口不存在。书级引擎（BookPipeline）无法按页调用 | 定义 `EngineRunner` 协议；将编排改为两级流水线（先书级再逐页降级） |
| **B2** | 阻塞 | E3, `verifier.py` | GlyphVerifier 仅有数据结构无验证规则架构；D4/C1/术语库的集成点不明确 | 定义 `VerifierRule` 协议和流水线架构；每项检测实现为独立 Rule |
| **B3** | 高 | E1, `registry.py` | `AdapterMeta` 缺少 tier、probe 字段；`EngineStats` 的 `monotonic()` 与持久化矛盾 | `AdapterMeta` 扩展 tier/probe；`last_seen` 改用 `time.time()`；明确 stats 生命周期 |
| **B4** | 高 | E2, `scheduler.py:108` | 评分公式冷启动崩溃（`0 × ∞ = NaN`）| 采用贝叶斯平均平滑 |
| **B5** | 高 | E4, `orchestrator.py` | `run_engine()` → `orchestrate_book()` 迁移路径不完整：旧配置映射、`_run_vlm` 代码重复、`_init_vlm_adapter()` 冲突 | 定义配置兼容层；提取共享 PDF 渲染逻辑；移入 `_init_vlm_adapter()` 到注册初始化 |

---

## 8. 建议项（非阻塞，推荐实施）

| 编号 | 优先级 | 位置 | 建议 |
|------|--------|------|------|
| S1 | 中 | E2, 并行共识 | 明确 v0.7 范围是顺序降级还是并行共识；若为顺序则删除"多引擎并行"声明 |
| S2 | 中 | E4, HumanGate | 在 `BookResult` 中增加 `uncertain_pages: dict[int, GlyphVerdict]`，将 `UNKNOWN` 结果推送校对而非丢弃 |
| S3 | 中 | 配置 | 在 `Config` 中引入 `SchedulerConfig` 嵌套 dataclass 分组，避免配置项散乱 |
| S4 | 低 | E1, `_build_engine_config()` | 将 `_build_engine_config()` 分解为逐引擎配置函数，每引擎的 config 独立构造 |
| S5 | 低 | E4, 异常处理 | 引擎调用包裹 `try/except OcrError` + `except Exception` 防御 |
| S6 | 中 | 测试 | 新增 `test_scheduler_integration.py` 端到端集成测试（含降级链、预算到期、benchmark 持久化） |
| S7 | 低 | 目录命名 | `kzocr/engine/` 和 `kzocr/engines/` 命名容易混淆，建议未来统一为 `kzocr/core/` 或明确指出差异（当前不建议动） |
| S8 | 低 | E4, budget | 在每 Tier 尝试前加 `budget.check()`，当前仅在页级循环中检查 |

---

## 9. 总结

v0.7 自适应引擎编排层方案方向正确，立意清晰——将 `run.py` 中积累的三个 `if-else` 分支重构为注册-调度-验证-编排的架构模型，是演进到多引擎支持的必要步骤。

但在进入实施前必须解决 **5 个阻塞性问题**（B1-B5）：

1. **B1 是最大风险**：`engine.run(page)` 接口不存在，且 Tier 1 引擎是书级的，无法逐页调用。需要重构编排模型为两级流水线（先书级再逐页降级），并定义 `EngineRunner` 协议。

2. **B2 是设计缺口**：GlyphVerifier 仅有壳无骨架。如果不定义验证规则流水线架构，D4/C1/术语库的集成会成为实现中的最大障碍。

3. **B3/B4 是工程细节但会导致运行时 bug**：冷启动崩溃和持久化矛盾会在实际使用中暴露。

4. **B5 是迁移安全性**：旧配置兼容层不定义清楚，实施中可能会破坏现有 `use_mock`/`use_vlm` 工作流。

**总体裁决**：方案的结构方向获得批准，但 **必须在计划的下一版本中闭合 B1-B5** 后方可进入实施。建议在 v0.7 方案中新增一个章节"实施前确认的接口契约"，明确定义 `EngineRunner` 协议、`VerifierRule` 协议、配置兼容层映射表。

---

## 附录：关键文件引用索引

| 引用文件 | 行号 | 说明 |
|---------|------|------|
| `kzocr/engine/types.py` | 13 | `GlyphStatus` 定义 |
| `kzocr/engine/types.py` | 132-139 | `ProbeResult` 全局探测结构 |
| `kzocr/engine/types.py` | 146-157 | `AdapterMeta` 当前字段（缺 tier/probe） |
| `kzocr/engine/run.py` | 43-67 | `run_engine()` — 被替换的入口 |
| `kzocr/engine/run.py` | 69-106 | `_build_engine_config()` — 引擎配置构建 |
| `kzocr/engine/run.py` | 224-268 | `_init_vlm_adapter()` — 需迁移的硬编码降级链 |
| `kzocr/engine/run.py` | 538-700 | `_run_vlm()` — 需提取共享 PDF 渲染/后处理逻辑 |
| `kzocr/engine/run.py` | 585 | `KZOCR_TOTAL_TIMEOUT` 现有预算检查 |
| `kzocr/engines/_common.py` | 23-88 | `adapter_to_line_result()` — 唯一转换入口 |
| `kzocr/engines/leakage.py` | 149-201 | `apply_leakage_defense()` — C1 四层防御 |
| `kzocr/engines/hierarchy.py` | 67-134 | `check_hierarchy_anomaly()` — D4 跨页分析 |
| `kzocr/config.py` | 27-117 | `Config` 当前配置项 |
| `kzocr/resources/confusion_set.json` | 1-60+ | 形似混淆集 |
