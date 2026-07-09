# v0.7 自适应 OCR 引擎编排层 —— 第二轮性能评审

> 评审角色：性能工程师
> 评审对象：`docs/plans/ocr-engine-unification.v0.7.md`（修订版）
> 评审目的：复查第一轮 3 个 P0 问题在修订版中的修复质量
> 环境假设：无 GPU；12 核 CPU；约 6 GiB 可用内存

---

## 一、总体判断

**有条件通过（Conditional Approve）。**

修订版对三个 P0 问题均给出了方案级别的修复，方向正确。但 **1 个 P0 修复存在实现残留风险**（Budget.exhausted 死代码），需要实施时关闭。此外修订版引入 **1 个新的 P1 性能隐患**（Tier 1 全书结果物化为 list，无页级超时保护）。

---

## 二、P0 复查

### P0#1：Tier 1 并行反收益 → **已修复，残留评级 P1**

**修复证据（v0.7 §6.4）：**

| 维度 | 第一轮指出 | 修订版处理 | 状态 |
|------|-----------|-----------|------|
| 默认并发 | 未定义 | **默认串行**（max_concurrency=1） | ✓ |
| GPU 环境 opt-in | 缺少约束 | `KZOCR_ENGINE_PARALLEL=1` 仅在 `ProbeResult.gpu=True` 时生效 | ✓ |
| 并行范围 | 未限定 | 仅限不同 GPU 设备（`CUDA_VISIBLE_DEVICES=0,1`） | ✓ |
| 设计-实现矛盾 | 声言「并行」但伪代码串行 | 伪代码仅选 `tier1_engines[0]`，无并行语法 | ✓ |

**伪代码一致性验证：**

```python
# 修订版 E4（line 560–570）
book_result = _run_book_engine(tier1_engines[0], pdf_path)  # ← 仅第一个引擎
```

仅检索 `[0]`，不遍历 Tier 1 列表。与「默认串行」声明一致。

**残留风险（P1）：** `SchedulerConfig` 中 `engine_parallel: bool = False` 字段定义了但伪代码中从未读取。若实施者直接抄伪代码，则 `engine_parallel` 配置会成为死字段，未来重新打开并行时需要额外重构。**建议在实施时在 `orchestrate_book()` 入口处增加断言：**

```python
if config.engine_parallel and not probe_result.has_gpu:
    logger.warning("engine_parallel ignored: no GPU detected")
    config.engine_parallel = False
```

---

### P0#2：B6 双闸缺失 → **已修复，但有实现残留**

**修复证据（v0.7 §6.3 + §6.2 伪代码）：**

修订版在编排主循环中显式加入了双闸：

```python
# 页数闸（line 540–542）
if page_num >= max_pages:
    break

# 时间闸（line 543–546）
elapsed = time.monotonic() - start_time
if elapsed > total_timeout:
    break
```

| 保护 | 第一轮状态 | 修订版 | 检查时机 |
|------|-----------|--------|---------|
| 页数上限 | 缺失 | `page_num >= max_pages: break` | 循环入口，渲染前截断 |
| 总时间预算 | 缺失 | `elapsed > total_timeout: break` | 每页 **渲染完成后** 检查 |

**与原版 `_run_vlm` 的对比：**

| 行为 | `_run_vlm` | 修订版 E4 | 一致？ |
|------|-----------|----------|--------|
| 页数截断时机 | render 前 | render 前 | ✓ |
| 时间检查时机 | 每页 render 后 | 每页 render 后 | ✓ |
| 断页后是否终止循环 | break | break | ✓ |
| 默认值 | 50 页 / 7200s | 50 页 / 7200s | ✓ |

**实现残留（P1 — 需实施时关闭）：**

`Budget.exhausted` 属性当前为死代码：

```python
# Section 6.3
@property
def exhausted(self) -> bool:
    return False  # 由外部循环管理
```

这意味着 Tier 2/3 入口处的 `not budget.exhausted` 和引擎选择循环内的 `if budget.exhausted: break` 永远不会触发。这有两个隐患：

1. **易误解的语义** —— 新维护者会困惑 `exhausted` 为何总返回 False
2. **未来如果重构为内部管理预算则缺少扩展点**

**建议方案：** 将 `exhausted` 改为 `_exhausted` 实例属性，由循环中的双闸在 break 前设置：

```python
# Budget 类
@dataclass
class Budget:
    ...
    _exhausted: bool = False

    def exhaust(self):
        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted

# 编排循环
if page_num >= max_pages:
    budget.exhaust()  # ← 设置标志
    break
if elapsed > total_timeout:
    budget.exhaust()  # ← 设置标志
    break
```

这样 `budget.exhausted` 在 Tier 2/3 内部的检查就能正确工作，同时保持「外部循环管理」的设计意图。

---

### P0#3：Benchmark 持久化策略未定义 → **已修复**

**修复证据（v0.7 §7.1 + §6.2 伪代码末尾）：**

| 维度 | 第一轮发现 | 修订版处理 | 状态 |
|------|-----------|-----------|------|
| 格式 | 未指定 | **NDJSON 追加式**（每行独立 JSON，行级追加） | ✓ |
| 写盘频率 | 未指定（`registry.record()` 隐含实时写） | **书完成后批量 flush**（`registry.persist_benchmarks()`） | ✓ |
| O(n²) 退化 | JSON 全文覆写则 O(n²) | 行级追加 = **O(1) 写**，禁止 JSON 全文覆写 | ✓ |
| 路径 | 未指定 | `$KZOCR_OUTPUT_DIR/benchmarks/` | ✓ |
| 线程安全 | 未涉及 | 复用 `kzocr/engines/atomic.py` 原子写入 | ✓ |
| 进程重建 | 未涉及 | 启动时从 benchmark 目录加载重建 `EngineStats` | ✓ |

**追加确认：**

- 单行约 100 字节，200 页 × 4 引擎 = 800 行 ≈ 80KB/书，写盘开销可忽略
- 内存 buffer 方案：进程中保持 EngineStats 实时更新 + 书结束时 flush，避免了每次 call 都写盘
- 原子写入确保部分写入不损坏已有数据

**无残留风险。** 该问题已经关闭，无需后续跟踪。

---

## 三、修订版引入的新问题

### N1：Tier 1 全书结果 list 化 + 无页级超时（P1）

**来源：** 修订版 §6.2 E4 伪代码

**问题 1A：`list(book_result.pages)` 全量物化**

```python
# line 568
tier1_pages = list(book_result.pages)
```

`book_result.pages` 被转换为 list，意味着全书所有页的结果同时驻留内存。若 `PageResult` 包含图像数据（crop、debug 图等），200 页可能额外消耗 50–200 MB。虽然文本场景下 < 1MB，但无约束保障。

**建议：** 若 `book_result.pages` 是序列（支持 `__getitem__` 和 `__len__`），可将 `list()` 改为直接引用，避免复制：

```python
tier1_pages = book_result.pages  # 假设已为 Sequence
```

如果一定要 list，在文档中注明 `PageResult` 不含图像数据。

**问题 1B：Tier 3 本地 LLM 调用无超时**

修订版全局时间闸在页循环入口检查，但单个 Tier 3 引擎调用（`_run_page_engine`）没有包裹超时。本地 LLM（shizhengpt）在最坏情况下可能挂起超过 120 秒而无中断机制。

上一轮建议（P1「引入页级超时 + Tier 3 超时熔断」）未被采纳：

| 建议 | 修订版处理 | 状态 |
|------|-----------|------|
| 单页整级超时 = 300s | 未处理 | ❌ |
| Tier 3 单独设 180s 超时 | 未处理 | ❌ |
| 超时后标记 UNCERTAIN 走 HumanGate | 未处理 | ❌ |

当前实现中，若某页 Tier 3 LLM 挂起 120s，加上 Tier 1 两引擎各 30s + Tier 2 10s，该页耗时 190s。**TOTAL_TIMEOUT = 7200s 意味着挂死 38 页就能耗尽全书预算。**

**建议：** 最低成本实现——用 `concurrent.futures` 包裹引擎调用：

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(_run_page_engine, engine, page_input.img)
    try:
        result = future.result(timeout=engine_timeout_seconds)
    except concurrent.futures.TimeoutError:
        logger.warning("[orchestrator] engine=%s timed out after %ds, moving to HumanGate",
                       engine.meta.name, engine_timeout_seconds)
        continue  # 或直接标记 UNCERTAIN
```

默认值建议：`KZOCR_ENGINE_TIMEOUT=120`（单位秒）。

---

## 四、三轮后性能风险汇总

| 风险 | 级别 | 描述 | v0.7 处理 | 本轮状态 |
|------|------|------|-----------|---------|
| ~~R1（原 P0）~~ | **已关闭** | Tier 1 并行反收益 | §6.4 默认串行 + GPU opt-in | ✓ **关闭** |
| ~~R2（原 P0）~~ | **已关闭** | B6 双闸缺失 | §6.2 伪代码加入页数/时间闸 | ✓ **关闭**（残留见 §2） |
| **N1** | **P1** | Tier 3 页级无超时熔断 | 未处理 | ❌ **新增** |
| ~~R4（原 P1）~~ | **已关闭** | Benchmark 持久化 | §7.1 NDJSON 追加 + 批量 flush | ✓ **关闭** |
| R3（原 P1） | **P1 → P2** | 三级兜底最坏延迟 190s/页 | TOTAL_TIMEOUT 全局兜底 | ⚠ 部分缓解（有全局闸但无页级熔断） |
| E1（新） | **P2** | Budget.exhausted 死代码 | `return False` 占位 | ⚠ 实施时需修复 |
| E2（新） | **P2** | `tier1_pages = list(...)` 全量物化 | 无约束 | ⚠ 若 PageResult 含图像则高 |
| R5（原 P2） | P2 → P3 | BookPipeline 双实例内存 | 已禁止并行，不适用 | ⚠ 残留期降低 |
| R6（原 P2） | **已关闭** | render_pages 非流式 | 伪代码标注「必须为生成器」 | ✓ |
| R7（原 P2） | **已关闭** | 资源过滤含外部调用 | §4.1 步 3 明确「仅读状态缓存」 | ✓ |
| R8（原 P3） | **已关闭** | KB 正则匹配 | §5.5 明确「哈希集/Trie，禁止正则」 | ✓ |

---

## 五、结论

### 三把锁解锁情况

| 第一轮「三把锁」 | 解锁状态 | 残留 |
|-----------------|---------|------|
| ①并行声言 vs CPU 争抢 | **已解锁** | ❌无 |
| ②动态路由 vs 双闸缺失 | **已解锁** | ⚠ Budget.exhausted 死代码 |
| ③三级兜底 vs 预算契约 | **部分解锁** | ❌无页级超时/Tier 3熔断 |

### 实施前必须处理的事项

1. **P1-N1：为 `_run_page_engine` 添加超时包裹**（推荐 120s 默认值），防止 Tier 3 挂死耗尽全书预算。
2. **P2-E1：修复 `Budget.exhausted`** 死代码为可设置的实例属性，使 Tier 2/3 内部 budget 检查实际生效。
3. **P2-E2：确认 `PageResult` 是否含图像数据**，若非则 `list()` 无风险；若是则改用直接引用。
4. **实施验证项：** 在 `orchestrate_book()` 入口增加 `engine_parallel` 有效性校验断言。

### 性能基线（更新）

| 指标 | v0.6（VLM-only） | v0.7 修订版预期 | 与上一轮变化 |
|------|-----------------|----------------|------------|
| 单页最优延迟 | ~30s | **~5s**（Tier 1 通过） | 同上一轮 |
| 单页最坏延迟 | 30s（无兜底） | ~190s（无页级超时） | 同上一轮 |
| 100 页最坏完成 | ~50min | 页级 190s × 100 = **~5.3h**（无熔断） | **新增：添加超时后降级为 100×120s = ~3.3h** |
| B6 双闸有效性 | 完整 | **完整**（修复后） | ↑ 提升（上一轮缺失 → 本轮完整） |
| 内存峰值 | 500–800MB | 500–1000MB | ↓ 降低（并行从默认关闭得益） |
| Benchmark 写盘 | 无 | NDJSON O(1) 追加 | ↑ 提升（新增） |
