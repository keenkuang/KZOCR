# PM 评审报告 — Round 3 (v0.7 详细设计文档)

| 字段 | 值 |
|------|-----|
| 评审对象 | `docs/plans/ocr-engine-unification.v0.7-DETAILED.md` |
| 评审角色 | 产品经理 |
| 日期 | 2026-07-10 |
| 前置评审 | Round 1 (PM 4 项必须修正 + 6 项建议) → Round 2 (已通过，进入详细设计) |

---

## 一、总体判断

**有条件通过，但 §12 迁移策略存在用户可感知的语义漂移，需修补后再进入实施。**

详细设计文档整体质量高，数据类定义完整、伪代码可执行、测试用例枚举充分，比 Round 1 的方案文档在实施细度上迈进了一大步。§10 CLI 扩展覆盖了 Round 1 要求的大部分用户控制能力，§12 也建立了旧配置兼容层。

但存在以下问题需要在实施前修正：

1. **REQUIRE_REAL 的语义在迁移过程中丢失**（P0 — 影响现有用户调试流程）
2. **SchedulerConfig 缺少 disabled_tiers 字段**（P0 — 代码与实际不一致）
3. **VLM_ENGINE 废弃策略缺少用户告知机制**（P1）
4. **benchmark 子命令功能不完整**（P2 — 缺少主动评估能力）
5. **进度反馈 / ETA 在详细设计中被遗漏**（P2 — Round 1 要求，但未体现在 DETAILED 文档中）

---

## 二、§10 CLI 扩展评审

### 2.1 pipeline 新增参数（5 个）

| 参数 | 详细设计中的体现 | Round 1 要求 | 状态 |
|------|----------------|-------------|------|
| `--engine <name>` | §10.1 实现，强制指定引擎跳过调度器 | ✅ 是（3.2 建议） | ✅ 通过 |
| `--prefer speed/accuracy` | §10.1 实现，speed 按延迟排序，accuracy 按通过率排序 | ✅ 是（3.2 建议） | ✅ 通过 |
| `--tier-order "1,3,2"` | §10.1 实现，自定义降级顺序 | ✅ 是（3.2 建议） | ✅ 通过 |
| `--tier-limit N` | §10.1 实现，限制最大兜底级数 | ✅ 是（4.2 建议） | ✅ 通过 |
| `--max-time-per-page N` | §10.1 实现，单页最大时间 | ✅ 是（3.3 D 建议） | ✅ 通过 |

**2.1.1 确认覆盖**：5 个参数全部覆盖了 Round 1 要求的核心用户控制能力，且设计合理。`--engine` 的覆写优先级高于调度器，符合"用户手动指定高于自动调度"的原则。

**2.1.2 改进建议：提示信息中说明参数组合的优先级**

用户可能同时指定 `--engine sensenova --prefer speed`。当前设计是 pinned_engine 直接返回单引擎，prefer 被忽略。建议 CLI help 中明确标注参数优先级：

```
参数优先级（高→低）：
  --engine > --tier-order > --tier-limit > --prefer > 调　度器默认
```

否则用户设了 `--engine sensenova --prefer speed` 发现 --prefer 没生效会困惑。

### 2.2 benchmark 子命令

**问题 2.2.1：只有 list/show，缺少主动评估能力（P2）**

Round 1 建议的 benchmark 命令功能矩阵：

| 功能 | Round 1 建议 | 详细设计 | 差距分析 |
|------|-------------|---------|---------|
| 查看引擎健康状态 | `benchmark status` | `benchmark list` | ✅ 命名差异，功能等价 |
| 查看指定引擎详情 | `benchmark history --engine vlm` | `benchmark show sensenova` | ✅ 功能等价 |
| 主动跑 benchmark | `benchmark run` | ❌ 未实现 | **缺失** |
| 重置历史数据 | `benchmark reset` | ❌ 未实现 | **缺失** |

主动 benchmark 能力对用户场景有价值：

```
# 管理员想在新机器上评估各引擎表现
kzocr benchmark run --test-set docs/benchmarks/test_set/ --pages 5
```

缺失这个能力，用户只能在跑实际书籍时被动积累数据。如果调度器需要"至少 3 页历史数据"才能稳定排序（见 §2.2），新部署环境的前几本书实际上是在"盲跑"。

**建议：** 如果 v0.7 时间紧张，benchmark run 可以推迟到 v0.8，但应在文档中注明已知缺口，并给出替代方案（如"首次部署建议先用真实数据跑至少 3 本书以建立调度器基线"）。

### 2.3 进度反馈（P2 — Round 1 要求在详细设计中未被落实）

Round 1 §3.3 建议 C 明确要求了进度条和 ETA 估计：

```
[12:30:15] 正在处理第 23/48 页 | 用时 3m12s | 预计剩余 3m45s
```

详细设计 §7.1 的编排主循环中，每页只在日志输出调度决策，未实现进度反馈。Round 2 PM 确认"进度条提及"已修复，但详细设计中未见具体实现代码或格式定义。

**影响评估：** 三级兜底下最坏情况每页 40-70 秒、100 页书可能等 2 小时，没有进度反馈的用户体验是"卡住了"。这不是可选的 UX 改进，而是处理长时间等待场景的基本要求。

**建议：** 在 `orchestrate_book()` 主循环的每页开头加入进度日志，格式可参考：

```python
# 每 5 页或每页耗时 > 10s 时输出
_progress_log(page_num, total_pages, elapsed, remaining_estimate, engine_stats)
```

**近期输出：（**每页都打**
```
(12:30) P 12/48 | 用时 3m12s | 预计 14m
```

**远期输出：（**每 5 页打，类似当前 `_run_vlm` 的行为**
```
[12:30] P 12/48 [████████░░░░] 进度 25% | ETA 14m | Tier1: 10 PASS, 2 T2
```

---

## 三、§12 迁移策略评审

### 3.1 旧配置兼容映射表评审

| 旧变量 | 映射行为 | 风险评估 |
|--------|---------|---------|
| `KZOCR_USE_MOCK=1` | 保留短路，不走调度器 | ✅ 安全 |
| `KZOCR_USE_VLM=1` | 映射为 `disabled_tiers=[1]` | ⚠️ 见 3.1.1 |
| `KZOCR_REQUIRE_REAL=1` | 映射为 `tier_limit=1` | 🔴 见 3.1.2 |
| `KZOCR_VLM_ENGINE=auto` | v0.7 废弃，由调度器管理 | ⚠️ 见 3.1.3 |

#### 3.1.1 USE_VLM → disabled_tiers：配置字段缺失（P0，阻塞级）

§12.1 的委派代码写的是：

```python
config_overrides = SchedulerConfig(disabled_tiers=[1])
```

但 §9.1 定义的 `SchedulerConfig` 字段列表中**没有 `disabled_tiers` 字段**。当前字段只包含 `max_tierN_engines`、`tier_limit` 等，没有任何"禁用某级引擎"的配置项。

**修正方案 A**：在 SchedulerConfig 中新增 `disabled_tiers: list[int] = field(default_factory=list)`，供调度器 `select_candidates()` 在层级约束步过滤。

**修正方案 B**：用现有字段组合表达——`tier_limit=1` 且 `engine_parallel=False`，但行为不等价：USE_VLM 的意图是"跳过 Tier 1 的书级引擎，只走 Tier 2/3 页级引擎"，而 tier_limit=1 是"只走 Tier 1"。两者语义相反。

**必须选 A**，无可行替代方案。

#### 3.1.2 REQUIRE_REAL → tier_limit=1：语义漂移（P0，用户可感知的 bug）

**当前（v0.6）`KZOCR_REQUIRE_REAL=1` 的语义**：真实引擎失败时**抛异常**，不降级到 mock。用户使用此模式是为了 debug——"我要知道引擎为什么失败了，别吞错误"。

**v0.7 映射 `tier_limit=1` 后的实际行为**：只走 Tier 1，如果 Tier 1 失败，**不抛异常，直接进入 HumanGate**（失败页标记），全书继续跑剩余页。

对用户的可感知影响：

```
# v0.6: 一本书第 3 页引擎崩了
→ 直接抛出异常，进程退出，用户看到 traceback

# v0.7: 同样场景
→ 第 3 页标记为 failed_pages，全书跑完
→ 最终输出 "3/50 页失败"，用户不知道引擎崩了
→ 用户可能以为只是 OCR 效果不好，实际上引擎根本就没工作
```

这是**不可接受的语义漂移**。原本用于 debug 的模式，v0.7 下变成了静默吞错。

**修正方案**：对 `REQUIRE_REAL=1` 需要两层映射：

```python
if config.require_real:
    overrides = SchedulerConfig(tier_limit=1, fail_on_no_pass=True)
    return orchestrate_book(pdf_path, book_code, config, overrides)
```

并在编排层增加检查：如果所有页的 Tier 1 全部失败 → 抛 `AllEnginesFailedError`（而不是静默返回带 failed_pages 的 BookResult）。

或者，如果不想在 v0.7 增加新机制，更安全的做法是：

```python
if config.require_real:
    # 保持原有语义：走调度器，但失败时抛异常
    result = orchestrate_book(pdf_path, book_code, config, overrides)
    if len(result.failed_pages) > 0:
        raise RuntimeError(f"REQUIRE_REAL: {len(result.failed_pages)} pages failed")
    return result
```

#### 3.1.3 VLM_ENGINE 废弃：用户无感知 （P1）

KZOCR_VLM_ENGINE=auto/sensenova/paddleocr_vl16 是用户控制 VLM 引擎偏好的唯一途径。v0.7 废弃此变量后：

- 用户之前 `VLM_ENGINE=sensenova`（偏好云端高精度）→ 调度器可能因延迟数据选 paddleocr_vl16
- 用户之前 `VLM_ENGINE=paddleocr_vl16`（偏好本地避免出境）→ 调度器可能因成功率数据选 sensenova
- **没有任何告警**告诉用户"你设的 VLM_ENGINE 已经不再生效了"

**建议：**
1. v0.7 对 KZOCR_VLM_ENGINE 保留兼容：如果用户显式设了非 auto 值，将该引擎设为 `pinned_engine`（等价于 `--engine sensenova`）
2. 或至少打一条警告日志：

```python
if os.environ.get("KZOCR_VLM_ENGINE", "auto") != "auto":
    logger.warning(
        "[config] KZOCR_VLM_ENGINE=%s 已在 v0.7 废弃，引擎选择由调度器管理。"
        "如需指定引擎请使用 kzocr pipeline --engine %s",
        vlm_engine, vlm_engine,
    )
```

### 3.2 用户感知的变更：CLI 用法检查

**现有 CLI 用法是否变化？**

| 命令 | v0.6 | v0.7 | 用户感知 |
|------|------|------|---------|
| `kzocr pipeline <pdf>` | 走 run_engine → if-else | 走 run_engine → orchestrate_book | **基本不变**（输出增加引擎报告日志） |
| `kzocr pipeline <pdf> --book-code XXX` | 同上 | 同上 | **无变化** |
| `kzocr smoke` | mock 引擎短路返回 | mock 引擎保持短路（§12.1） | **无变化** ✅ |
| `kzocr export <code>` | 不变 | 不变 | **无变化** |
| `kzocr push <file>` | 不变 | 不变 | **无变化** |

**新参数对现有脚本的影响：**
- 所有新参数 (`--engine`, `--prefer` 等) 是可选的，不加时走调度器默认行为
- 现有 `kzocr pipeline <pdf>` 调用不会因新增参数而中断
- ✅ 向后兼容性良好

**需要关注的点**：用户如果设了 `KZOCR_USE_VLM=1`，v0.7 映射为禁 Tier 1。但由于 3.1.1 指出的 `disabled_tiers` 字段缺失，这个映射在现有代码中不会生效，USE_VLM 退化为无条件走调度器——这是 Run-time 问题而非 CLI 签名问题。

### 3.3 配置项废弃时间线

| 配置项 | 详细设计 v0.7 状态 | 详细设计 v0.8 预期 | 评估 |
|--------|-------------------|-------------------|------|
| `KZOCR_USE_VLM` | 兼容（映射为 disable T1） | 移除，警告 | ✅ 合理 |
| `KZOCR_REQUIRE_REAL` | 兼容（映射为 tier_limit=1） | 移除，警告 | ⚠️ 映射行为需要修正（见 3.1.2） |
| `KZOCR_VLM_ENGINE` | 废弃（调度器接管） | 移除 | ⚠️ v0.7 直接废弃太激进，建议 v0.7 先兼容/警告，v0.8 再移除 |

**时间线合理性评估：** 总体合理，但 `KZOCR_VLM_ENGINE` 从 v0.7 直接废弃（无兼容层）对用户不够友好。它不像 USE_VLM/REQUIRE_REAL 有 True/False 的映射语义——VLM_ENGINE 携带的是**引擎偏好值**（auto/sensenova/paddleocr_vl16），直接忽略值意味着用户偏好被丢弃。

**建议时间线调整：**

```
v0.7:
  - USE_VLM: 兼容（映射为 disabled_tiers=[1]），日志 INFO 提示"将在 v0.8 移除"
  - REQUIRE_REAL: 兼容（映射为 fail_on_no_pass），日志 INFO 提示
  - VLM_ENGINE: 兼容（非 auto 时映射为 --engine 等效），日志 WARNING 提示"将在 v0.8 移除，请改用 --engine"

v0.8:
  - USE_VLM: 移除，日志 WARNING
  - REQUIRE_REAL: 移除，日志 WARNING
  - VLM_ENGINE: 移除
```

---

## 四、未覆盖的 Round 1 要求检查

Round 1 PM 评审的 4 项"必须修正"和 6 项"强烈建议"在详细设计中的落实情况：

### 4.1 4 项必须修正（Round 2 确认全部修复）

| # | 项目 | Round 2 状态 | 详细设计体现 | 评审 |
|---|------|-------------|-------------|------|
| 1 | 引擎报告（每次 pipeline 输出引擎使用情况） | ✅ 已修复 | §7.4 引擎报告日志格式 | ✅ |
| 2 | 手动指定高于调度器（--engine, --prefer） | ✅ 已修复 | §10.1 实现 | ✅ |
| 3 | 进度汇报（进度条 + ETA） | ✅ 已修复 | **未见具体实现** | ⚠️ 见 2.3 |
| 4 | mock 引擎不走调度器 | ✅ 已修复 | §12.1 use_mock 短路 | ✅ |

**关于 #3 的说明**：Round 2 PM 确认"进度条提及"已修复，但 Round 2 评审的是修订版方案文档（`ocr-engine-unification.v0.7.md`），该文档确实提及了进度反馈。但**详细设计文档**（DETAILED 版本）中，§7 的编排主循环伪代码并没有包含任何进度日志代码，§11 的测试用例也未包含进度相关测试。这需要补充。

### 4.2 6 项强烈建议

| # | 项目 | 详细设计体现 | 状态 |
|---|------|-------------|------|
| 5 | `--prefer speed/accuracy` | §10.1 实现 | ✅ |
| 6 | `--max-time-per-page` | §10.1 实现，§7.5 B6 双闸中默认 120s | ✅ |
| 7 | `--tier-limit` | §10.1 实现，§9 SchedulerConfig 默认 3 | ✅ |
| 8 | 初始权重 + 轮询调度防止冷启动 | §2.2 前 3 次预设优先级 + §2.3 5% 轮询采样 | ✅ |
| 9 | allow_cloud_vision 与调度器联动 | §4.1 第 4 步过滤云端引擎 | ✅ |
| 10 | 向后兼容旧式环境变量 | §12 迁移策略 | ⚠️ 见三 |

### 4.3 Round 1 其他建议未落实情况

| 建议 | 来源 | 状态 | 说明 |
|------|------|------|------|
| `--engine-report <json>` 参数 | §3.1 建议 B | ❌ 未实现 | 可选，可推迟到 v0.8 |
| benchmark run（主动评估） | §3.4 | ❌ 未实现 | 见 2.2.1 |
| benchmark reset | §3.4 | ❌ 未实现 | 低优先级 |
| GlyphStatus 用户文档 | §3.6 | ❌ 未实现 | 应在 CLI help 或 README 中说明 |
| 重跑确定性（--deterministic） | §4.1 | ❌ 未实现 | 低优先级 |
| `--engine-weight` 人工调权 | §4.3 | ❌ 未实现 | 低优先级 |

---

## 五、其他产品问题

### 5.1 kzocr pipeline --tier-order 的输入格式

§10.3 解析 `--tier-order "1,3,2"` 为 `[1, 3, 2]`。但在编排主循环（§7.1）中，代码是**硬编码顺序** Tier 1 → Tier 2 → Tier 3，没有读取 `overrides.tier_order` 的逻辑。

```
编排循环实际执行顺序（§7.1 伪代码）：
  Tier 1（全书）→ Tier 2（逐页）→ Tier 3（逐页）

如果用户设了 --tier-order "1,3,2"：
  期望顺序：Tier 1 → Tier 3 → Tier 2
  实际顺序：Tier 1 → Tier 2 → Tier 3（因为 overrides.tier_order 没有被使用）
```

**建议**：确认 tier-order 的实施计划。有两种实现方式：
1. 编排循环按 tier_order 顺序迭代（简单直接）
2. 仅在 select_candidates 中做额外排序（更保守）

如果是方案 1，需要在编排伪代码中加入 tier 顺序的循环逻辑。

### 5.2 kzocr pipeline 新参数是否影响 kzocr smoke

kzocr smoke 内部调用 `run_engine("mock.pdf", ...)` 且 `use_mock=True`。由于 mock 模式保持短路不经过调度器，新参数不会影响 smoke。✅

但需要考虑：用户能否在 smoke 中验证调度器行为？不能直接做到。建议在 smoke 模式下新增 `--with-scheduler` 参数，启用调度器 + mock 引擎来测试调度链路是否正常，作为集成测试的补充。

### 5.3 引擎报告日志的用户可达性

§7.4 的引擎报告以 `logging.INFO` 输出。对于非交互式用户（CI 环境、自动化脚本），日志输出是显式的。

但对于终端交互用户，如果日志级别被调高（如 `logging.WARNING`），引擎报告将不可见。建议引擎报告使用标准的 `print()` 或专用的 `logging.user_info` 级别，确保用户即使在 WARNING/ERROR 级别下也能看到结果摘要。

---

## 六、总结

### 6.1 阻塞问题（必须修复才能进入实施）

1. **SchedulerConfig 缺少 disabled_tiers 字段**（3.1.1）—— USE_VLM 兼容代码会运行时报错
2. **REQUIRE_REAL 语义漂移**（3.1.2）—— debug 模式变成静默吞错，用户投诉必然

### 6.2 强烈建议

3. VLM_ENGINE 废弃加日志警告（3.1.3）
4. 编排主循环加入进度日志（2.3）
5. tier-order 在编排循环中的实际使用确认（5.1）

### 6.3 可推迟到 v0.8

6. benchmark run 主动评估（2.2.1）
7. --engine-report JSON 输出（4.3）
8. --deterministic 模式（4.3）
9. CLI help 优先级说明（2.1.2）
