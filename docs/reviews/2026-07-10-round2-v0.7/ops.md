# KZOCR v0.7 自适应 OCR 引擎编排层 · 运维工程师第二轮评审

- **角色**：运维工程师
- **评审日期**：2026-07-10
- **评审范围**：`docs/plans/ocr-engine-unification.v0.7.md`（修订版，commit ece9d9e）
- **上一轮**：`docs/reviews/2026-07-10-round1-v0.7/ops.md`
- **总体结论**：**有条件通过** —— 3 项阻塞问题已解决 2 项，1 项部分解决；新增 1 项阻塞问题和 2 项强烈建议需在 Phase 3 前确认

---

## 第一轮问题逐项复查

### A1（阻断）：Benchmark 持久化目录 —— ✅ 已解决

| 维度 | 第一轮方案 | 修订后方案 | 运维评价 |
|------|-----------|-----------|---------|
| 存放位置 | `docs/reviews/` | `$KZOCR_OUTPUT_DIR/benchmarks/`（7.1） | ✅ 符合预期 |
| 格式 | 未指定 → 我建议 YAML | NDJSON 追加式（每行独立 JSON） | ✅ ++ NDJSON 优于我的 YAML 建议 |
| 写入策略 | 未指定 | 进程内实时更新内存 stats，每本书完成后批量 flush（7.1） | ✅ |
| 并发安全 | 未考虑 | 复用 `atomic.py` 的原子写入（7.1, 9） | ✅ |
| 目录配置 | 无 | `KZOCR_BENCHMARK_DIR` → 默认 `$KZOCR_OUTPUT_DIR/benchmarks/`（7.3） | ✅ |

**评价**：NDJSON 追加式比 YAML 更优 —— 每行独立序列化，行级追加 O(1) 写，原子写入防损坏。且 `last_seen` 使用 `time.time()` 而非 `time.monotonic()` 支持跨进程持久化（3.1），这个细节说明团队考虑了容器重启/进程迁移场景。

**但是**（见下文 C1）：滑动窗口问题仍未解决。

---

### A2（阻断）：调度决策零日志 —— ⚠️ 部分解决

| 维度 | 修订版做了什么 | 还缺什么 |
|------|--------------|---------|
| trace 机制 | ✅ 新增 `EngineCallRecord`、trace JSON、`KZOCR_TRACE_DIR`（6.5） | — |
| 引擎报告 | ✅ 书完成时输出结构化引擎报告日志（6.5） | — |
| 候选列表日志 | ❌ 无 | 调度器 `select_candidates()` 内部未输出每个 tier 的候选列表、权重分、选中决策 |
| 每页每 tier 耗时 | ❌ 无 | 主循环中 `_run_book_engine` / `_run_page_engine` 包裹了 `time.monotonic()` 计时，但结果仅写入 trace 结构体，未输出为 **stdout 结构化日志** |
| grep 可追溯 | ❌ 无 | trace 写入文件（可选），但运维在容器化场景下更依赖 `kubectl logs` / `journalctl` grep |

**核心差距**：trace JSON 写入文件的机制很好，但运维在排查时通常先 grep 日志，找不到才去翻 trace 目录。如果 trace 是**仅文件**（`if config.trace_dir:`），而默认 `trace_dir=""`（7.3），则默认场景下调度决策依然不可追溯。

**要求**：至少在 `logger.info()` 级别输出每页每 tier 的决策摘要，不依赖 `trace_dir` 配置。以下是我认为最低限度的日志输出，纳入主循环即可：

```python
# orchestrate_book() 主循环中，每个 tier 选择后
logger.info(
    "[scheduler] book=%s page=%d tier=%d candidates=%s selected=%s latency_ms=%d verdict=%s",
    book_code, page_num, tier,
    ",".join(e.meta.name for e in candidates),
    selected_engine.meta.name,
    latency_ms, verdict.status,
)
```

这样即使 `trace_dir=""`，运维仍能通过 `grep "\[scheduler\] book=mifangqiuzhen-970"` 重建全书调用链。

---

### A3（阻断）：failed_pages 未暴露为指标 —— ❌ 未解决

修订版 6.5 的引擎报告日志包含 `总页数: 48 | 失败: 0 | UNCERTAIN: 2`，但这只是信息输出，不是指标暴露：

| 问题 | 状态 | 说明 |
|------|------|------|
| 失败页数落在日志中 | ✅ | 引擎报告包含 |
| 失败率超阈值告警 | ❌ 无 | 无 `if failed_ratio > 0.1: logger.warning(...)` |
| Prometheus/StatsD 推送 | ❌ 无 | 计划未提及任何指标推送机制 |
| 失败页关联引擎 | ❌ 无 | 无法知道某页失败是因为 Tier 1 错了还是全 Tier 失败 |

**我降低这次的要求**：从"推到 Prometheus"降为"日志级告警"。最低要求：

```python
# orchestrate_book() 返回前增加
failed_ratio = len(failed_pages) / max(len(pages_text) + len(failed_pages), 1)
if failed_ratio > 0.1:
    logger.warning(
        "[orchestrator] book=%s failed_ratio=%.2f exceeds threshold (10%%)",
        book_code, failed_ratio,
    )
if failed_ratio > 0.3:
    logger.error(
        "[orchestrator] book=%s failed_ratio=%.2f exceeds critical threshold (30%%)",
        book_code, failed_ratio,
    )
```

**Prometheus 指标推送可 deferred 到 v0.8**，不阻塞本版本。

---

### B1（强烈建议）：三级缓存关系 —— ⚠️ 部分解决

| 关系 | 修订版定义 | 评价 |
|------|----------|------|
| D3 缓存 vs 调度器 | 9: "编排循环在调度器前检查缓存"；3.2: "缓存优先于调度器，不计 benchmark" | ⚠️ 文字层面定义了，但主循环伪代码（6.2）**并未体现** D3 缓存检查。文字是承诺，代码是事实 |
| 缓存命中 vs benchmark | "缓存命中不计 benchmark" | ✅ 定义清楚 |
| 引擎级缓存 | "黑盒，编排层不管理" | ✅ 定义清楚 |
| 写入 D3 缓存时机 | 未在伪代码中体现 | ❌ 主循环调用引擎后，结果应该写入 D3 缓存以供后续使用 |

**问题**：主循环伪代码中的流程是：
```
渲染 → 双闸检查 → Tier 1（首次执行全书）→ 逐页验证 → 失败则 Tier 2 → 失败则 Tier 3 → HumanGate
```

缺少：
1. 进入 Tier 1 前检查 VLM 缓存（缓存命中则跳过该页，且在页计数预算内）
2. 引擎执行后写入 VLM 缓存

**要求**：在 6.2 的主循环伪代码中**明确标注** D3 缓存的检查点与写入点。不是只在文本段中提一句，而是要让读者能在伪代码中看到缓存位。例如：

```python
# Tier 1 阶段前
cached_text = vlm_cache.get(page_input)
if cached_text is not None:
    pages_text.append(cached_text)
    continue  # 跳过调度验证，不计入 benchmark
```

---

### B2（强烈建议）：trace 机制 —— ✅ 已解决

| 维度 | 修订版方案 | 评价 |
|------|----------|------|
| `EngineCallRecord` | 6.5: 含 page/tier/engine/latency_ms/glyph_status | ✅ |
| trace JSON 文件 | 6.5: `{trace_dir}/{book_code}_{timestamp}.json` | ✅ |
| `KZOCR_TRACE_DIR` 配置 | 7.3: 可选，默认空字符串 | ✅ 默认不写文件，高频场景不会产生额外 I/O |
| trace 在 BookResult 中 | 6.2 主循环返回 `engine_trace=trace` | ✅ |
| 引擎报告日志 | 6.5: 结构化摘要 | ✅ |

**评价**：trace 机制设计完整，从粒度（每 event）到持久化（文件可选）到传递（BookResult 携带）都有定义。但同上 A2 的 gap：trace 仅在文件启用时有，默认 stdout 无调度日志。

---

### B3（强烈建议）：use_vlm/vlm_engine 配置项 —— ⚠️ 部分解决

修订版 7.2 的处理方式：

```python
if config.use_vlm:
    config_overrides = SchedulerConfig(disabled_tiers=[1])
    return orchestrate_book(pdf_path, book_code, config, config_overrides)
```

从运维角度看，有几个问题：
1. **两条路径并存**：`use_vlm`、`use_mock`、调度器，共 3 种模式。`use_vlm` 映射为"禁 T1 的调度器"还算合理，但运维需要记住 `use_vlm` 已经不是以前那个意思了。
2. **配置项保留周期未定义**：废弃配置项最怕"此配置将在下版本移除"但一直不清除。建议明确标注废弃时间线。
3. **`use_mock` 为何不走调度器**：如果 `use_mock` 只是"始终可用 + 轻量"，为什么不能注册为 Tier 0 引擎（已存在于注册表中）而非保留独立的短路分支？

**要求**：在文档或 changelog 中明确标注 `use_vlm` 和 `vlm_engine` 的废弃计划（建议 v0.8 移除），运维需要同步更新部署模板和监控面板。

---

### C1（建议改善）：benchmark 滑动窗口 —— ❌ 未解决

修订版 3.1 的 `EngineStats` 仍然只存储**全局累加值**（`total_calls`、`total_latency_ms` 等）。我的第一轮评审建议增加滑动窗口（最近 100 次），反映引擎近期表现。

**影响分析**：
- 全球累计值对运维的参考价值有限。如果一个引擎在"开天辟地"时表现很好但最近变差了（例如云端引擎升级导致延迟变高），全球平均值需要很久才会反映变化。
- 但这不是本版本的阻塞问题 —— 贝叶斯平滑 + 半衰期衰减（4.2）已经在一定程度上缓解了"历史高分锁定"的问题。

**降级处理**：滑动窗口 defer 到 v0.8。当前方案依赖衰减因子 + 冷启动采样来缓解，在可接受范围内。

---

## 第二轮新增发现

### D1（新增 · 阻塞）：NDJSON benchmark 目录缺乏容量管理机制

**问题**：NDJSON 追加式写入是 O(1) 性能优异，但缺少 Retention 策略。

- 默认每本书几百页，每页一条 NDJSON 事件
- 如果批处理跑 10000 本书，`benchmark_dir` 将积累数百万行数据
- 进程启动时"从 benchmark 目录加载重建 `EngineStats`"（7.1），大文件会拖慢启动时间
- 没有 TTL/轮转机制的持久化日志文件会在长期运行中膨胀

**建议（Phase 1 加入，非阻塞但必须设计方案）：**

```python
@dataclass
class BenchmarkRetention:
    max_events_per_engine: int = 10000       # 每个引擎最多保留的事件数
    compact_interval_books: int = 100        # 每 N 本书后触发 compact
    archive_old_entries: bool = True         # compact 时丢弃旧数据
```

**注意**：benchmark 数据不同于其他日志 —— 写入 O(1) + 缓存到内存 + 定期 flush 的模型已经够好。但如果加载 1GB 的 NDJSON 重建内存状态，用户等 30 秒才能看到第一个调度决策，这是不可接受的。

**最低要求**：在 benchmark 加载路径中加一个 `max_load_lines` 截断（默认 50000 条/引擎），超过的只加载最新 N 条。

---

### D2（新增 · 强烈建议）：预算穿透时调度器行为未定义

修订版 4.1 调度流程的步骤 4 是"预算检查"，6.3 定义了 `Budget.check_time_budget()`。但看主循环代码：

```python
# Tier 2
if budget.check_time_budget(elapsed) and not budget.exhausted:
```

这里有个问题：`budget.exhausted` 始终返回 `False`（6.3: `return False  # 由外部循环管理`）。谁设置它为 `True`？

- 双闸（6.3）是在循环顶部检查的，如果时间耗尽则 `break`
- 但 Tier 2 内部的 `budget.exhausted` 检查形同虚设
- 如果单页耗时极长（接近 `total_timeout`），循环 break 是对的，但 Tier 2/3 的 `budget.check_time_budget()` 和 `budget.exhausted` 的双重检查令人困惑

**要求**：明确 `Budget.exhausted` 的行为 —— 谁负责设置它、什么条件下触发。不要在 dataclass 中放一个始终返回 `False` 的属性并写注释"由外部循环管理"，这会成为后续维护者的陷阱。

---

### D3（新增 · 建议改善）：EngineStats.load() 从 NDJSON 重建的效率风险

7.1: "进程启动时从 benchmark 目录加载重建 `EngineStats`"

假设 benchmark_dir 积累了 50MB NDJSON，`load()` 需要：
- 扫描目录
- 解析每一行 JSON
- 累加到对应引擎的 `EngineStats`

如果 benchmark 目录中有老旧事件（例如 3 个月前的），它们仍会被加载并影响 `total_calls` / `total_pages`。而衰减因子（4.2）依赖 `last_seen` 来降权，但 `last_seen` 本身也从 NDJSON 重建 —— 如果加载时 sum 了所有历史事件但只保留了最新 `last_seen`，衰减因子仍可能对历史数据降权不足。

**建议**：`EngineStats.load()` 应增加一个**时间窗口**参数（默认只加载 90 天内的数据）。实现方式：

```python
def load_benchmarks(benchmark_dir: str, max_age_days: int = 90) -> dict[str, EngineStats]:
    cutoff = time.time() - max_age_days * 86400
    stats: dict[str, EngineStats] = {}
    for line in _read_ndjson_lines(benchmark_dir):
        event = json.loads(line)
        if event["ts"] < cutoff:
            continue  # 跳过过时事件
        # ... 累加统计
    return stats
```

---

### 新发现：可观测性总结

修订版的 `BookResult` 扩展（6.5）提供了以下可观测性数据：

```
engine_trace[] → 引擎调用时序 ✔
failed_pages   → 失败页集合    ✔
uncertain_pages → 不确定页集合  ✔
engine_report  → 书级报告日志  ✔
trace_file     → 离线分析      ✔
```

对比第一轮 my 的追溯方案检查清单：

| 排查问题 | 第一轮结果 | 修订版 | 结果 |
|---------|-----------|--------|------|
| 这本书用了哪些引擎？ | ❌ | `engine_usage_counter` + trace | ✅ |
| 每级耗时？ | ❌ | `EngineCallRecord.latency_ms` | ✅ |
| 哪些页失败了？ | 部分 | `failed_pages` | ✅ |
| 字形验证说了什么？ | ❌ | `EngineCallRecord.glyph_status` + 引擎报告 | ✅ |
| **默认即可 grep？** | ❌ | **trace 仅文件，默认不输出** | **❌** |

最后一行是当前最大 gap：trace 数据默认只能在 **文件中** 查看，不能通过 `kubectl logs | grep` 直接查询。修复见 A2。

---

## 最终评估矩阵

### 第一轮问题复查

| 编号 | 问题 | 原始状态 | 修订版状态 | 本轮判定 |
|------|------|---------|-----------|---------|
| A1 | Benchmark 持久化目录 | ❌ 阻断 | $KZOCR_OUTPUT_DIR/benchmarks/ | ✅ **关闭** |
| A2 | 调度决策零日志 | ❌ 阻断 | 有 trace JSON + 引擎报告，但**默认 stdout 仍无调度日志** | ⚠️ **需确认** |
| A3 | failed_pages 指标 | ❌ 阻断 | 引擎报告含失败数，但**无阈值告警** | ❌ **未解决** |
| B1 | 三级缓存关系 | ❌ 强烈 | 文字定义有，**主循环伪代码缺** | ⚠️ **需确认** |
| B2 | 缺少 trace 机制 | ❌ 强烈 | EngineCallRecord + trace JSON | ✅ **关闭** |
| B3 | use_vlm 配置残留 | ❌ 强烈 | 映射为禁 T1 | ⚠️ **需确认废弃时间线** |
| C1 | 无滑动窗口 | ❌ 建议 | 无，依赖衰减因子缓解 | ⏭️ **defer 到 v0.8** |
| C2 | allow_cloud_vision | ❌ 建议 | 已纳入调度器 4.1 | ✅ **关闭** |
| C3 | KZOCR_OUTPUT_DIR 文档 | ❌ 建议 | 占位但未强调持久化要求 | ⚠️ **需补充** |

### 本轮新增

| 编号 | 问题 | 类型 | 建议 | 要求 |
|------|------|------|------|------|
| D1 | benchmark 目录缺乏容量管理 | **阻塞** | 设计 retention / 加载截断策略 | Phase 1 前确认设计 |
| D2 | Budget.exhausted 形同虚设 | 强烈 | 明确设置者与触发条件 | Phase 3 前修复 |
| D3 | EngineStats.load() 无时间窗口 | 强烈 | 加载时过滤线 | Phase 1 补充 |

---

## 本轮结论

| 维度 | 评分 | 变化 |
|------|------|------|
| 架构设计 | A- | 不变，NDJSON 比我的 YAML 建议更好 |
| 可观测性 | D → **C** | 提升 1 级：trace/json 解决了离线追溯，但缺默认 stdout 调度日志 |
| 持久化策略 | D → **B** | 提升 2 级：目录位置修复，格式和原子写入设计优秀 |
| 运维排障能力 | D → **C+** | 提升 1.5 级：trace 机制就位，但依赖 trace_dir 使门索引仍高 |
| 部署平稳性 | B | 不变 |

### 判定：**有条件通过**

修订版架构调整符合运维预期，文档比例级（NDJSON 格式、原子写入、`time.time()` 跨进程）体现对生产环境的思考。

但以下 2 项需在进入 Phase 1 前确认设计，否则会给运维下坑：

1. **A2（降级为强烈）**：在 `orchestrate_book()` 主循环中加入每页每 tier 的 `logger.info()` 调度日志，不依赖 `trace_dir` 配置
2. **D1（新增中优先）**：benchmark NDJSON 的容量管理策略 —— 加载截断 + 轮转窗口

以下 2 项需在进入 Phase 3 前修复：

3. **D2（低优先）**：`Budget.exhausted` 的语义澄清
4. **B1**：在主循环伪代码中体现 D3 缓存检查点

以上 4 项确认后，本版本可进入实施。
