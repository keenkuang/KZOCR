# 软件工程评审 — Round 3 (v0.7 详细设计文档)

| 字段 | 值 |
|---|---|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.7-DETAILED.md` |
| 审查角色 | 软件工程 |
| 审查人 | code-reviewer-9 |
| 日期 | 2026-07-10 |
| 代码基线 | `kzocr/engine/types.py`, `kzocr/config.py`, `kzocr/engines/errors.py`, `kzocr/engine/run.py` |

---

## 总体判断：**条件通过 (CONDITIONALLY APPROVED)** — 详细设计在 API 一致性上有 3 项阻塞级类型不匹配、2 项中等风险的新问题，需在 Phase 1 实施前修正。

详细设计文档相对于规划方案（v0.7.md）有大量具体化提升：伪代码可执行程度高、测试用例枚举充分、迁移策略清晰。但文档中定义的数据类与现有 `kzocr/engine/types.py` 之间存在多处字段名、字段类型、字段集合的偏离，这些偏离在文档中未标注为 breaking changes。

---

## 一、API 签名与数据类一致性（3 项阻塞）

### C1: `PageInput.img` vs 现有 `PageInput.image` — 字段名不匹配

**严重程度：阻塞**

| | 设计文档 (§1.6) | 现有 `types.py:181` |
|---|---|---|
| 字段名 | `img: "np.ndarray"` | `image: np.ndarray` |
| 类型注解 | `"np.ndarray"`（字符串前瞻引用） | `np.ndarray`（直接类型） |

设计文档使用 `img` 而现有代码使用 `image`。这是一个二进制不兼容的字段名变更。

**影响范围：** 所有现有适配器调用（如 `kzocr/engine/run.py` 中的 `_run_vlm` 创建 `PageInput` 时均使用 `image=` 关键字参数）。

**建议：** 统一为字段名 `image`（保持向后兼容），或在文档中明确声明此变更为 breaking change，并提供 `image` → `img` 的迁移路径。

---

### C2: `PageLayout` 定义与现有类型偏离

**严重程度：阻塞**

| 字段 | 设计文档 (§1.6) | 现有 `types.py:188` | 分析 |
|---|---|---|---|
| `page_num` | ❌ 缺失 | `int` (必填) | 删除必填字段 = 破坏性变更 |
| `orientation` | `str = "horizontal"` | `str = "horizontal"` | 一致 |
| `is_vertical` | `bool = False` | `bool = False` | 一致 |
| `has_table` | `bool = False` | ❌ 缺失 | 新增字段，兼容 |
| `estimated_lines` | ❌ 缺失 | `int = 0` | 删除字段 = 破坏性变更 |

设计文档的 `PageLayout` 如果替换现有类型，等于删除了 `page_num` 和 `estimated_lines` 字段。但 `page_num` 在现有代码中被大量引用（`PageLayout` 通常在已知页号上下文中使用，但作为数据类被序列化传递时 `page_num` 是必要的关联信息）。

**建议：** 若意图是替换，保留 `page_num` 和 `estimated_lines` 为可选字段（`Optional[int]`），新增 `has_table`。若意图是新增子类型（如 `SchedulerPageLayout`），则用不同类名避免混淆。

---

### C3: `EngineCallRecord` 字段膨胀导致类型歧义

**严重程度：阻塞**

| 字段 | 现有 `types.py:197` | 设计文档 (§6.2) / (§1.6) | 冲突 |
|---|---|---|---|
| `latency_ms` | `float` | `int` | **类型不同** |
| `glyph_status` | `Optional[str]` | `str \| None = None` | 语义等价 |
| `error` | `Optional[str]` | `str \| None = None` | 语义等价 |
| `status` (引擎状态) | ❌ 无 | `str = "HEALTHY"` | 新增 ✓ |
| `detector_chain` | ❌ 无 | `list[str] = []` | 新增 ✓ |
| `ts` | ❌ 无 | `float = 0.0` | 新增 ✓ |
| `cache_hit` | ❌ 无 | `bool = False` | 新增 ✓ |
| `breakdown` | ❌ 无 | `dict[str, float] = {}` | 新增 ✓ |

`latency_ms` 从 `float` 变为 `int` 是二进制不兼容变更。任何现有代码中传递 `latency_ms=123.45` 的调用点都会在运行时静默截断为 123。

**建议：** 保留 `latency_ms` 为 `float`，仅在文档/序列化时取整。或在文档中标注此变更为 breaking change 并 grep 确认无小数点调用。

---

## 二、数据类设计问题（2 项高、3 项中）

### H1: `EngineRegistration.adapter` 类型应为 `EngineRunner | None` 而非 `Callable | None`

**严重程度：高**

设计文档 §1.2 定义 `adapter: Callable | None = None`，但 §6.1 定义了 `EngineRunner(Protocol)` 作为引擎统一执行接口。`Callable` 丢失了 `run_page` / `run_book` 的类型约束，无法利用静态类型检查捕获签名不匹配错误。

```python
# ❌ 当前设计文档：
adapter: Callable | None = None

# ✅ 应改为：
adapter: EngineRunner | None = None
```

**建议：** 将 `adapter` 类型改为 `EngineRunner | None`，或将 `EngineRunner` 作为 `Protocol` forward reference（如果存在循环依赖风险）。

---

### H2: `PageInfo` vs `PageInput` 字段过度重叠

**严重程度：高**

文档 §4.3 定义了 `PageInfo` 用于调度器输入：

```python
@dataclass
class PageInfo:
    page_num: int
    book_type: str = ""
    pub_era: str = ""
    is_vertical: bool = False
    has_table: bool = False
```

但这 5 个字段中：
- `page_num` — 已存在于 `PageInput.page_num`
- `is_vertical`, `has_table` — 已存在于 `PageLayout`（在 `PageInput.layout` 中）
- `book_type`, `pub_era` — 来自书级配置 `BookResult.book_type` / `BookResult.pub_era`

这意味着调用方每次调用 `select_candidates()` 时都需要手动从多个数据源拼装 `PageInfo`。这会增加调用代码的复杂度和出错概率。

**建议：**
- 方案 A：将 `book_type` / `pub_era` 作为 `Budget` 的字段（或作为全局 context），`select_candidates()` 从现有类型推导页面相关信息
- 方案 B：接受冗余，将 `PageInfo` 改为 `PageContext`（含 book 级维度），同时接受 `PageInput` 和 `PageLayout` 作为构造参数

---

### M1: `domain_adjust()` 竖排分支的过早 return 导致后续规则死代码

**严重程度：中**

```python
# §4.3, line 608
if page_layout and page_layout.is_vertical and tier >= 2:
    return base_score * 1.5 + 0.2 + adjustments
```

此处的 `return` 导致竖排页的 laser 快引擎和 formula 高召回调整永远不会生效。`adjustments` 在此行之前始终为 `0.0`（仅在第 604 行初始化，在 608 行之前从未被修改）。两种可能性：

1. **设计意图** — 竖排页不需要 laser/formula 调整：需要注释明确说明，并移除 `+ adjustments`（纯迷惑）。
2. **不是设计意图** — 竖排页应先应用垂直偏移，再叠加后续调整：需将 return 移到函数末尾，让后续 `if` 语句生效。

**建议：** 明确选择并注释。如果是意图——简化 return 值为 `base_score * 1.5 + 0.2` 并注明"竖排页跳过激光/方剂调整"。

---

### M2: `_exhausted` 与 `exhaust()` 在串行模式下无安全问题，但字段命名混淆

**严重程度：中**

```python
@dataclass
class Budget:
    _exhausted: bool = False        # "私有"字段
    def exhaust(self) -> None:
        self._exhausted = True
    @property
    def exhausted(self) -> bool:
        return self._exhausted
```

`_exhausted` 以下划线前缀似乎是私有字段意图，但 `exhaust()` 方法是公开 API。Python 下划线仅为命名约定，不阻止外部写入 `budget._exhausted`。

在串行模式下（v0.7 默认为串行），这不是竞态问题。但如果未来引入并行化（`engine_parallel=True`），Budget 没有线程锁保护。

**建议：**
1. 添加注释说明串行模式下非线程安全
2. 考虑使用 `@dataclass(frozen=True)` + `replace()` 模式（functional budget），而非就地变异

---

### M3: `SchedulerConfig` 与 `Config` 的 `allow_cloud_vision` 重复

**严重程度：中**

现有 `Config`（`config.py:60`）已有 `allow_cloud_vision: bool = False`。设计文档 §9.2 在 `SchedulerConfig` 中也定义了 `allow_cloud_vision: bool = False`，§9.4 的 Config 扩展中 `scheduler: SchedulerConfig` 作为嵌套字段。

这会导致两个层级都有同一个配置项，产生歧义：
- `config.allow_cloud_vision`（顶层）
- `config.scheduler.allow_cloud_vision`（嵌套）

如果两者不同步，将产生不确定行为。

**建议：** 只在 `SchedulerConfig` 中保留，Config 层通过 `__getattr__` 委派，或统一为单一路径。同时对齐环境变量映射表（§9.3 应明确映射路径）。

---

## 三、并发安全分析

在 v0.7 串行模式下，以下问题不会触发竞态，但作为架构隐患应记录。

### 3.1 `EngineRegistry._lock` 保护不足

`EngineRegistry` 使用 `threading.Lock` 保护 `register()`、`mark_unavailable()`、`record()` 等写操作。但 `select_candidates()` 中的 `_score()` 函数在无锁情况下直接从 `engine.stats` 读取多个字段：

```python
# §4.1, _score()
pass_rate = engine.stats.glyph_pass_rate    # → 读取多个 stats 字段
latency = max(engine.stats.avg_latency_per_page_ms, 1.0)
decay = engine.stats.decay(now)
```

如果未来并行化（`engine_parallel=True`），一个线程正在执行 `record()`（累计写入 stats）而另一线程同时执行 `_score()`（读取 stats），可能读到部分更新的不一致中间态。

**风险评估：** v0.7 串行模式下无风险。但 `_lock` 的存在暗示未来并行意图，建议在 `select_candidates()` 中加读锁（`RLock` 或副本快照），或将 `_score()` 操作限定为 stats 的快照拷贝。

### 3.2 `_append_benchmark()` 文件锁正确

§8.5 的 `fcntl.flock` 实现正确：多进程追加写入使用独占锁防止行交错。`os.fsync` 确保写入落盘。

### 3.3 `concurrent.futures` 超时僵尸线程

§7.3 承认 `future.result(timeout=timeout_s)` 不会终止后台线程。注释称"v0.7 串行模式下数量可控（≤3），可接受"，但单个挂起的 HTTP 连接可能不会在 socket 层面超时，如果 Tier 2 云引擎调用挂死，线程池的工作线程会被长期占用。多次超时后可能耗尽默认的 `ThreadPoolExecutor` 线程。

**建议：**
- 使用 `ThreadPoolExecutor(max_workers=1)` 每次新建实例可接受（如当前设计），但应确保 `__exit__` 的 `.shutdown(wait=False)` 不阻塞主流程
- 考虑换成 `func_timeout`（signal-based）或 `asyncio.wait_for()` 方案

---

## 四、与现有代码风格一致性

### 4.1 `from __future__ import annotations` 遗漏

现有 `kzocr/engine/types.py:6`、`kzocr/config.py:8`、`kzocr/engines/errors.py:12` 全部使用了 `from __future__ import annotations`。设计文档所有代码块均未包含此 import。

Python 3.10+ 特性：缺失此 import 不影响功能（PEP 604 的 `str | None` 语法原生支持），但风格统一性建议加上。

### 4.2 `Optional[str]` vs `str | None`

| 位置 | 风格 |
|---|---|
| 现有 `types.py` | `Optional[str]`（统一） |
| 现有 `config.py` | `str = ""`（无 Optional，默认空字符串） |
| 设计文档 §1.2 | `str \| None = None` |
| 设计文档 §9.1 | `str = ""` |

设计文档使用 `str | None = None` 与现有 `Optional[str]` 混合。虽然功能等价，建议统一风格。鉴于项目大部分现有文件使用 `Optional[...]` 风格且未使用 Python 3.10+ 的 `A | B` 联合语法（`config.py` 除外），建议在 types.py 中保持 `Optional[str]`。

### 4.3 类型注解引号风格

现有 `types.py` 对 `PageInput` 和 `PageLayout` 使用 `"PageLayout | None"`（字符串前瞻引用），但设计文档使用直接类型或 `"np.ndarray"` 风格。建议统一为字符串前瞻引用风格以兼容 `from __future__ import annotations`。

### 4.4 字段顺序规范

现有 `types.py` 的 dataclass 风格：必填字段在前，可选字段在后，可选字段有默认值。设计文档遵循此规范。

### 4.5 模块级 import 顺序

现有代码风格：标准库 → 第三方 → 本地导入，每组空行分隔。设计文档未显式控制 import 风格，建议实施时对齐。

---

## 五、已定义与未定义的辅助函数

### 5.1 伪代码中引用但未定义的函数

| 函数名 | 引用位置 | 未定义的影响 |
|---|---|---|
| `_safe_select_candidates()` | §7.1 | 核心函数，推测是 `select_candidates()` 的包装器，带 try/except |
| `_make_page_info()` | §7.1 Tier 2 | 从 page_num + layout 构造 PageInfo |
| `_load_vlm_cache()` / `_save_vlm_cache()` | §7.6 | 重试 try 中提到，但 VLM 缓存已在 `run.py` 中作为 `_load_cache_text` / `_save_cache_text` 存在 |
| `_record_engine_usage()` | §7.1 多处 | 统计记录，应调用 `registry.record()` |
| `_run_book_engine()` | §7.1 | BookPipeline 调用包装器 |
| `_run_page_engine()` | §7.6 | 页级引擎调用包装器（与 `_run_single_engine_with_timeout` 的关系未明确） |

**`_safe_select_candidates` 定义推测：**
```python
def _safe_select_candidates(scheduler, registry, tier=1, ...):
    """select_candidates 的安全包装，空结果时不抛异常。"""
    try:
        candidates = scheduler.select_candidates(registry, tier, ...)
        return candidates or []
    except Exception:
        return []
```

对 `_load_vlm_cache` / `_save_vlm_cache`：现有 `run.py` 的 `_load_cache_text()` / `_save_cache_text()` 功能相同但命名不同。建议复用现有函数而非重命名。

### 5.2 `EngineScheduler` 类 vs `scheduler.py` 模块

`orchestrate_book()` 伪代码引用 `scheduler = EngineScheduler(config)`，但设计文档只在 §4.1 定义了 `select_candidates()` 函数（模块级）。`EngineScheduler` 类未在任何地方定义。

**建议：** 在 `scheduler.py` 中定义：

```python
class EngineScheduler:
    """调度器。封装 select_candidates 及相关配置。"""
    def __init__(self, config):
        self.config = config

    def select_candidates(self, registry, tier, page_info, budget, ...) -> list:
        # 委托给模块级 select_candidates() 或直接内联
        ...
```

---

## 六、Phase 依赖分析

### 6.1 依赖图验证

```
Phase 1 (基础准备)：
  types.py: AdapterMeta 扩展 ← Phase 2 scheduler 依赖
  types.py: EngineRunner 协议 ← Phase 2/3 调度器 + 编排依赖
  types.py: 新增类型 (EngineCallRecord 等)
  config.py: SchedulerConfig
  errors.py: SchedulerError 等
  registry.py: EngineRegistration / EngineStats / EngineRegistry ← Phase 2 调度器依赖
  render_pages() 提取 ← Phase 3 编排依赖
  benchmark NDJSON 持久化 ← Phase 2/3 统计依赖
  conftest.py + test_registry.py

Phase 2 (核心逻辑)：
  scheduler.py ← 依赖 Phase 1 types + registry + config
  verifier.py ← 依赖 Phase 1 types（GlyphVerdict）
  test_scheduler.py + test_verifier.py

Phase 3 (编排 + 集成)：
  orchestrator.py ← 依赖 Phase 1 + Phase 2 全部
  run.py 委派改造 ← 依赖 Phase 1 + Phase 3
  CLI 扩展 ← 依赖 Phase 3 orchestrator
  test_orchestrator.py + test_regression.py
```

**依赖合理。** 没有反向依赖或循环依赖。

### 6.2 缺失的依赖项

| 缺失项 | 应在哪个 Phase 加入 | 缺失后果 |
|---|---|---|
| `kzocr/scheduler/__init__.py` | Phase 1 或 Phase 2 | Phase 2 不存在 `scheduler` 包 |
| `kzocr/verifier/__init__.py` + `kzocr/verifier/detectors/` 子模块 | Phase 2 | 5 个 Detector 文件组织方式未指定 |
| `kzocr/scheduler/egress.py` (validate_url) | Phase 1 | Round 2 N1 已提及，未解决 |
| `PageInfo` 构造辅助函数 | Phase 1 | 否则 Phase 2 scheduler 依赖拼装 |

### 6.3 Phase 边界建议

当前 Phase 1 内容已足够，但建议在 Phase 1 末尾增加一个子任务：

```
1.9: 创建 Phase 2/3 所需的空桩模块
  - kzocr/scheduler/__init__.py（空）
  - kzocr/verifier/__init__.py（空）
  - kzocr/scheduler/egress.py（validate_url 空实现）
```

---

## 七、边界情况与异常路径

### 7.1 现有边界情况评估

| 场景 | 设计文档处理 | 评估 |
|---|---|---|
| Tier 1 book 引擎无 pages（空 BookResult） | `t1_elapsed_per_page = t1_elapsed` | 正确，但 `if tier1_result and tier1_result.pages` 防御已足够 |
| Tier 1 引擎抛出异常（非引擎逻辑问题） | except 后 `tier1_result = None` | `registry.record()` 未调用（Round 2 遗留） |
| 所有 Tier 候选都为空 | `select_candidates()` 返回 []，Tier 循环跳过 | 最终进入 HumanGate，正确 |
| VLM 缓存文件损坏 | 未显式处理 | 建议 catch `(OSError, ValueError)` 并降级到直接引擎调用 |
| 多本书并行渲染 | 当前串行，无问题 | 未来并行化时需要考虑 |

### 7.2 测试策略评估

与 Round 2 相比，详细设计的测试用例显著细化：

| 文件 | Round 2 | Round 3 (详细设计) | 评估 |
|---|---|---|---|
| `test_registry.py` | ≥ 8 | ≥ 14 | 充分 |
| `test_scheduler.py` | ≥ 8 | ≥ 17 | 充分 |
| `test_verifier.py` | ≥ 10 | ≥ 17 | 充分 |
| `test_orchestrator.py` | ≥ 6 | ≥ 15 | 充分（8 路径 + 7 额外）|
| `test_regression.py` | ≥ 5 | ≥ 5 | 充分 |

覆盖缺口：
- **benchmark 磁盘 IO 异常** — 未包含
- **KZOCR_TRACE_DIR=""（禁用 trace）** — 未验证
- **`engine_parallel=True` + 无 GPU** — 伪代码中已处理（WARNING + 重置为 False），无测试
- **`disabled_tiers` 空列表 vs 部分禁用** — 边界测试

---

## 八、汇总与裁决

### 阻塞问题（Phase 1 实施前必须修正）

| # | 问题 | 位置 | 修复建议 |
|---|---|---|---|
| C1 | `PageInput.img` vs `image` | §1.6 → `types.py:181` | 统一为 `image` |
| C2 | `PageLayout` 字段增减未标注 | §1.6 → `types.py:188` | 保留现有字段，新增 `has_table` |
| C3 | `EngineCallRecord.latency_ms` 类型 `int` vs `float` | §6.2 → `types.py:201` | 统一为 `float` |

### 高优先级问题

| # | 问题 | 位置 | 修复建议 |
|---|---|---|---|
| H1 | `adapter: Callable` 应改为 `EngineRunner` | §1.2 | 类型约束 |
| H2 | `PageInfo` 与已有类型冗余 | §4.3 | 合并或委派构造 |

### 中等风险问题

| # | 问题 | 位置 |
|---|---|---|
| M1 | `domain_adjust()` 竖排 return 导致后续规则死代码 | §4.3 |
| M2 | `_exhausted` 非线程安全 + 命名混淆 | §1.5 |
| M3 | `allow_cloud_vision` 双层定义 | §9.2 + §9.4 |
| M4 | `_safe_select_candidates` / `EngineScheduler` 等 6 个辅助符号未定义 | §7.1 多处 |

### Round 2 未解决遗留

| # | 问题 | Round 2 状态 | Round 3 |
|---|---|---|---|
| N1 | `egress.py` 未创建 | 中等 (N1) | 仍缺失 |
| N2 | `ProbeResult.keys` breaking change | 中等 (N2) | 仍未标注迁移影响范围 |
| N5 | prior=0.7 vs default=0.5 | 低 (N5) | 设计文档仍同时定义两个不同值 |

---

### 最终裁决

| 维度 | 评分 | 说明 |
|---|---|---|
| API 签名一致性 | ⚠️ 3 项阻塞 | C1/C2/C3 均为类型不匹配，修复成本低但影响大 |
| 数据类完整性 | ✅ 整体良好 | 测试用例枚举等充分，但 `PageInfo` 冗余 |
| 并发安全 | ✅ 串行充分 | 可接受，并行化需重新评估 |
| 风格一致性 | ⚠️ 需对齐 | `Optional` vs `str\|None`、`__future__` annotations |
| Phase 依赖 | ✅ 结构合理 | 无循环依赖，但 6 个辅助符号未定义 |
| Round 2 遗留 | ⚠️ 3 项未解决 | N1/N2/N5 仍存 |

**裁决：条件通过 (CONDITIONALLY APPROVED)**

必须在 Phase 1 实施前修复：
1. **C1 / C2 / C3** — 修正 `PageInput.image`、`PageLayout` 保留字段、`latency_ms: float`
2. **H1** — 修正 `adapter: EngineRunner | None`
3. **定义缺失的 6 个辅助函数**（至少作为空桩或签名声明）
4. **创建 `kzocr/scheduler/` 和 `kzocr/verifier/` 包目录**

推荐在 Phase 1 期间解决：
5. `domain_adjust()` 竖排分支持注释说明（M1）
6. 统一 `Optional[str]` 风格（与现有代码对齐）
7. Round 2 N2（ProbeResult.keys）增加迁移检查项
