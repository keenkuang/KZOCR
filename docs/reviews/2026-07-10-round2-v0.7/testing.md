# v0.7 自适应 OCR 引擎编排层 — 第二轮测试评审报告

> 评审人：测试工程师
> 评审对象：`docs/plans/ocr-engine-unification.v0.7.md`（修订版）
> 基准：第一轮评审 `docs/reviews/2026-07-10-round1-v0.7/testing.md`
> 本轮复查焦点：第一轮识别的 3 个致命缺口
> 日期：2026-07-10

---

## 总体判断

**方案修订版已明确填补了第一轮 3 个致命缺口的其中 2 个（回归迁移策略、benchmark 存储方案），但第 1 个（集成测试层）的修复幅度不够。** 此外，修订版引入的新内容在第 1 轮未覆盖的范围内暴露出 2 个新的测试缺口。

第一轮致命缺口修复状态：

| 致命缺口 | 第一轮评级 | 修订版对应 | 修复状态 |
|---------|-----------|-----------|---------|
| 1. 集成测试层缺失 | 致命 | §10.1 `test_orchestrator_integration.py`，§8 Phase 3.6 | ⚠️ **部分修复** |
| 2. 缺回归迁移策略 | 致命 | §7.2 委派模式 + §10.2 回归策略 + §8 Phase 3.3 | ✅ **已修复** |
| 3. benchmark 存储方案未定 | 致命 | §7.1 NDJSON 追加式持久化 | ✅ **已修复** |

---

## 一、致命缺口复查（原 #1）：集成测试层

### 1.1 修订版中的变更

方案 §8 Phase 3.6 新增了 `test_orchestrator_integration.py`，定位为「集成测试」，覆盖 8 种兜底路径（T1 PASS → T1 FAIL+T2 PASS → 全部 FAIL → HumanGate → RARE → UNKNOWN → 预算耗尽 → 空注册表），使用参数化。

### 1.2 评估：部分修复

**已解决的问题：**

- Orchestrator 主循环的兜底路径覆盖已从 0 提升到 8 种，范围合理
- `pytest.mark.parametrize` 方式可以使用例可维护

**未解决的问题（集成测试的范围仍然过窄）：**

修订版中的 `test_orchestrator_integration.py` 本质上是 **Orchestrator 级别的大单元测试**，而非真正的集成测试。

```
修订版定义的边界：
  mock_registry(mock_engine_list) → Orchestrator.orchestrate_book() → assert pages_text/failed_pages

仍然缺失的边界（真正需要集成的链路）：
  probe_engines()(真实调用) → EngineRegistry(真实) → EngineScheduler.select_candidates()(真实)
  → GlyphVerifier.verify()(真实) → registry.record()(真实)
```

具体来说：

1. **注册→调度集成被截断** — `test_orchestrator_integration.py` 的测试构造的是**已经组好的引擎列表**传给 Orchestrator，从不经过 `probe_engines()` → `select_candidates()` 的真实过滤链。这意味着调度器的排序逻辑、衰减因子、预算检查等**在集成测试中从未被验证**。

2. **验证→编排集成被截断** — Orchestrator 内部的 `verifier.verify()` 调用在集成测试中被 mock 返回值控制（测试的是 "如果 verifier 返回 X，Orchestrator 怎么做"），从不验证 "如果 OCR 文本是 Y，verifier 实际返回什么，Orchestrator 怎么据此决策"。

3. **持久化→加载集成被截断** — 每本书完成后 `registry.persist_benchmarks()` 在集成测试中被完全忽略，从不验证 "写完 NDJSON → 下次启动时 load → 调度器评分是否与预期一致" 的完整闭环。

**建议补充：**

在已有 8 路径参数化测试之外，增加一个 **最小 mock 集成测试**（mock 只到网络/GPU/文件系统这层，不 mock 注册/调度/验证的内部逻辑）：

```python
def test_orchestrate_book_minimal_mock():
    """最小 mock 集成测试：只 mock 引擎实际调用和探测条件，不 mock 注册/调度/验证。"""
    # Arrange
    # 只 patch 文件系统(模型路径存在) 和 网络(端口可达)，让 probe_engines 走真实逻辑
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch.dict(os.environ, {"SENSENOVA_API_KEY": "sk-test"}),
        patch.object(ProbeResult, "ports", {"paddleocr_vl16": 18080}),
    ):
        config = KzocrConfig(scheduler=SchedulerConfig(
            max_pages=3,
            total_timeout_s=300,
            benchmark_dir=tmp_benchmark,  # 真实临时目录
        ))
        # 只 patch 引擎的执行（不 patch 注册和调度）
        with patch("kzocr.scheduler.orchestrator._run_book_engine",
                   return_value=mock_book_result):
            result = orchestrate_book(TEST_PDF, "test_book", config)
    
    # Assert
    # 验证全链路真实走通
    assert result.failed_pages == {}  # 引擎可被正确选中和执行
    # 验证 benchmark 确实写入
    assert len(list(Path(tmp_benchmark).glob("*.ndjson"))) > 0
```

### 1.3 新增建议：集成测试检查清单

| 集成场景 | 当前 test_orchestrator_integration 覆盖 | 需要补充 |
|---------|---------------------------------------|---------|
| probe→registry 真实流程 | ❌ mock 引擎列表直接传入 | `test_probe_to_registry_integration` |
| scheduler 排序→orchestrator 选择 | ❌ mock select_candidates 返回值 | `test_scheduler_to_orchestrator_integration` |
| verifier 真实判断→orchestrator 降级 | ❌ mock verdict 返回值 | `test_verifier_to_orchestrator_integration` |
| persist_benchmarks→下次加载评分 | ❌ 未覆盖 | `test_benchmark_persistence_across_sessions` |
| 引擎真实异常（network error/TimeoutError）→scheduler 标记 UNAVAILABLE | ❌ mock 异常 | `test_real_engine_failure_propagation` |

---

## 二、致命缺口复查（原 #2）：回归迁移策略

### 2.1 修订版中的变更

方案 §7.2 明确使用了**委派模式**：

```python
def run_engine(pdf_path, book_code, config) -> BookResult:
    if config.use_mock:
        return build_mock_book(...)
    if config.use_vlm:
        config_overrides = SchedulerConfig(disabled_tiers=[1])
        return orchestrate_book(pdf_path, book_code, config, config_overrides)
    return orchestrate_book(pdf_path, book_code, config)
```

同时 §10.2 明确了 4 点回归策略，§8 Phase 3.3 安排了委派模式改造步骤，§10.1 新增 `test_regression.py`（≥5 用例）。

### 2.2 评估：已修复

**设计合理之处：**

- `run_engine()` 保持入口签名不变，migration test 只需验证路由目标变更而非 API 变更
- `use_mock` 短路优先，确保 mock 模式不受影响
- `use_vlm` 映射为 `disabled_tiers=[1]`，语义清晰

**建议补充（非阻塞，但提升信心）：**

1. **Phase 1-2 过渡期保护** — 方案说「Phase 1-2 期间现有测试不受影响」，但这依赖于 `run_engine()` 在 Phase 1-2 期间**仍然走旧路径**，直到 Phase 3 才切入委派。建议在 Phase 1 开始前，先在 CI 中运行一次全量 `pytest tests/` 留存基线通过率（report 文件存档），Phase 3 再对照。

2. **Config 降级开关（可选）** — 第一轮建议的 `enable_scheduler: bool = True` 开关虽然不需要默认启用，但建议在 Config 中预留一个隐藏的 `_legacy_routing: bool = False` 字段，仅用于紧急降级。

3. **`test_regression.py` 的 5 个用例建议分配：**

| # | 测试 | 验证点 |
|---|------|-------|
| 1 | `test_run_engine_mock_shortcut` | `use_mock=True` → 不调用 `orchestrate_book()` |
| 2 | `test_run_engine_vlm_maps_to_disabled_tier1` | `use_vlm=True` → `SchedulerConfig.disabled_tiers=[1]` 被传入 |
| 3 | `test_run_engine_default_dispatches_to_orchestrator` | 默认路径 → `orchestrate_book` 被调用 |
| 4 | `test_run_engine_old_config_compat` | 未设置 `SchedulerConfig` 时仍能工作 |
| 5 | `test_run_engine_require_real_preserved` | `require_real=True` 行为不变 |

---

## 三、致命缺口复查（原 #3）：Benchmark 存储方案

### 3.1 修订版中的变更

方案 §7.1 明确了 NDJSON 追加式持久化：

- 目录：`$KZOCR_OUTPUT_DIR/benchmarks/`
- 格式：每行独立 JSON 事件
- 进程内 `EngineStats` 实时更新，每本书完成后批量 flush
- 进程启动时从 benchmark 目录加载重建 `EngineStats`
- 复用 `kzocr/engines/atomic.py` 的原子写入

### 3.2 评估：已修复

**设计合理之处：**

- NDJSON 行级追加 O(1) 写入，避免 JSON 全文覆写的 O(n²) 退化
- 复用已有的 `atomic.py` 原子写入，避免并发问题
- 进程启动时从 NDJSON 重建 `EngineStats™`，无须独立的状态文件

**尚需确认的测试设计问题：**

1. **NDJSON 文件锁** — 如果两个进程（如并行处理两本书）同时写入同一 NDJSON 文件，即使使用 `atomic.py` 原子写入，也存在**覆盖风险**（两进程同时 flush，后完成的覆盖先完成的）。建议：
   - 每本书写入独立文件（如 `benchmarks_{book_code}_{session_id}.ndjson`），进程启动时汇总读取
   - 或在文件级别加 `fcntl.flock()`（跨进程锁）
   - 对应的测试：`test_concurrent_benchmark_writes` 验证并行写入不丢数据

2. **NDJSON 加载性能** — 如果 benchmark 积累到数万行，启动时逐行加载 + 重建 `EngineStats` 的性能是否在可接受范围内？建议在测试中增加：
   - `test_load_10k_benchmarks_under_100ms`（性能门禁）
   - 或当文件 > 100MB 时启动「抽样加载」策略

3. **数据格式演进** — NDJSON 是扁平 key-value，如果未来需要增加字段（如 `model_version`），旧行缺少该字段。`load_benchmarks()` 是否需要向前兼容？建议测试：
   - `test_load_benchmark_with_missing_fields` 验证 `dict.get(field, default)` 容错

### 3.3 Benchmark 测试检查清单

| 测试场景 | 方案提及 | 优先度 |
|---------|---------|-------|
| save/load roundtrip（空→写入→读出→追加） | §8 Phase 1.7 | P0 |
| 进程启动时重建 EngineStats | §7.1 | P0 |
| 数据损坏 → 优雅降级 | 第一轮 R1 建议 | P1 |
| 多 book_code 隔离 | 未明确 | P1 |
| 并发写入（进程级） | 未明确 | P1 |
| 10K+ 行加载性能门禁 | 未明确 | P2 |
| 旧版 NDJSON 格式向前兼容 | 未明确 | P2 |

---

## 四、修订版新内容的测试评审

修订版增加了若干第一轮不存在的新设计，这些内容在第一轮评审中未被覆盖。

### 4.1 EngineRunner 协议层（§2.2）— 测试关注

新增 `EngineRunner` Protocol（`kzocr/engine/types.py`），定义 `run_page()` / `run_book()` 接口。

**测试要求：**

| 场景 | 说明 |
|------|------|
| 结构性子类型检查 | 所有 9 个引擎是否满足 `EngineRunner` 协议？`pytest` 中需用 `@runtime_checkable` + `isinstance` 验证 |
| `kind="book"` 引擎是否不支持 `run_page()` | `run_book` 引擎调用 `run_page` 应抛 `NotImplementedError` 或 `TypeError` |
| 参数类型校验 | `PageInput` → `AdapterPageResult` 的契约测试 |

**问题：** 方案说「所有 page-level 引擎实现此方法」，但未说明现有的 9 个引擎中哪些需要做适配改动。测试团队需要明确的引擎→协议实现映射表。

### 4.2 EngineScheduler 领域感知权重（§4.4）— 测试关注

新增 `domain_adjust()` 函数有 4 条规则：

| 规则 | 测试用例 |
|------|---------|
| 竖排对 Tier 1 降权 0.3× | `test_domain_adjust_vertical_t1_lowered` |
| 雕版印刷对 Tier≥2 提权 1.5× | `test_domain_adjust_lead_print_t2_boosted` |
| 表格页对 Tier 1 降权 0.5× | `test_domain_adjust_table_t1_lowered` |
| 冷启动降权 0.8× | `test_domain_adjust_cold_start_penalty` |

**权重值的测试合理性：** 这些权重的选择依据是什么？如 `0.3 × 0.5 × 0.8 = 0.12`（竖排+表格+冷启动，Tier 1 引擎得分降低到 12%），是否经过实际数据集验证？建议在 `test_scheduler.py` 中增加「极端组合」场景测试。

### 4.3 衰减因子（§4.2）— 测试关注

`decay(last_seen, half_life_days=7.0)` 的测试需要：

| 场景 | 用例 |
|------|-----|
| last_seen = now → decay = 1.0 | `test_decay_zero_elapsed` |
| last_seen = now - 7 天 → decay ≈ 0.5 | `test_decay_half_life` |
| last_seen = now - 30 天 → decay ≈ 0.05 | `test_decay_long_elapsed` |
| 轮询采样数据不参与衰减 | `test_polling_data_not_decayed` |

**注意：** `time.time()` 在测试中不可控（慢 1ms 可能导致断言失败），必须 patch `time.time`：

```python
def test_decay_half_life():
    frozen_now = 1000000.0
    with patch("time.time", return_value=frozen_now):
        last_seen = frozen_now - 7 * 86400  # 7 天前
        assert decay(last_seen) == pytest.approx(0.5, rel=0.001)
```

### 4.4 B3 egress 校验（§4.5）— 测试关注

`validate_url()` 在 Orchestrator 中 Tier 2 引擎调用前执行：

| 场景 | 说明 |
|------|------|
| allowlist 内的 base_url → 通过 | 正常流程 |
| allowlist 外的 base_url → 抛 EgressBlockedError | 异常路径 |
| base_url 为空 → 通过/阻断？ | 边界：本地引擎的 base_url 可能为空 |
| orchestrate 中 egress 失败 → tier 2 跳过，继续 tier 3 | 兜底路径 #9（新增） |

**注意：** egress 校验失败不应终止全书，应只跳过该引擎，继续下一引擎/下一 tier。当前方案中的伪代码 `validate_url()` 若失败抛异常会中整个 for 循环，需要修改为 `try-except-continue`。对应的集成测试需新增「校验失败 → 继续下一引擎」路径。

### 4.5 EngineOverrides CLI 覆盖（§4.5）— 测试关注

新增 CLI 参数强制覆盖调度器：

| 参数 | 测试关注点 |
|------|-----------|
| `--engine sensenova` | 绕过所有调度逻辑，强制 pin 引擎 |
| `--prefer speed/accuracy` | 排序基准切换而非更换引擎 |
| `--tier-order "1,3,2"` | 非标准 tier 顺序 |
| `--tier-limit 2` | 限制最大 tier 层数 |
| `--max-time-per-page 120` | 单页超时覆盖 |

**关键测试风险：** `--engine` 强制 pin 时，如果指定引擎不可用（UNAVAILABLE），Orchestrator 的行为是什么？

- 方案伪代码中 `if overrides.pinned_engine: return [registry.get(name)]`
- 如果 `registry.get(name)` 返回 None（引擎不存在）或 UNAVAILABLE，应该怎么办？
- 建议：pinned 引擎不可用 → 抛明确的 `PinnedEngineUnavailableError(engine_name)`，不要静默回退到自动调度

### 4.6 ToxinDoseDetector（§5.3）— 新增测试关注

正则匹配 `{herb_name}\s*(\d+)\s*g` 的健壮性需要测试：

| 输入 | 预期 |
|------|------|
| `"附子 10g"` | FAIL (dosage 10 > max) |
| `"附子10g"` | FAIL (无空格) |
| `"附子 10 g"` | FAIL (空格在 g 前) |
| `"附子 10.5g"` | 是否处理浮点剂量？ |
| `"附子汤 10g"` | 混淆：`附子汤` ≠ `附子`，不应匹配 |
| `"炮附子 6g"` | 是否算另一种药材？ |
| 剂量在安全阈值内 → None | 不触发 |
| 剂量 = 边界值（等于 max_dosage_g）→ None | 等于不算超过 |

**问题：** 正则 `{herb_name}` 直接拼接有毒药名到 pattern 中，如果药名包含正则特殊字符（如 `+`、`.`、`(`），会导致 pattern 编译错误或意料之外的匹配。建议使用 `re.escape(herb)`。

### 4.7 `Conftest.py` 共享 fixture 检查（§10.3）

修订版中 `conftest.py` 的 fixture 定义与第一轮建议基本一致。补充建议：

| Fixture | 说明 |
|--------|------|
| `mock_all_engines_available` | ✓ 已在方案 |
| `mock_only_tier1_engines` | ✓ 已在方案 |
| `sample_engine_stats` | ✓ 已在方案 |
| **`sample_domain_adjust_fixtures`** | 新增：提供 `PageInfo` / `BookInfo` 组合，复用给 `test_scheduler.py` 的 `domain_adjust` 测试 |
| **`frozen_time`** | 新增：patch `time.time()` 固定时间，用于衰减测试 |
| **`tmp_benchmark_dir`** | 新增：`tmp_path` 子目录 + 清理，用于 benchmark 持久化 roundtrip 测试 |

---

## 五、综合测试缺口追踪矩阵

以下矩阵将第一轮识别的 12 项遗漏（7.1 致命 3 项 + 7.2 重要 9 项）映射到修订版中的处理状态，并补充本轮新增的缺口：

| # | 第一轮缺口 | 修订版状态 | 本轮评级 |
|---|-----------|-----------|---------|
| 1 | 集成测试层（致命） | ⚠️ 新增 `test_orchestrator_integration.py` 但仅覆盖 Orchestrator 级，未覆盖 probe→registry→scheduler→verifier 完整链路 | **关键（仍需补充）** |
| 2 | 回归迁移策略（致命） | ✅ 明确委派模式 + `test_regression.py` | **已关闭** |
| 3 | 并发安全测试（致命） | ❌ 未提及 | **关键（本轮新增）** |
| 4 | Config 新增字段测试 | ❌ 未在方案中提及 | 重要 |
| 5 | 调度层异常测试 | ❌ 未在方案中提及 | 重要 |
| 6 | Empty registry 行为 | ⚠️ 8 兜底路径含"空注册表"，但无组件级测试 | 一般 |
| 7 | BMF fixture 除零 | ❌ 未提及 | 一般（方案中 `EngineStats` 的派生字段在访问时计算，如果处理 `total_pages=0` 已隐含解决，但需确认） |
| 8 | mock 引擎在编排层行为 | ❌ 未提及 | 一般 |
| 9 | Tier 过滤单元测试 | ❌ 未提及 | 重要 |
| 10 | probe_engines fallback | ❌ 未提及 | 重要 |
| 11 | CLI benchmark 子命令 | ⚠️ §8 Phase 3.4 提到 CLI 扩展但未提测试 | 重要 |
| 12 | conftest.py fixture 复用 | ✅ §10.3 列了 3 个共享 fixture | **已关闭** |

**本轮新增缺口：**

| # | 新增缺口 | 说明 | 评级 |
|---|---------|------|-----|
| 13 | 并发安全（记录重复） | 未提及并发的 `EngineStats.record()` 线程安全，如果 KZOCR_ENGINE_PARALLEL=1 时 | **关键** |
| 14 | EngineRunner 协议契约测试 | §2.2 新增协议层，但未提及各引擎的协议实现适配测试 | 重要 |
| 15 | domain_adjust 权重组合测试 | 4 条调权规则的组合效果未验证（如竖排+表格+冷启动 = 0.12×） | 一般 |
| 16 | decay 函数测试依赖 time.time | 需 patch time.time 才能确定性测试，但方案中未提及 | 一般 |
| 17 | egress 校验失败不阻断全线 | 当前伪代码缺少 try-except，实际行为需确认并测试 | **关键** |
| 18 | pinned engine 不可用时的行为 | `--engine` 指定不可用引擎应抛明确异常而非静默回退 | 重要 |
| 19 | ToxinDoseDetector 正则边界 | `re.escape` 缺失、浮点剂量、药名子串混淆 | 一般 |
| 20 | NDJSON 并发写入冲突 | 两进程同时 flush 同级 NDJSON 文件的覆盖风险 | 重要 |

---

## 六、阻塞项复查

第一轮识别的 5 个阻塞项：

| # | 阻塞项 | 修订版状态 | 本轮状态 |
|---|-------|-----------|---------|
| 1 | Benchmark 存储方案 | ✅ NDJSON 追加式（§7.1） | **已解除** |
| 2 | Budget 数据结构 | ✅ `Budget` dataclass（§6.3） | **已解除** |
| 3 | orchestrate_book vs run_engine 关系 | ✅ 委派模式（§7.2） | **已解除** |
| 4 | 多引擎并发执行方案 | ⚠️ 明确默认串行、GPU opt-in（§6.4），但未提及 `EngineStats.record()` 线程安全 | **部分解除** |
| 5 | glyph_pass_rate 公式 | ✅ 方案使用贝叶斯平均公式（§3.5）：`(pass_rate_avg × n + C × prior) / (n + C) × (1 / latency_avg)` | **已解除** |

**新增阻塞项：**

| # | 阻塞项 | 影响 | 需要确认 |
|---|-------|------|---------|
| 6 | `EngineStats.record()` 并发保护 | 如果 `KZOCR_ENGINE_PARALLEL=1` 生效，多个线程同时调用 `record()`，stats 累加会出错（非原子操作） | 方案确认 record 的并发策略（锁/原子累加/每个引擎独立实例） |
| 7 | Egress 校验失败的处理语义 | 当前伪代码 `validate_url()` 失败抛异常，会导致整个 tier 层循环中断而非跳过单个引擎 | 方案确认是 try-except-continue 还是抛异常终止 |

---

## 七、推荐行动项（按优先级）

### 实施前必须确认

1. **补充真正的最小 mock 集成测试**（§1.2 建议），覆盖 probe→registry→scheduler→verifier→persist 完整链路，不 mock 中间件内部逻辑
2. **确认 `EngineStats.record()` 的并发安全方案**（新增阻塞项 #6），确定后再设计并发测试
3. **确认 egress 校验失败的处理语义**（新增阻塞项 #7），当前伪代码与合理行为之间有 gap
4. **确认 pinned engine 不可用时的行为**（§4.5）— 抛异常还是回退

### 测试实施中必须包含

5. 在 `test_orchestrator_integration.py` 的 8 路径中增加 egress 校验失败路径（共 9 条）
6. 增加 EngineRunner 协议的结构性子类型检查（`isinstance` check for all 9 engines）
7. 增加衰减因子的 `time.time` patch 确定性测试
8. 补充 `domain_adjust` 的 4 条规则独立测试 + 极端组合测试
9. 增加 NDJSON 并发写入保护和数据损坏容错的持久化测试
10. 在 `test_cli.py` 中新增 `kzocr benchmark` 子命令和 `--engine/--prefer/--tier-order` 参数的测试

### 边界资产

11. ToxinDoseDetector 的正则健壮性测试（子串混淆、浮点、特殊字符 escape）
12. 补充 `conftest.py` 中的 `frozen_time` 和 `tmp_benchmark_dir` fixture
13. 补充 `test_regression.py` 的 5 个迁移兼容测试用例设计（§2.2 建议分配表）
14. Phase 1 开始前留存 `pytest tests/` 基线通过率报告

---

## 八、实施路径

```
第一轮 3 个致命缺口
│
├─ #1 集成测试层 → ⚠️ 部分修复，需补充最小 mock 集成测试（推荐 1）
│
├─ #2 回归迁移策略 → ✅ 已修复
│     ├─ run_engine 委派模式（§7.2） ✓
│     └─ test_regression.py（§10.1） ✓
│
└─ #3 Benchmark 存储 → ✅ 已修复
      ├─ NDJSON 方案明确（§7.1） ✓
      ├─ 并发写入保护 → 需补充测试（推荐 9）
      └─ 加载性能门禁 → 建议补充（推荐 9）

本轮新增关注
│
├─ 并发安全 record() → 需方案确认（阻塞 #6）
├─ egress 校验语义 → 需方案确认（阻塞 #7）
├─ EngineRunner 协议 → 补契约测试（推荐 6）
└─ domain_adjust / decay → 补确定性测试（推荐 7, 8）
```

---

## 总结

修订版 v0.7 方案对测试策略的完善度相比第一轮有了显著提升，**回归迁移策略（`run_engine` 委派 + `test_regression.py`）和 Benchmark 存储方案（NDJSON 追加式）已得到满意的解决**，这两件事可以关闭。

**集成测试层仍是最大风险**——修订版新增的 `test_orchestrator_integration.py` 范围限于 Orchestrator 层面，而真正需要的 "probe_engines() 真实探测 → EngineRegistry 真实注册 → select_candidates() 真实排序 → GlyphVerifier 真实判断 → persist_benchmarks() 真实写入" 的端到端链路仍然缺少测试覆盖。建议在 Phase 3 交付标准中增加一个限制条件：**在最小 mock 环境下，一条完整的编排链路可以实际走通**（mock 只到文件系统和网络层，不 mock 注册/调度/验证的内部逻辑）。

此外，修订版引入的新内容（EngineRunner 协议、domain_adjust 权重、decay 衰减因子、egress 校验、CLI pinning 覆盖）在测试策略中都未被覆盖，需要在实施计划中补充对应的测试用例设计。
