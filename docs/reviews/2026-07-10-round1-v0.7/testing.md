# v0.7 自适应 OCR 引擎编排层 — 测试方案评审报告

> 评审人：测试工程师
> 评审对象：`docs/plans/ocr-engine-unification.v0.7.md`
> 基准：现有 17 个测试文件（~260 个测试用例）
> 日期：2026-07-10

---

## 总体判断

**方案整体方向正确，但测试策略有 3 个致命缺口和 5 个重要遗漏。**

优势：
- 按模块拆分的 4 个新测试文件（registry/scheduler/verifier/orchestrator）结构合理
- 现有测试覆盖度高（17 文件约 260 用例），mock 风格一致，经验可复用
- 现有测试中有多个可直接复用的测试模式（如 `test_real_engine.py` 的 `mock_real_env` fixture、`test_vlm.py` 的局部 fixture 模式）

致命缺口：
1. **全路径覆盖缺少集成测试层**——四个组件各自 mock 彼此，没有任何测试验证 `registry + scheduler + verifier + orchestrator` 的完整链路
2. **缺回归迁移策略**——方案只是说"`run_engine()` 改为调用 Orchestrator"，但未提及如何保证 260 个现有测试不受影响
3. **benchmark 持久化未定存储方案**——无从设计持久化测试

---

## 逐项评审

### 1. 四个新测试文件覆盖度评估

**test_registry.py** — 覆盖度中等偏低

| 应覆盖 | 方案提及 | 评审意见 |
|--------|---------|---------|
| `EngineRegistration` 数据类构造 | 未明确 | 基础契约测试应加 |
| `EngineStats` 字段及 `avg_latency_per_page_ms`/`glyph_pass_rate` 计算 | 未明确 | 计算逻辑容易浮点误差，必须有 epsilon 断言 |
| `probe_engines()` 逐引擎探测（端口/API key/GPU/模型文件） | 隐式提及 | **核心难点**：9 个引擎各有不同探测条件，需要构造 9 组 mock 环境 |
| 探测失败的容错（某引擎端口不通不应阻塞全部） | 未明确 | 必须覆盖"部分引擎可用"和"全部不可用" |
| benchmark 持久化 | 未明确 | 见第 6 点 |
| 注册去重（同一 name 二次注册） | 未明确 | 边界情况 |
| `status` 状态转换（HEALTHY→DEGRADED→UNAVAILABLE） | 未明确 | 当前无状态转换逻辑，但 `EngineStatus` 既是枚举就应测试 |

**test_scheduler.py** — 覆盖度中等

| 应覆盖 | 方案提及 | 评审意见 |
|--------|---------|---------|
| `glyph_pass_rate × (1/avg_latency)` 加权排序 | 隐式提及 | **确定性难题**：见第 3 点 |
| Tier 约束过滤（OCR 引擎不进入 Tier 2） | 未明确 | 每个 tier 的候选列表需构造精确 |
| 资源过滤（UNAVAILABLE 状态、VRAM、网络） | 未明确 | 组合爆炸：至少测试 3 种过滤条件 + 排列 |
| 预算检查（wall-clock + token） | 未明确 | `Budget` 数据结构未定义，暂无法设计用例 |
| `select_candidates` 返回 N 个候选（N 可配置） | 隐式提及 | N=0,N=1,N=default,N>available 都要覆盖 |
| 候选池为空时的行为 | 未明确 | 应返回空列表，Orchestrator 据此降级 |

**test_verifier.py** — 覆盖度中高，但 fixture 构造成本高

| 应覆盖 | 方案提及 | 评审意见 |
|--------|---------|---------|
| D4 字符数尖峰 → UNCERTAIN | 提及 | 可复用 `test_hierarchy.py` 中现成的 `check_hierarchy_anomaly` 测试数据 |
| C1 跨页泄漏 → FAIL | 提及 | 可复用 `test_leakage.py` 的泄漏检测构造 |
| 药材名/术语知识库匹配 → PASS/RARE | 提及 | 依赖 `ResourceStore`，需要注入受控种子数据 |
| 形似混淆集 → UNKNOWN | 提及 | 同上 |
| 空输入/全未知输入 | 未明确 | 边界 |
| 多规则同时匹配时的优先顺序 | 未明确 | 例如：既命中知识库 PASS 又命中泄漏 FAIL，谁优先？ |

**test_orchestrator.py** — 覆盖度偏低，全路径覆盖成本高

| 应覆盖 | 方案提及 | 评审意见 |
|--------|---------|---------|
| Three-tier 三级兜底循环（T1 pass→跳过 T2/T3） | 部分提及 | 见第 4 点 |
| 多引擎兜底（同 tier 内引擎 fail→下一个） | 未明确 | 需要构造引擎列表 + mock 每个引擎的返回 |
| HumanGate（全 tier 失败→记录 failed_pages） | 未明确 | 必须验证 `failed_pages` dict 内容 |
| `registry.record()` 被正确调用（成功/失败各一次） | 未明确 | 验证 Orchestrator 与 Registry 的交互契约 |
| 预算耗尽中断 | 未明确 | Budget 未定义，无法设计 |
| 页码连续性、空 PDF、全失败 PDF | 未明确 | 从 `test_vlm.py` 继承此类边界 |

---

### 2. 编排层测试难点分析

#### 2.1 引擎注册探测逻辑的 mock 策略

`probe_engines()` 需检测 9 个引擎各自的条件：

| 引擎 | 探测条件 | mock 方式 | 已有先例 |
|------|---------|----------|---------|
| mock | 无（始终可用） | 无需 mock | — |
| paddleocr/rapidocr/mineru/unirec | BookPipeline 路径存在 | `patch("pathlib.Path.exists")` | `test_real_engine.py` |
| sensenova | API key + 网络可达 | `patch.dict(os.environ, ...)` + `patch("urllib.request.urlopen")` | `test_khub_client.py` |
| paddleocr_vl16 | 端口 18080 可达 | `patch.dict(ProbeResult.ports, ...)` | `test_types.py` ProbeResult |
| shizhengpt | 模型文件存在 | `patch("pathlib.Path.exists")` | `test_real_engine.py` |
| kimi_pipeline | 引擎目录存在 | `patch("sys.modules", ...)` | `test_real_engine.py` `mock_real_env` |

**建议**：提取共享 fixture 到 `conftest.py`，避免 4 个测试文件重复构造相同的 9 引擎 mock 环境。

```python
# conftest.py （建议新增）
@pytest.fixture
def mock_all_engines_available():
    """patch 所有 9 引擎的探测条件，返回全部可用的 EngineRegistration 列表"""
    ...

@pytest.fixture
def mock_only_tier1_engines():
    """只有本地 OCR 可用，VLM/LLM 全部不可用"""
    ...
```

#### 2.2 字形验证器的规则 fixture 构造

每条验证规则需要特定的 OCR 输出作为输入：

| 规则 | 输入要求 | fixture 构造方式 |
|------|---------|----------------|
| D4 字符数尖峰 → UNCERTAIN | 某页字符数 > 邻页中位数 × 3 | 直接构造 pages_text 列表，复用 `test_hierarchy.py:TestCheckHierarchyAnomaly.test_detects_oversize_page` |
| C1 跨页泄漏 → FAIL | page_b 开头内容出现在 page_a 中后部 | 复用 `test_leakage.py:TestLeakageDetector.test_detect_leak` |
| 知识库匹配 → PASS | 返回的 OCR 文本命中 `rare_allowlist` 术语 | 构造一个只有 1 条 allowlist 的 `ResourceStore` 实例 |
| 混淆集 → UNKNOWN | OCR 文本命中 `confusion_set` 错字模式 | 同上，构造只有 1 条 confusion_set 的实例 |

**核心问题**：`ResourceStore` 涉及 JSON 文件 I/O，测试中应注入 mock 而非依赖种子文件。
现有 `test_resources.py` 对 `ResourceStore` 有完整测试（20+ 用例），verifier 层面应该 mock `ResourceStore` 而非实际加载。

---

### 3. 调度器确定性测试方案

加权公式：`score = glyph_pass_rate × (1 / avg_latency)`

**确定性挑战**：`avg_latency_per_page_ms` 和 `glyph_pass_rate` 都是浮点数，`1/latency` 导致非线性的排序差异。

**推荐方案**：

```python
# 方案：构造可精确预测排序的 EngineStats 数据

def test_select_candidates_sorts_by_score():
    """EngineA(pass_rate=0.9, latency=100) > EngineB(pass_rate=0.8, latency=200)"""
    registry = MockRegistry()
    registry.register(engine_a, EngineStats(
        total_calls=100, total_latency_ms=10000, total_pages=100,
        glyph_pass_count=90, glyph_fail_count=10,  # pass_rate=0.9
        avg_latency_per_page_ms=100.0,
    ))
    registry.register(engine_b, EngineStats(
        total_calls=100, total_latency_ms=20000, total_pages=100,
        glyph_pass_count=80, glyph_fail_count=20,  # pass_rate=0.8
        avg_latency_per_page_ms=200.0,
    ))
    # engine_a score = 0.9 × 0.01 = 0.009
    # engine_b score = 0.8 × 0.005 = 0.004
    candidates = scheduler.select_candidates(registry, tier=1, ...)
    assert candidates[0].meta.name == "engine_a"
```

**关键约束**：
- 所有 `EngineStats` 通过 fixture 直接构造，不走 `record()` 方法（避免计算逻辑干扰）
- 使用 `pytest.approx()` 或固定 decimal 断言，避免浮点误差
- **必须覆盖** 3 种边界：stats 全为 0（无历史数据）、只有 pass 无 fail、只有 fail 无 pass

---

### 4. Orchestrator 三级兜底全路径覆盖

现状方案中的伪代码展示了一个三层嵌套循环：

```
外层: for page in pages
  内层: for tier in [1, 2, 3]
    最内: for engine in candidates
```

全路径覆盖需要构造的组合：

| # | Tier 1 | Tier 2 | Tier 3 | 预期路径 | 验证点 |
|---|--------|--------|--------|---------|-------|
| 1 | 第 1 引擎 PASS | — | — | 跳过 T2/T3，继续下一页 | `pages_text` 追加，T2/T3 引擎未调用 |
| 2 | 第 1 引擎 FAIL → 第 2 引擎 PASS | — | — | T1 内兜底成功 | 引擎调用顺序，record(success) 调用 |
| 3 | 全部 FAIL | 第 1 引擎 PASS | — | T1→T2 降级 | T2 引擎被调用，`failed_pages` 无此页 |
| 4 | 全部 FAIL | 全部 FAIL | 第 1 引擎 PASS | T1→T2→T3 降级 | T3 被调用 |
| 5 | 全部 FAIL | 全部 FAIL | 全部 FAIL | HumanGate | `failed_pages[page_num]` 含原因 |
| 6 | 第 1 引擎 UNKNOWN | — | — | UNKNOWN = 不通过 | 同 FAIL 行为 |
| 7 | 第 1 引擎 RARE | — | — | RARE = 通过 | 同 PASS 行为 |
| 8 | 预算耗尽（第 2 页中断） | — | — | 提前返回 BookResult | `failed_pages` 含未处理页 |

**这里有一个方案未提及的语义**：`orchestrate_book` 是书级编排（`orchestrate_book`），但每个引擎的 `run()` 在方案伪代码中是页级。这意味着 Orchestrator 需要管理页→引擎的分派。如果某引擎是书级（如 `kimi_pipeline`，`kind="book"`），则不能逐页调用。这一差异在测试中必须覆盖。

**建议**：使用 `pytest.mark.parametrize` 为上述 8 种路径编写参数化测试，每个参数化元组包含 mock 引擎列表 + 预期调用序列。

---

### 5. 现有 260 测试的回归保障

方案将 `run_engine()` 改为调用 Orchestrator，直接影响：

**受影响文件**：

| 测试文件 | 影响程度 | 应对策略 |
|---------|---------|---------|
| `test_real_engine.py` (4 routing + 5 internal tests) | **高** | 路由测试的 mock 需从 `_run_real`/`_run_vlm` 改为 mock `orchestrate_book()`；或者更稳健：在 `orchestrate_book()` 内部调用 `_run_real`/`_run_vlm`，则路由测试基本可保留 |
| `test_vlm.py` (3 routing + 8 D2 + 8 D3 tests) | **高** | 同上 |
| `test_pipeline.py` (5 tests) | **低** | 调用 `mock_book_result`，不涉及引擎路由 |
| `test_cli.py` (15 tests) | **低** | CLI 端 mock `run_engine`，路由变化不影响 |
| 其余 12 个文件 | **无** | 测试独立模块，不调用 `run_engine` |

**回归策略建议**：

1. **保留旧路由测试作为集成门禁**：在 `test_orchestrator.py` 中增加一个"兼容层测试类"，验证 `run_engine("test.pdf", config=cfg)` 在 use_mock/use_vlm/require_real 各模式下路由行为不变
2. **`run_engine()` 内部委派而非替换**：保持 `run_engine()` 入口签名的路由逻辑，只是路由目标从 `_run_real`/`_run_vlm` 改为 `orchestrate_book()`。这样现有 mock `_run_real`/`_run_vlm` 的测试可保留
3. **依赖注入降级**：在 Config 中加一个 `enable_scheduler: bool = True` 开关，设为 False 时回归旧路径，方便 CI 逐步切换

更推荐方案 2——不改 `run_engine()` 入口，只在内部委派：

```python
def run_engine(...):
    registry = probe_engines(config)  # 新逻辑
    scheduler = EngineScheduler()
    return orchestrate_book(...)
```

---

### 6. Benchmark 持久化测试策略

方案中 benchmark 持久化 **只有"记录"两个字，未定存储方案**。不同方案测试策略完全不同：

| 存储方案 | 测试策略 | 复杂度 |
|---------|---------|-------|
| SQLite（复用 `RateLimitStore` 模式） | 参考 `test_ratelimit.py:TestRateLimitStore` 的 save/load/persistence 模式 | 中 |
| JSON 文件 | 参考 `test_atomic.py` 的原子写入 + `test_resources.py` 的 JSON 有效性 | 低 |
| 内存 dict（不持久化） | 简单但失去持久价值 | 极低 |
| 云存储/Redis | 需要 mock 网络层 | 高 |

**无论选择哪种方案，以下测试必加**：

- `save_benchmark` / `load_benchmark` 的基本 roundtrip
- 并发写入的数据竞争保护
- 数据损坏容错（corrupt file → 优雅降级而非崩溃）
- 多 book_code 隔离（各引擎 benchmark 不混淆）
- 空 benchmark（首次运行）→ 使用默认值/跳过评分

---

### 7. 测试缺口 — 12 项遗漏

#### 7.1 致命缺口（必须修复）

1. **集成测试层缺失** — 4 个新文件全是单元测试，mock 彼此隔离。至少需要一个集成测试验证：
   ```
   probe_engines() → EngineRegistry → EngineScheduler.select_candidates() → GlyphVerifier.check()
   ```
   的完整链路。建议在 `test_orchestrator.py` 中增加 `test_orchestrate_book_full_pipeline()`，使用最小 mock（只 mock 引擎实际调用，不 mock 注册和调度）。

2. **缺少 run_engine() 迁移兼容测试** — 没有测试验证 `run_engine()` 重构后旧行为不变。

3. **并发安全测试缺失** — 方案提到多引擎并行（Tier 1 默认 2 并发），但 `EngineStats` 的 `record()` 方法如果被并发调用，stats 计算会出错。需要增加 `test_orchestrator.py` 中的并发测试。

#### 7.2 重要遗漏

4. **Config 新增字段测试** — 方案提到 `KZOCR_MAX_TIER1_ENGINES`, `KZOCR_BENCHMARK_DIR` 等新配置项，应在 `test_config.py` 中补充默认值与 env override 测试（参照现有 `TestConfigDefaults`/`TestConfigFromEnv` 模式）。

5. **调度层异常测试** — 方案提到新增 `SchedulerError`, `AllEnginesFailedError`，应在 `test_errors.py` 中补充异常继承链和构造测试（参照现有 `TestExceptionHierarchy` 模式）。

6. **Empty registry / no engines available** — 所有 4 个组件在"候选为零"时的行为：
   - Registry: `probe_engines()` 返回 `[]` → Orchestrator 是否继续？
   - Scheduler: 空注册表 → `select_candidates()` 返回 `[]` → 不抛异常
   - Orchestrator: 所有 tier 候选都为空 → 直接 HumanGate

7. **BMF fixture 污染** — `EngineStats` 的计算方法 `avg_latency_per_page_ms = total_latency_ms / total_pages` 如果 `total_pages=0` 会除零。需要在注册或 record 时处理并测试。

8. **mock 引擎在编排层中的行为** — `mock` 引擎目前返回固定的 `BookResult`。在编排层中 mock 是当做普通引擎注册还是特殊处理？如果 mock 在注册表中，它的探测条件是什么？需要明确。

9. **Tier 定义的确定性测试** — Tier 定义是固定的（OCR/云端 VLM/本地 LLM），但方案未提供 Tier 过滤函数的单元测试。scheduler 的 tier 过滤应独立于 select_candidates 可测。

10. **probe_engines() 的 fallback 行为** — 探测过程中某引擎 import 失败（如 `tcm_ocr.pipeline.book_pipeline` 不存在）不应使整个探测崩溃。需要测试"部分探测失败"场景。

11. **CLI benchmark 子命令** — 方案提到新增 `kzocr benchmark` 子命令，但 `test_cli.py` 中未提及。需要增加参数解析和命令函数的测试。

12. **conftest.py 未考虑** — 4 个新测试文件和 1 个修改后的 `run_engine()` 共需要 5 组 mock fixture（9 引擎注册、多 tier 候选集、字形验证数据、budget 配置、历史 benchmark）。建议提取到 `tests/conftest.py`，避免重复。

---

## 测试阻塞项

| # | 阻塞项 | 影响 | 需要设计先行确认 |
|---|-------|------|----------------|
| 1 | **benchmark 存储方案未定** | benchmark 持久化、scheduler 加载历史数据、CLI benchmark 子命令的测试全部阻塞 | `kzocr/scheduler/registry.py` 中 benchmark IO API |
| 2 | **Budget 数据结构未定义** | orchestrator 的预算检查、scheduler 的预算过滤无法测试 | `Config` 中预算相关字段设计 |
| 3 | **`orchestrate_book()` vs `run_engine()` 的关系未定** | 所有回归测试策略取决于此 | 方案文档应明确：是委派还是替换？ |
| 4 | **多引擎并发执行方案未提及** | 并发测试设计（threading/asyncio/进程池？数据竞争保护？） | 并发实现方案 |
| 5 | **`glyph_pass_rate` 的 UNKNOWN 是否计入分母** | 方案 `EngineStats` 注释 `pass / (pass + fail + unknown)` 已排除 UNKNOWN？还是公式不同？ | 评分公式正式定义 |

---

## 建议

### 优先级 P0（实施前必须解决）

1. 在方案中明确 benchmark 持久化方案（推荐 SQLite，复用 `RateLimitStore` 模式，已有现成测试模式可参考）
2. 在方案中明确 Budget 数据结构（至少含 `max_wall_clock_ms`、`max_tokens`、`max_pages`）
3. 在方案中明确 `orchestrate_book()` 对 `run_engine()` 的侵入方式（委派优先）
4. 在方案中补充 Tier 过滤函数的单元测试设计

### 优先级 P1（测试实施中必须包含）

5. 在 `test_orchestrator.py` 中增加集成测试（最小 mock 的完整链路）
6. 在 `tests/conftest.py` 中提取 3 个共享 fixture：`mock_all_engines`, `mock_tier1_only`, `sample_glyph_rules`
7. 调度器测试使用 `pytest.approx` + 构造已知排序的 EngineStats fixture
8. Orchestrator 覆盖 8 种兜底路径（参数化），重点验证引擎调用序列和 `registry.record()` 调用次数

### 优先级 P2（边界资产）

9. 补充 `test_config.py` 中调度配置字段的默认值和 env override 测试
10. 补充 `test_errors.py` 中调度层异常的继承链验证
11. 在 `test_real_engine.py` 和 `test_vlm.py` 中各增加一条"路由逻辑不变"的回归测试

---

## 实施优先级

```
P0（方案修订）────→ P1（测试实施）──────→ P2（补充资产）
     │                     │                      │
     ├─ 存储方案             ├─ conftest.py          ├─ test_config.py 调度字段
     ├─ Budget 设计          ├─ 集成测试              ├─ test_errors.py 异常继承
     ├─ run_engine 委派      ├─ 调度器确定性测试        ├─ 回归门禁
     └─ Tier 过滤单元         ├─ Orchestrator 全路径    └─ CLI benchmark
                              ├─ benchmark roundtrip
                              └─ 回归兼容层
```

---

## 总结

v0.7 编排层的测试方案具备正确的模块化思路，但方案文档本身对测试策略的描述过于简略，仅列出了 4 个新增文件名，未涉及具体的 mock 策略、fixture 设计、边界覆盖、集成测试和回归保障——**这些都必须在实施前补充到方案中，否则测试实施会严重偏离目标**。

**最大的风险点**是 `run_engine()` 重构对 260 个现有测试的波及范围，建议使用"委派模式"（`run_engine()` 内部调用 `orchestrate_book()`，保留路由逻辑）来最小化影响。

**最大的测试设计难点**是调度器加权排序的确定性测试，但只要使用直接构造 `EngineStats` fixture（不走 record 累加计算），配合 `pytest.approx`，可以稳定控制。

`conftest.py` 的引入时机已经成熟——17 个现有测试没有共享 fixture，v0.7 新增的 4 个测试文件都有强烈的 fixture 复用需求（9 引擎 mock 环境、字形验证规则数据、调度候选人列表），不应再各自重复构造。
