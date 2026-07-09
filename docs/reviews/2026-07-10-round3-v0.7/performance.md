# v0.7 性能评审报告

**评审角色：** 性能工程师
**评审对象：** `docs/plans/ocr-engine-unification.v0.7-DETAILED.md`
**评审日期：** 2026-07-10

---

## 总体评价

设计在性能和资源预算控制方面覆盖了主要路径，但存在若干值得关注的性能隐患和设计偏差。以下是逐点评审。

---

## P1 — §7 B6 双闸实现充分性分析

### 现有实现

| 闸 | 位置 | 检查方式 | 默认值 |
|---|---|---|---|
| 页数上限 | `orchestrate_book()` for 循环入口，第 1107 行 | `page_num >= budget.max_pages` | 50 |
| 总时间预算 | 同循环入口，第 1114 行 | `elapsed > config.total_timeout_s` | 7200s |
| 单页超时 | 仅 Tier 3 引擎调用，第 1191 行 | `_run_single_engine_with_timeout` (ThreadPoolExecutor) | 120s |

### 充分性分析

**充分的部分：**

1. **页数闸（MAX_PAGES）**：在渲染前截断，避免浪费渲染计算。正确。`budget.exhaust()` 随后被调用，Tier 2/3 入口的 `if not budget.exhausted` 可以拦住后续页面。**充分。**

2. **总时间闸（TOTAL_TIMEOUT）**：基于 `time.monotonic()` 的挂钟时间，不易受系统时间跳变影响。每页检查一次，精度足够。**充分。**

3. **单页超时（MAX_TIME_PER_PAGE）**：仅限 Tier 3 本地 LLM。使用 `ThreadPoolExecutor` + `future.result(timeout=timeout_s)` 实现，在超时时抛出 `TimeoutError` 被 `except TimeoutError` 捕获。**对 Tier 3 充分。**

**不充分的部分：**

1. **[性能 P1] Tier 2 缺少单页超时保护。** Tier 2 云端 VLM 引擎的直接调用路径（`_run_page_engine`）无超时包裹。虽然云引擎通常不会挂死，但网络抖动或 API 卡死可能导致单页在 Tier 2 上阻塞数分钟，消耗总时间预算且无日志区分"是超时还是慢"。

   **建议：** 将 `_run_single_engine_with_timeout` 泛化为 Tier 2 也使用，或至少在 Tier 2 调用包装 `concurrent.futures.wait()` + 超时。超时值可以放宽（如 300s），但不应无上限。

2. **[性能 P2] 时间闸检查使用 `config.total_timeout_s` 而非 `budget.max_wall_clock_ms`。** 虽然两者初始化值相同，但语义不一致：`Budget.check_time_budget()` 方法（第 283 行）未被编排循环调用，编排循环重复了同样的逻辑。后续若 `Budget` 和 `Config` 的值不同步，会产生歧义。

   **建议：** 编排循环第 1114 行改为 `if not budget.check_time_budget(elapsed)`，以 `budget` 为唯一权威来源。

3. **[性能 P3] 双闸命中后 `budget.exhaust()` 调用位置正确但缺少**"剩余页立即标记为 HumanGate"**的明确实现。** 第 1109 行 `break` 退出循环后，第 1213 行的 `failed_pages` 只记录已处理的页，未处理的剩余页不会出现在 `pages_text` 或 `failed_pages` 中。调用者收到的 `BookResult` 中 `failed_pages` 可能为空但 `pages` 数量小于实际书页数——调用端需要检查 `pages` 长度来感知截断。

   **建议：** 明确在 `break` 后将剩余页统一写入 `failed_pages`，或新增一个 `truncated` 标志位让调用者能区分"全部处理完但失败"和"被截断"。

---

## P1 — §4 调度器排序计算开销与缓存策略

### 计算成本分解

每次 `select_candidates()` 调用对每个候选引擎执行 `_score()`，其计算链：

```
_score(engine):
  ├─ engine.stats.glyph_pass_rate       → 属性, O(1) 加减乘除
  ├─ engine.stats.avg_latency_per_page_ms → 属性, O(1) 除法
  ├─ engine.stats.decay(now)            → math.exp(-elapsed / half_life_seconds)
  │                                        math.exp 是浮点超越函数，~50-100 cycles
  │                                        time.time() 系统调用，~100ns-1μs
  └─ domain_adjust()                    → 读属性 + 3 次简单比较，O(1)
```

**单次 `_score()` 调用**的总 CPU 成本估计：约 200-500 纳秒（纯计算，不含 GC/解释器开销）。

**调用频率估算（最坏场景）：**

| 场景 | 每页调用次数 | 50 页总量 |
|---|---|---|
| 常规（T1 PASS）每页 | 1 次（T1 排序） | 50 |
| T1 FAIL 需要 T2 | +1 次（T2 排序） | +50 |
| T1/T2 都 FAIL 需要 T3 | +1 次（T3 排序） | +50 |
| **最坏（每页走完三级）** | **3 次/页** | **150 次** |

150 次 `_score()` 调用 × 每次 ~500ns ≈ **75μs**。与单次 VLM 调用（数秒到数十秒）相比，**调度器排序开销可忽略不计**。

### 缓存必要性评估

**结论：不需要缓存。** 原因：

1. 绝对开销微乎其微（全书 < 100μs）。
2. 增加缓存会引入失效逻辑：每次 `registry.record()` 后评分变化，缓存需要精确失效——比重新计算更复杂。
3. 贝叶斯平均和衰减因子的计算是 O(1) 的简单浮点运算，没有复用收益。

### 一条注意

`decay()` 函数每页每个候选引擎调用一次 `time.time()`（系统调用）。在 3tier × 50 页 × 3 引擎 = 450 次 `time.time()` 调用。虽然本身开销极小（~200ns/次），但在高并发场景（如多本书并行）下，可考虑在 `select_candidates()` 入口一次获取 `now` 并传递下去。当前串行模式下完全可忽略。

---

## P2 — §2 冷启动 5% 轮询开销

### 实现分析

```python
def _should_poll(registry: EngineRegistry) -> bool:
    return random.random() < 0.05        # O(1)，极低
```

```python
def _select_poll_candidate(registry, tier, candidates):
    tier_engines = [e for e in registry.get_by_tier(tier)
                    if e not in candidates and e.status != "UNAVAILABLE"]
    if not tier_engines:
        return None
    return random.choice(tier_engines)   # O(n), n ≤ 3
```

### 成本评估

- `random.random()` 单次 ~30ns。
- 列表推导过滤最多遍历 3 个元素。
- 50 页 × 最多 3 次 `select_candidates()`/页 × 5% = 约 7-8 次实际轮询执行。

**全书成本 < 1μs。可忽略。**

### 一个问题

**轮询采样的 `last_seen` 不更新**这一约定（第 391 行）在实现中未体现约束——`_select_poll_candidate` 返回的引擎在 `_run_*_engine` 中执行后，`registry.record()` 会正常更新 `last_seen`。这意味着轮询调用的实际效果会**破坏衰减独立性**。

**建议：** 在 `registry.record()` 中增加一个 `poll: bool = False` 参数；当 `poll=True` 时不更新 `last_seen`。当前设计只靠"约定不更新"而没有强制机制，容易引入隐式 bug。

---

## P2 — §8 NDJSON 启动数据重建的 I/O 开销

### 加载流程性能分析

`load_benchmarks()` 在每次进程启动时执行一次：

```
遍历 benchmark_dir/*.ndjson
  └─ 逐行读取
     ├─ line.strip() 判空
     ├─ json.loads(line)  → 解析整行 JSON
     ├─ if event["ts"] < cutoff: continue  ← 已解析后才判断时间范围
     ├─ lines_loaded += 1
     ├─ if lines_loaded > max_load_lines: break
     └─ 累加 EngineStats
```

### 最坏 I/O 量估算

| 参数 | 值 |
|---|---|
| 引擎数 | 7（所有书级 + 页级引擎） |
| 每引擎最大行数 | 50,000 |
| 保留天数 | 90 |
| 每行平均大小 | ~100-150 字节 |
| 单引擎文件最大 | ~7.5 MB（50K × 150B） |
| **总读取量** | **~52.5 MB**（7 × 7.5MB） |
| JSON 解析次数 | 350,000 次 |

### 性能评估

- **全量解析中有部分数据会被跳过**（超过截止时间和行数限制的行），但跳过发生在解析**之后**。如果 90 天内积累了远超 50K 行（高频使用），前 50K 行之后的行不会读取（第 1417 行 `break` 只中断 `lines_loaded` 计数循环），但**更坏的情况是**：数据以时间正序写入，最近行在文件尾部——`break` 在第 1417 行切断后，最近的 50K 行**未被读取**。

  **→ 这是一个严重的时序 bug。** NDJSON 是追加写入，老数据在前，新数据在后。`lines_loaded++` 从行 1 开始计数，到达 50K 时断在中间位置，舍弃了文件尾部的最新数据。

  **建议：** 改为从文件尾部向前读取（`tail -n 50000` 语义），或先通过 `wc -l` 获取总行数，只解析最后 `max_load_lines` 行。

- 每条 JSON 解析约 1-5μs（CPython json.loads），350K 条 ≈ 350ms-1.75s。**在 52.5MB 读取 + 350K 解析下，启动时间约 1-3 秒**。对 CLI 工具来说可接受，但建议记录一条 `INFO` 日志显示重建耗时和行数，便于运维监控退化。

- `Path(benchmark_dir).glob("*.ndjson")` 对于目录内有大量文件的场景（如运维误创建碎片文件）可能变慢。当前 7 个文件不存在此问题。

---

## P1 — §7 串行 + GPU opt-in 设计分析

### 当前设计

```python
# 第 1064-1066 行
if config.engine_parallel and not probe_result.has_gpu:
    _logger.warning("engine_parallel ignored: no GPU detected")
    config.engine_parallel = False
```

实际编排主循环**全程串行**：

```
T1 全书 → 逐页：T1 verify → T2(逐引擎串行) → T3(逐引擎串行)
```

### 设计正确性

对于 v0.7 的规模（≤50 页，T2/T3 大多 1 个引擎），**串行设计是正确的选择**：

1. **性能上合理**：85%+ 的页面在 T1 直接 PASS，不需要 T2/T3。串行处理 85% 页面的延迟 = 串行处理 100% 页面的延迟。
2. **避免 GPU 争用**：T3 本地 LLM 如 paddleocr_vl16 需要 GPU，并行调用可能导致 OOM 或显存竞争。GPU opt-in 检查和串行执行天然避开了这个坑。
3. **调度器状态一致性**：串行下 `registry.record()` 更新 stats 影响后续 `select_candidates()` 评分——如果在并行多选引擎后统一 record，调度器无法在当页依据实时反馈调整排序。

### 潜在问题

1. **[性能 P2] `engine_parallel` 这个配置项在串行架构下是误导性的。** 当前设计仅在第 1064 行检查 GPU 后设置 `False`，但即使 GPU 存在，编排循环也没有任何并行路径。`engine_parallel` 是一个**无效配置**——它被保留为占位但不产生实际行为差异。建议要么去掉此配置，要么明确标注"v0.7 保留配置但不实现，v0.8 启用"。

2. **[性能 P3] 串行兜底场景的尾部延迟未被评估。** 最坏场景（每页 T1 FAIL → T2 FAIL → T3 执行 120s 超时），50 页 × 120s = **6000s（100 分钟）**，远超默认总时间预算 7200s 但不触发超时（因为第 1114 行是在**每页开始前**检查，而 50 页的 6000s < 7200s）。实际上，更坏的情况是 T2 云端 30s + T3 120s = 150s/页 × 50 = 7500s > 7200s，此时会在第 ~48 页触发总时间预算。**设计上依赖总时间闸兜底第 50 页前触发，是合理的。**

3. **[信息] 串行 T2 + T3 的**"前 N 页"服务体验差异**。对于短书（10 页以下），串行延迟差异不大。对于长书（50 页），如果用户手动取消中途进程，已处理的 T1 PASS 页结果不丢失（`pages_text` 实时追加）。这一点设计正确。

---

## P1 — Budget exhaust 语义分析

### 实现

```python
def exhaust(self) -> None:
    self._exhausted = True

@property
def exhausted(self) -> bool:
    return self._exhausted
```

### 充分性分析

**充分的部分：**

1. **`exhaust()` 在双闸触发后立即调用**，位置正确（第 1109/1117 行 break 前）。
2. **`if not budget.exhausted` 在 T2/T3 入口处检查**（第 1150/1182 行），确保预算耗尽后不再尝试更高级引擎。
3. **T2/T3 内部循环的 `if budget.exhausted: break`**（第 1156/1186 行）处理了"在当前页 T2 处理过程中总时间耗尽"的边界——虽然第 1114 行只检查了循环入口，但 T2/T3 内部不会跨页执行到总超时（总超时在每页循环 1114 行检查），所以 T2/T3 内部的 budget.exhausted 检查主要覆盖 `exhaust()` 被外部**其他机制**触发的情况。

**不充分的部分：**

1. **[性能 P2] `budget.exhausted` 只在 T2/T3 入口和引擎循环内检查，但 T1 全书级引擎（BookPipeline）不检查 budget。** 如果 T1 BookPipeline 执行了 45 分钟后返回，且超过 `total_timeout_s`，双闸会在 T1 返回后的逐页循环中触发——但 T1 本身消耗的时间不会被中断。对于全书 50 页，T1 的 BookPipeline（如 paddleocr）通常 < 5 分钟，不构成实际问题。但如果某个 Tier 1 引擎特别慢（如 mineru 可能需要 20-30 分钟），这 20-30 分钟是**预算盲区**。

   **建议：** 如果未来引入多 Tier 1 候选或 T1 引擎可替换，可以考虑为 `run_book_engine` 也加超时包裹。当前阶段可接受（因为 T1 只执行一次且执行引擎单一）。

2. **[性能 P1] `Budget.check_time_budget()` 方法（第 283 行）在整个 Orchestrator 中未被使用。** 这是一个死代码风险——方法已定义但调用方全部内联了 `elapsed > config.total_timeout_s`。当 budget 和 config 解耦时（如 CLI 覆盖超时但未更新 config），check_time_budget 会返回错误结果。

   **建议：** 编排循环第 1114 行统一为 `if not budget.check_time_budget(time.monotonic() - start_time)`。

---

## 其他性能相关发现

### P2 — GlyphVerifier 性能预算未验证

第 924 行规定了 "单次 `verify()` < 50ms"，但未给出验证方法。`ToxinDoseDetector` 使用 `re.finditer` 逐药名匹配，toxic_db 规模未知。如果 toxic_db 有 200+ 条药名且每条都用独立正则搜索，`verify()` 可能接近或超过 50ms 预算。

**建议：** 增加一条性能测试用例（`test_verifier_perf_budget`），用最大规模的 toxic_db fixture 验证 `verify()` 在 50ms 内完成。

### P3 — `_run_single_engine_with_timeout` 的线程池开销

第 1300 行每次调用都创建一个 `ThreadPoolExecutor(max_workers=1)`。Python 线程池创建有固定开销（~1-5ms），且线程不会被立即回收（默认 idle 保持）。每页 Tier 3 调用都创建新线程池，50 页 = 50 个线程池对象。

**建议：** 在 `orchestrate_book()` 级别创建一个共享的线程池（或使用 `concurrent.futures.ThreadPoolExecutor` 作为实例变量重用），避免每页建池开销。或者直接使用 `threading.Thread` + `thread.join(timeout=timeout_s)` + `thread.is_alive()` — 更轻量。

### P3 — `_build_pages_result` 中 `pr.text = text` 的副作用

第 1280 行直接修改 `tier1_result.pages[i].text`。如果 `tier1_result.pages` 在其他地方被引用（如日志打印、缓存），会观察到 text 被覆盖。当前代码中 `tier1_result` 在函数外部不再使用，但这是脆弱的设计。

**建议：** 将 `pr = tier1_result.pages[i]` 改为 `pr = replace(tier1_result.pages[i], text=text)`（使用 `dataclasses.replace` 创建副本）。

---

## 总结优先级

| 优先级 | 问题 | 影响评估 |
|--------|------|---------|
| **P1** | NDJSON 启动加载按行号截断而非按时间戳截断，丢弃最新数据 | 引擎评分使用过时历史数据，调度器决策偏离真实表现 |
| **P1** | Tier 2 引擎调用缺少单页超时保护 | 最坏场景：云引擎卡死时单页阻塞数分钟 |
| **P1** | `Budget.check_time_budget()` 未被编排循环使用 | 代码与行为不一致，长期维护风险 |
| **P2** | 轮询采样的 `last_seen` 无强制保护，依赖"约定" | 轮询数据干扰衰减因子，影响新引擎评分 |
| **P2** | `engine_parallel` 配置名不符实 | 使用者可能期待并行但实际串行 |
| **P2** | GlyphVerifier 50ms 预算未验证 | 大规模 toxic_db 可能超预算 |
| **P3** | `_run_single_engine_with_timeout` 每页创建线程池 | 微优化，50 页场景约 50ms 额外开销 |
| **P3** | `_build_pages_result` 的直接修改副作用 | 当前无影响，但在代码复用场景下是隐患 |
