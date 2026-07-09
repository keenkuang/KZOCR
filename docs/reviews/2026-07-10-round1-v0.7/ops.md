# KZOCR v0.7 自适应 OCR 引擎编排层 · 运维工程师评审

- **角色**：运维工程师
- **评审日期**：2026-07-10
- **评审范围**：`docs/plans/ocr-engine-unification.v0.7.md`
- **总体结论**：**不通过**（需修正 2 项设计缺陷、补齐 3 项可观测性要求后方可进入实施）

---

## 总体判断

v0.7 编排层的架构方向（注册中心 + 调度器 + 字形验证器）正确，解决了 v0.6 硬编码 if-else 的核心痛点。但从运维视角看，方案存在三个层面的缺口：

1. **可观测性设计未纳入方案** — 调度决策、引擎耗时、失败链路完全不可追溯
2. **持久化策略存疑** — `docs/reviews/` 是设计评审归档目录，不是运行时数据存放地
3. **多级缓存关系未定义** — D3 VLM 缓存、引擎级缓存、benchmark 持久化三者可能相互矛盾而无协调机制

以下按 6 个评审要点展开。

---

## 1. Benchmark 持久化位置：`docs/reviews/` 不合理

**方案原文**（E1 说明）：`EngineStats` 的 benchmark 数据"持久化到 `docs/reviews/` 目录"。

### 问题

- `docs/reviews/` 是设计评审归档目录，由开发/架构评审写入，不应被运行时进程写入。运维侧预期此目录只读、可版本管理（git tracked）。
- 每本古籍 OCR 后触发写入 `docs/reviews/`，会导致 git 仓库被自动生成的 JSON 文件污染（git dirty / 需频繁 .gitignore），且该目录无容量管理机制（古籍基准运行数百次后该目录可能膨胀至 GB 级）。
- 多实例部署（如容器化）时，各实例共享同一文件系统的 `docs/reviews/` 存在并发写冲突。

### 建议的持久化方案

| 方案 | 说明 | 运维评价 |
|------|------|---------|
| **推荐：`KZOCR_BENCHMARK_DIR` 独立数据目录** | 新增配置项，默认 `$KZOCR_OUTPUT_DIR/benchmarks/`，与缓存/输出同层级 | 不受 git 管理、可挂载持久卷、多实例可独立或共享 |
| 备选：SQLite | 复用或新建 `benchmark.db`，结构化查询方便 | 但增加数据库维护，对于单机场景过重 |
| 备选：仅内存 + 日志 | 启动时从缓存重建、退出时不持久，仅靠日志回溯 | 重启后历史丢失，与"调度器按历史表现排序"的设计矛盾 |

**Core 理由**：benchmark 数据是**运行时运营数据**，不是设计评审产物。应放入 `KZOCR_OUTPUT_DIR` 或独立数据目录，`docs/` 只保留人类可读的设计文档。

### 持久化格式

```yaml
# $KZOCR_BENCHMARK_DIR/<engine_name>/benchmark.yaml
# 或 JSON，推荐 YAML 人工可读
engine: sensenova
updated_at: "2026-07-10T14:30:00Z"
stats:
  total_calls: 1427
  avg_latency_ms: 3450
  glyph_pass_rate: 0.87
  recent_trend:  # 最近 100 次滑动窗口
    calls: 100
    pass_rate: 0.91
    avg_latency_ms: 2800
```

滑动窗口比全局累计更重要——运维需知道引擎**最近**表现而非"开天辟地以来"。

---

## 2. 可观测性：调度决策目前近乎盲操作

### 当前方案中与日志/指标相关的内容

- `orchestrator.py` 的 `registry.record(engine, success=True, glyph=verdict)` — 仅记录统计，未说明是否输出日志
- `BookResult.failed_pages` — 仅作为返回结构体的字段，未提及推送指标

### 调度器每次做出选择时应输出的内容

```python
# scheduler.select_candidates() 中
logger.info(
    "[scheduler] tier=%d page=%d candidates=%s weighted=%s selected=%s",
    tier, page.num,
    [e.meta.name for e in candidates],
    [f"{e.meta.name}:{e.stats.glyph_pass_rate:.2f}/{e.stats.avg_latency_per_page_ms:.0f}ms" for e in candidates],
    selected[0].meta.name if selected else "NONE",
)
```

**最低要求日志字段**，每页每 tier：

| 字段 | 示例 | 用途 |
|------|------|------|
| 时间戳 | `2026-07-10T14:30:00.123Z` | 时序关联 |
| 页码 | `page=42` | 排查 |
| Tier | `tier=1` | 层级回溯 |
| 候选列表 | `[paddleocr, rapidocr, mineru]` | 调度依据 |
| 权重分 | `paddleocr:0.85/320ms` | 排序理由 |
| 选中引擎 | `-> rapidocr` | 最终决策 |
| 验证结果 | `glyph=PASS/0.92` | 字形验证裁决 |
| 耗时 | `engine_ms=450, verify_ms=12` | 性能分析 |

### failed_pages 应该暴露为指标

**应**。原因：

- `BookResult.failed_pages` 目前仅作为返回值的一个字段传递，下游（zai 校对台）并不消费它（`BookResult` → `BookResult.failed_pages` 但适配器 `push_book_to_zai` 不检查失败页）。
- 运维维度需要实时知道**失败率上升**（例如连续 10 本书的失败页 > 5%），在批处理中发现异常。
- 建议在编排主循环中（`orchestrate_book`）结束时输出结构化指标：

```python
# orchestrate_book() 返回前
failed_ratio = len(failed_pages) / total_pages
logger.info(
    "[orchestrator] book=%s pages=%d failed=%d ratio=%.2f engines_used=%s",
    book_code, total_pages, len(failed_pages), failed_ratio,
    list(engine_usage_counter.keys()),
)
# 若需要指标暴露（Prometheus/StatsD），增加：
if failed_ratio > 0.1:
    logger.warning("[orchestrator] 失败率超阈值: %.2f", failed_ratio)
```

### 指标暴露建议

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `kzocr_engine_calls_total` | Counter | `engine, tier, status` | 引擎调用次数（success/fail/skip） |
| `kzocr_engine_latency_ms` | Histogram | `engine, tier` | 引擎耗时分布 |
| `kzocr_glyph_verdict_total` | Counter | `engine, status` | 字形验证裁决分布 |
| `kzocr_failed_pages_per_book` | Gauge | `book_code` | 每本书失败页数（批处理用） |

**最低实现**：即使在 Prometheus 端点不存在的场景，也应在日志中输出以上指标的等效结构化信息，使运维可通过 `grep` / `jq` 从日志重建指标曲线。

---

## 3. 缓存策略：三级缓存关系未定义

v0.7 涉及三类缓存/持久化：

| 缓存层级 | 来源 | 粒度 | 用途 | 生命周期 |
|---------|------|------|------|---------|
| D3 VLM 缓存 | v0.5 D3 | 单页文本 | 断点续跑、重复页面跳过 | 按 TTL 过期（默认 24h） |
| 引擎级缓存 | v0.6 bookmark cache | 页或书 | 引擎自身缓存（如 PaddleOCR 的推理缓存） | 引擎内部管理 |
| Benchmark 持久化 | v0.7 E1 | 引擎全局 | 调度器历史依据 | 持久（无 TTL） |

### 三者的关系管理原则

1. **VLM 缓存应优先于调度器** — 如果 D3 缓存命中，该页不进入调度流程。编排主循环应在调用调度器之前检查 VLM 缓存，避免重复 OCR。
2. **Benchmark 不应包含缓存命中的数据** — 如果某页通过 D3 缓存跳过，不应计入该引擎的 benchmark（会歪曲 latency 统计）。
3. **引擎级缓存是黑盒** — 编排层不应管理、不应清除、也不应感知引擎内部缓存。但编排层应记录**引擎调用实际耗时**（等于引擎返回的时间），无论引擎内部是否缓存。
4. **Benchmark 持久化写入频率** — 不应每页写入（I/O 开销大），建议：
   - 进程内统计实时更新内存中的 `EngineStats`
   - 每本书完成后（或每 N 本书）批量写回磁盘
   - 进程退出时确保 flush

### 协调建议

```python
# orchestrator 伪代码
for page in pages:
    # 1. D3 缓存优先
    cached = vlm_cache.get(page)
    if cached:
        pages_text.append(cached)
        continue  # 不进入调度器，不影响 benchmark
    
    # 2. 调度器选择引擎
    engine = scheduler.select(registry, tier, page, budget)
    
    # 3. 调用引擎，记录原始耗时（含引擎内部缓存）
    t0 = time.monotonic()
    result = engine.run(page)
    latency_ms = int((time.monotonic() - t0) * 1000)
    
    # 4. 验证器检查
    verdict = verifier.check(result.text)
    
    # 5. 更新内存中的 benchmark（不含 D3 缓存命中数据）
    registry.update_stats(engine, latency_ms=latency_ms, verdict=verdict)
    
    # 6. 写入 D3 缓存
    vlm_cache.set(page, result.text)

# 书完成后：批量持久化 benchmark
registry.persist_benchmarks()
```

---

## 4. 多引擎并发场景的资源监控

### 方案现状

计划提到"多引擎并行（同一页）"但具体实现未展开。从伪代码看仍为**串行逐引擎尝试**（`for engine in engines: ... break`），并非真正的并行执行。

### 如果最终走向真正并行

运维需要知道：

| 需要知道什么 | 如何获取 | 优先级 |
|-------------|---------|-------|
| 哪个引擎正在运行 | `ps aux | grep python` + 进程名标注 | 低（容器级够用） |
| 每个引擎的实时 GPU 使用 | `nvidia-smi pmon` 或 `py3nvml` 轮询 | 中 |
| 每个引擎的 VRAM 占用 | 同上 + 配置中 `min_vram_gb` 的校验 | 中 |
| 每引擎每页耗时 | 编排层日志 | **高** |
| 引擎调用栈（A 失败→B 被选→B 成功） | 结构化日志 + trace_id | **高** |

### 建议

1. **每个引擎调用包裹计时器**，日志输出 `engine=X, page=Y, latency_ms=Z, result=success/fail`
2. **引入 `trace_id`（每本书一个）**，贯穿所有日志，方便 `grep trace_id` 追溯单本书的完整调用链
3. **如果后期做并行**（如同一页同时发往 3 个引擎），需要：
   - `asyncio` 或 `concurrent.futures` 线程池 + 超时控制
   - 并发数上限（可配置，默认 2）
   - 每引擎资源隔离（各容器/sandbox？目前为同一进程，资源竞争不可避免）

当前串行场景下，**最少日志要求**：

```python
t0 = time.monotonic()
result = engine.run(page)
elapsed = time.monotonic() - t0
logger.info(
    "[engine] book=%s page=%d engine=%s latency_ms=%d verdict=%s",
    book_code, page.num, engine.meta.name,
    int(elapsed * 1000), verdict.status,
)
```

---

## 5. 运维排障：古籍 OCR 结果不理想的追溯方案

### 当前方案可提供的追溯能力

| 问题 | 方案中有吗？ | 评注 |
|------|------------|------|
| 这本书用了哪些引擎？ | 否 | `orchestrate_book()` 未记录引擎使用序列 |
| 每级（Tier 1/2/3）的耗时？ | 否 | 无每 tier 计时 |
| 哪些页失败了？ | 部分 | `failed_pages` 仅在 `BookResult` 中，未见落库或日志 |
| 字形验证器说了什么？ | 否 | 验证结果仅在内存中流转 |

### 建议的追溯方案

**trace_id + 每页每级结构化日志**：

```python
# 每页编排时输出一条结构化 JSON 日志，方便后续用 jq 查询
import json
trace = {
    "trace_id": trace_id,
    "book_code": book_code,
    "page": page.num,
    "events": [],
}

# Tier 1
t1_candidates = scheduler.select(...)
t1_selected = t1_candidates[0].meta.name if t1_candidates else None
t1_start = time.monotonic()
# ... engine.run ...
t1_elapsed = time.monotonic() - t1_start
trace["events"].append({
    "tier": 1, "engine": t1_selected,
    "latency_ms": int(t1_elapsed * 1000),
    "verdict": verdict.status,
    "confidence": verdict.confidence,
})

# 书完成后输出
logger.info("[trace] %s", json.dumps(trace))
```

运维排查命令示例：

```bash
# 找某本书的完整引擎调用链
grep "$trace_id" kzocr.log | jq '.events'

# 检查失败页分布
grep "FAIL" kzocr.log | jq 'select(.events[].verdict == "FAIL") | .page'

# 查看某引擎在所有书上的平均耗时
grep "engine=sensenova" kzocr.log | jq -s 'map(.events[] | select(.engine=="sensenova") | .latency_ms) | add / length'
```

### 建议的落地方案

1. 在 config 中新增 `KZOCR_TRACE_DIR`（默认 `$KZOCR_OUTPUT_DIR/traces/`）
2. 每本书完成后，将 trace JSON 写入 `{trace_dir}/{book_code}_{timestamp}.json`
3. 这个 trace 文件包含：引擎选择序列 + 每级耗时 + 每页验证结果 + failed_pages
4. **不写入数据库**（避免与 zai 的数据模型耦合），以文件形式独立存储

这样运维人员排查的思路是：

```
发现某古籍 OCR 结果差
  → 找到该书的 trace 文件
  → 查看调度器选了哪些引擎（看看是不是总选了个差的）
  → 查看各 tier 耗时（是否 tier 1 就耗时很长、tier 2/3 根本没被调用）
  → 查看失败页分布（是集中在某些特定版面，还是随机失败）
  → 查看字形验证裁决（是"勉强 PASS"还是"真 PASS"）
```

---

## 6. 部署影响：引入 `scheduler/` 目录后的变化

### 新增文件

```
kzocr/scheduler/__init__.py    ─ 新模块，无部署配置
kzocr/scheduler/registry.py    ─ 无新增外部依赖
kzocr/scheduler/scheduler.py   ─ 无新增外部依赖
kzocr/scheduler/verifier.py    ─ 无新增外部依赖（依赖内部 glyph_status/confusion_set）
kzocr/orchestrator/orchestrator.py ─ 调用以上模块
```

### 部署配置变化

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `KZOCR_BENCHMARK_DIR` | 路径 | `$KZOCR_OUTPUT_DIR/benchmarks/` | benchmark 持久化目录（需可写） |
| `KZOCR_MAX_TIER1_ENGINES` | 整数 | 2 | Tier 1 最大并发引擎数 |
| `KZOCR_TRACE_DIR` | 路径 | `$KZOCR_OUTPUT_DIR/traces/` | trace 文件输出目录（需可写） |
| `KZOCR_TOTAL_TIMEOUT` | 整数 | 7200 | 已存在，编排层应复用 |

### 部署检查清单

| 检查项 | 说明 |
|--------|------|
| `$KZOCR_OUTPUT_DIR` 可写 | 编排层的缓存/benchmark/trace 全部依赖此目录 |
| 磁盘容量规划 | benchmark 随时间增长，需评估 retention 策略 |
| 容器挂载 | `$KZOCR_OUTPUT_DIR` 应挂载持久卷，而非容器 ephemeral 存储 |
| `.gitignore` | 新增 `benchmarks/`、`traces/` 到 `.gitignore` |
| `scripts/` 部署脚本 | 如需创建 benchmark 数据目录的初始化步骤 |
| `pyproject.toml` | 无新增运行时依赖（全为标准库 + 已有依赖），`dependencies` 不变 |

### 对现有配置的影响

| 现有配置 | 影响 |
|---------|------|
| `KZOCR_USE_MOCK` | 仍优先于编排层：`if cfg.use_mock: return mock_book()` |
| `KZOCR_USE_VLM` | 建议也纳入编排层（作为 Tier 2/3 的候选），但方案未明确是否兼容 |
| `KZOCR_VLM_ENGINE=auto` | 自动选择逻辑应由调度器接管，而非 `_init_vlm_adapter` 中的硬编码 |
| `allow_cloud_vision` | **关键**：编排层必须校验此开关，禁止将古籍图像发往非授权的云端引擎 |

### 特别风险：混合模式下的配置兼容

方案说 `run_engine()` 改为调用 `orchestrate_book()`，但当前 `run_engine()` 的逻辑是：
1. `use_mock` → mock
2. `use_vlm` → _run_vlm（绕过 pipeline）
3. 否则 → _run_real（pipeline）

v0.7 编排层接管后，`use_vlm` 的分支是否消失？如果保留，则存在两条编排路径（老 VLM v0.5 路径 vs 新编排层路径），增加运维的配置复杂度。建议**v0.7 完全废弃 `use_vlm` 和 `vlm_engine` 两个配置项**，统一由调度器管理。

---

## 建议汇总

### 必须修复（阻断实施）

| # | 问题 | 优先级 | 建议 |
|---|------|--------|------|
| A1 | Benchmark 持久化到 `docs/reviews/` | **H** | 改为 `KZOCR_BENCHMARK_DIR`（默认 `$KZOCR_OUTPUT_DIR/benchmarks/`） |
| A2 | 调度决策零日志，不可追溯 | **H** | 调度器每次选择输出结构化日志（候选列表、权重、选中项、耗时） |
| A3 | `failed_pages` 未暴露为指标 | **H** | 至少输出日志指标（失败率、失败页数）；有条件则推 Prometheus |

### 强烈建议

| # | 问题 | 优先级 | 建议 |
|---|------|--------|------|
| B1 | 三级缓存关系未定义 | **H** | 明确 D3 缓存→调度器→引擎级缓存的优先级；benchmark 不计缓存命中数据 |
| B2 | 缺少 trace 机制 | **H** | 每本书生成 trace JSON（引擎序列+耗时+裁决），存 `KZOCR_TRACE_DIR` |
| B3 | `use_vlm` / `vlm_engine` 配置项残留 | **M** | v0.7 应废弃这两个配置，统一由编排层调度器管理 |

### 建议改善

| # | 问题 | 优先级 | 建议 |
|---|------|--------|------|
| C1 | benchmark 无滑动窗口 | **M** | 增加最近 N 次（默认 100）的滑动窗口统计，反映引擎当前表现 |
| C2 | 编排层未校验 `allow_cloud_vision` | **M** | 调度器在选择云端引擎前必须检查此开关 |
| C3 | `KZOCR_OUTPUT_DIR` 为必要条件 | **L** | 文档中明确标注该目录为必填且需持久化存储 |

---

## 总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | A- | 注册中心+调度器+验证器结构清晰，解决了 v0.6 的硬编码问题 |
| 可观测性 | **D** | 调度决策无日志、引擎耗时无记录、failed_pages 无指标暴露、无 trace 机制。生产环境下几乎是盲操作 |
| 持久化策略 | **D** | Benchmark 数据存入设计评审目录，违反分层原则，且无容量管理/并发写保护 |
| 运维排障能力 | **D** | 当前方案无法回答"这本书用了哪些引擎、每级耗时多少"这个排障最基本的问题 |
| 部署平稳性 | B | 新增文件无外部依赖；但配置项兼容性需注意 `use_vlm` 的废弃时机 |

**综合结论：不通过** —— 架构方向正确，但可观测性和持久化方案存在设计级缺陷。需优先处理 A1-A3 后方可进入实施。

运维工程师底线要求：**让调度器"开口说话"**——每个选择都要有日志、每本书都要有 trace、每页失败都要可归因。做不到这三点，编排层就是黑盒，出问题只能加日志重跑，这在古籍 OCR 这种一次数小时的批处理场景中不可接受。
