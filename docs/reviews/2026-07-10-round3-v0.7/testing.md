# v0.7 测试策略评审报告

> 评审角色：测试工程师
> 评审对象：`docs/plans/ocr-engine-unification.v0.7-DETAILED.md §11`
> 日期：2026-07-10

---

## 1. 新测试用例覆盖率分析

### 1.1 test_registry.py — 14 例 → **建议 +4 例**

| 现有覆盖 | 判断 | 说明 |
|---------|------|------|
| 数据类构造 / 派生字段 / 冷启动默认值 / 贝叶斯公式 / 衰减 | ✅ 充分 | | | probe 三场景（空/全/部分） | ✅ 充分 |
| benchmark save/load/append / 进程重启 / 时间窗口 / 损坏行 | ✅ 充分 |
| 状态转换 / 去重注册 | ✅ 充分 |

**补充建议：**

| # | 建议补充用例 | 理由 |
|---|------------|------|
| 15 | `test_get_by_tier_edge` | `get_by_tier(non_existent_tier)`、`get_by_tier(0)` 边界未覆盖 |
| 16 | `test_record_glyph_combinations` | `record()` 的各种 glyph_status 组合（None/PASS/FAIL/UNKNOWN/UNCERTAIN）对不同计数器的累加正确性 |
| 17 | `test_repr_masks_sensitive` | `__repr__` 掩码设计（第 1.2 节）无测试验证——关键安全设计应可测试 |
| 18 | `test_registry_concurrent_safety` | `_lock` 为线程安全预留，但无并发测试验证。建议至少加 `concurrent.futures` 简单测试 |

### 1.2 test_scheduler.py — 17 例 → **建议 +4 例**

| 现有覆盖 | 判断 | 说明 |
|---------|------|------|
| 空注册表 / 冷启动预设优先级 / 贝叶斯默认 | ✅ |
| 排序确定性 / Tier 过滤 / 竖排跳过 T1 / 云引擎过滤 / 过滤 UNAVAILABLE / Top-N 限制 | ✅ |
| domain_adjust 三场景 | ✅ |
| 轮询采样 / pinned_engine / prefer speed/accuracy / pinned unavailable error | ✅ |

**补充建议：**

| # | 建议补充用例 | 理由 |
|---|------------|------|
| 18 | `test_select_candidates_empty_tier` | 指定 tier 但该 tier 无注册引擎的场景 |
| 19 | `test_select_candidates_with_overrides` | `tier_order`、`tier_limit`、`max_time_per_page` 覆盖参数对 select_candidates 输出的影响（当前只测了 prefer 和 pinned）|
| 20 | `test_domain_adjust_default_adjustments` | 非竖排 / 非激光 / 非方剂书的常规路径——domain_adjust 应返回 base_score 无偏移（空调整路径漏测，回归风险） |
| 21 | `test_score_overflow` | 极端数据（超大 latency、pass_rate=0、pass_rate=1.0）下 `_score` 不崩溃 |

### 1.3 test_verifier.py — 17 例 → **建议 +5 例**

| 现有覆盖 | 判断 | 说明 |
|---------|------|------|
| 5 个检测器各自独立测试 | ✅ | |
| 短路逻辑 / 全 PASS / RARE 无 FAIL / 空知识库 / 全 disable / 优先级顺序 / enable/disable | ✅ |

**补充建议：**

| # | 建议补充用例 | 理由 |
|---|------------|------|
| 18 | `test_verify_short_circuit_toxin_critical` | 用例 11 只验证了 ToxinDose FAIL 短路，但 5.4 节代码第 890 行区分了 **普通 FAIL** vs **`severity=critical` 的 FAIL**——两者短路行为不同（critical 立即返回，非 critical 继续收集）。当前 17 例未覆盖此区分 |
| 19 | `test_verify_aggregate_unknown` | 混合 RARE + UNKNOWN → 编排循环需要判断是否降级。当前测试只覆盖了纯 PASS、纯 RARE，未覆盖聚合逻辑中 `has_fail=True` 或 `has_unknown=True` 分支（代码第 909-912 行） |
| 20 | `test_verify_aggregate_fail_no_critical` | 非 critical FAIL + 其他检测器通过 → verify 应返回 UNKNOWN（非 PASS/非 RARE）。这个分支是编排循环降级判断的输入，漏测风险高 |
| 21 | `test_detector_performance_budget` | §5.5 明确约定 `verify()` < 50ms。应有性能测试（可用 `@pytest.mark.slow`），至少确保空知识库 + 短文本时 < 10ms |
| 22 | `test_toxin_dose_regex_safety` | 第 751 行 `re.escape(herb)` 值得点赞。但应加正则 DoS 测试：超长文本（10KB+）下 ToxinDose 线性扫描是否超时 |

### 1.4 test_orchestrator.py — 9 参数化 + 6 额外 = 15 例 → **建议 +5 例**

| 现有覆盖 | 判断 | 说明 |
|---------|------|------|
| 8 种兜底路径参数化 | ✅ 充分 | |
| B6 双闸 / 引擎崩溃 / VLM 缓存 / registry.record 调用 / 最小 mock 集成 | ✅ |

**补充建议：**

| # | 建议补充用例 | 理由 |
|---|------------|------|
| 16 | `test_empty_book_code` | 全流程中 `book_code=None` 的路径（日志格式、benchmark 文件命名） |
| 17 | `test_pages_text_length_mismatch` | `_build_pages_result()` 中 `len(pages_text) != len(tier1_result.pages)` 的场景——设计文档第 1277 行假设 `tier1_result` 存在且长度匹配 |
| 18 | `test_tier2_exception_graceful` | Tier 2 引擎抛异常 → `continue`（第 1167-1170 行），但断言的异常类型范围（除 EgressBlockedError 外所有 Exception）。应至少测 AIOError / ConnectionError / ValueError 各一种——它们是大概率真实异常 |
| 19 | `test_failed_ratio_critical` | §7.1 第 1249 行的 30% 失败率告警——通过 mock 构造 16 页失败 6 页（ratio=0.375）验证 `_logger.error` 被调用 |
| 20 | `test_engine_parallel_opt_in` | `engine_parallel=True` + 有 GPU vs 无 GPU 的分支（第 1064 行）——当前无覆盖 |

### 1.5 test_regression.py — 5 例 → **建议 +3 例**

| 现有覆盖 | 判断 | 说明 |
|---------|------|------|
| mock shortcut / VLM→disabled_tier1 / 默认委派 / 旧配置兼容 / require_real 保留 | ✅ 基本充分 |

**补充建议：**

| # | 建议补充用例 | 理由 |
|---|------------|------|
| 6 | `test_use_vlm_and_require_real_conflict` | 两旧配置同时启用时迁移委派的冲突处理 |
| 7 | `test_run_engine_missing_benchmark_dir` | `benchmark_dir` 不存在时 `orchestrate_book()` 的 fallback 行为 |
| 8 | `test_golden_output_consistency` | 同一个 mock 输入 → 新旧两条路径输出相同 BookResult。这是回归门禁的核心用例，当前 5 例只覆盖了委派关系，未验证输出等价性 |

---

## 2. conftest.py 共享 fixture 评审

### 2.1 现有 5 个 fixture 总体评价

| fixture | 用途 | 问题 |
|---------|------|------|
| `mock_all_engines_available` | 全mock 9 引擎 | 描述说返回 `list[EngineRegistration]`，但 `select_candidates()` 接受 `EngineRegistry`，`orchestrate_book()` 需要 `EngineRegistry`——各测试需自行转换。建议改为返回 `EngineRegistry` |
| `mock_only_tier1_engines` | 仅 Tier 1 可用 | 同上 |
| `sample_engine_stats` | 已知排序 fixture | 设计 OK。建议加文档注释说明 EngineA→EngineB 的排序预期 |
| `frozen_time` | 固定时间戳 | **问题：** 使用 `patch("time.time", ...)` 全局 patch。需确认被测代码使用 `import time; time.time()` 而非 `from time import time`。后者无法被此 patch 拦截。建议同时 patch `time.time` 和 `kzocr.scheduler.utils.time.time`（如果代码用了模块级 import） |
| `tmp_benchmark_dir` | benchmark 临时目录 | 设计 OK |

### 2.2 建议新增 fixture

| 建议 fixture | 用途 | 优先级 |
|-------------|------|--------|
| `populated_registry` | 预填充 EngineStats 的 EngineRegistry（用于 warm start 测试）| **高** |
| `sample_page_info` | 各类 PageInfo 工厂（`book_type=tcm_ancient`, `pub_era=laser`, `is_vertical` 组合） | **高** |
| `detector_context_factory` | DetectorContext 便捷构造器 | 中 |
| `mock_probe_result` | probe() 返回值的 mock（GPU/VRAM/端口/keys 可控） | **高** |
| `mock_engine_registry_no_stats` | 注册引擎但 EngineStats 全为零（冷启动场景） | 中 |

---

## 3. 集成测试检查清单评审

### 3.1 现有清单覆盖

| 集成场景 | 覆盖状态 | 评价 |
|---------|---------|------|
| probe→registry 真实流程 | **需补充**（标注正确） | 这是编排入口，不应留空。最低要求：1 个集成测试通过 mock_config 触发 probe→registry→get_all 验证 |
| scheduler 排序→orchestrator 选择 | 已覆盖 | 参数化 8 路径覆盖好 |
| verifier 真实判断→orchestrator 降级 | 已覆盖 | |
| persist_benchmarks→下次加载评分 | 已覆盖 | roundtrip 测试覆盖 |
| 引擎真实异常→scheduler 标记 UNAVAILABLE | 已覆盖 | engine_crash 用例 |

### 3.2 建议补充的集成场景

| # | 集成场景 | 建议测试文件 | 理由 |
|---|---------|------------|------|
| 6 | benchmark NDJSON → 启动加载 → 影响 scheduler 排序 | `test_registry.py` 或新建 `test_integration.py` | 当前 roundtrip 只验证了存/读的文件一致性，未验证 load 后的 EngineStats 能否正确影响 `select_candidates` 排序 |
| 7 | CLI 参数 → EngineOverrides → orchestrate_book | `test_cli.py` 或 `test_orchestrator.py` | 第 10 节 CLI 扩展（`--prefer`, `--tier-order` 等）无集成测试验证 CLI 参数到编排结果的全链路 |
| 8 | 多本书连续运行 → benchmark 累加 | `test_registry.py` | 模拟 3 本书依次处理，验证 NDJSON 中累计了 3 本书的记录，且下次启动加载正确 |
| 9 | benchmark 文件 >100MB 截断 | `test_registry.py` | §8.5 容量管理策略无任何测试 |
| 10 | probe 中 API key 安全检查 | 新建 `test_security.py` 或 `test_registry.py` | §3.3 要求 ProbeResult.keys 改为 `dict[str, bool]`——无测试验证不会意外泄露 key 明文 |

---

## 4. 回归门禁（现有 268 测试）可行性评估

### 4.1 当前测试基线

当前仓库 `tests/` 目录下有 **17 个测试文件、21 个测试用例**。如"268"是其他分支或后续扩展后的预期总量，那么回归门禁设计需考虑以下问题：

### 4.2 关键风险

| 风险 | 影响 | 建议 |
|------|------|------|
| **旧测试依赖旧入口** | 现有 21 例覆盖 `_run_vlm`、`BookPipeline` 等旧路径。`run_engine()` 委派模式改造后，旧路径入口可能被重写或调用链变化 | 旧测试应仍能通过——委派模式确保 `run_engine()` 保持签名兼容。但需加 CI 步骤：`git stash && pytest tests/ -v` 在 **无 v0.7 代码变更** 的基线先跑通全绿 |
| **268 测试的组成不透明** | 设计文档提到"268 测试"但未说明构成（单元/集成/端到端比例）。当前实际只有 21 例，差异过大 | 建议先做测试基线盘点，明确 268 的来源（是否包含其他模块如 khub 的测试） |
| **回归门禁粒度不足** | `test_regression.py` 只覆盖 5 个委派路径，等价于只验证了"入口没换"，未覆盖"输出没变" | 建议增加 golden data 机制：为 `KZOCR_USE_MOCK=1` 录制一组标准输出的 hash 或 snapshot，每次 CI 验证 hash 未变 |

### 4.3 回归门禁实施建议

```
回归门禁规则（建议加入 CI）：
1. `pytest tests/ -v --tb=short` 必须全绿          → 单元 + 回归通过
2. 新增测试用例覆盖率 >= 90%（新代码行）             → `pytest --cov=kzocr.scheduler --cov=kzocr.orchestrator`
3. golden data 一致性校验（mock 模式输出 hash 不变）  → 新增脚本 `tests/golden/check.sh`
4. 所有 benchmark 测试不写死 `/tmp`，使用 `tmp_path` → 防 CI 环境冲突
```

---

## 5. 总体评分与建议优先级

| 维度 | 评分 | 说明 |
|------|------|------|
| 测试用例覆盖面 | **B+** | 核心路径覆盖良好，边缘场景约 15-20% 缺口 |
| fixture 设计 | **B** | 基础 5 个 OK，但缺注册表/PageInfo/ProbeResult 等高频 fixture，导致各测试重复构造 |
| 集成测试清单 | **B** | 5 个场景中 1 个待补充，另缺少 5 个重要集成场景 |
| 回归门禁设计 | **C** | 5 个委派测试不足以支撑 268 测试的回归保证。需补充 golden data 和覆盖率门禁 |
| 安全测试 | **C** | API key 掩码、ProbeResult 类型变更 均无测试验证——安全设计应提前引入测试 |
| 性能测试 | **D** | verify <50ms、轮询开销、大注册表排序性能——完全无测试 |

**建议修复优先级：**

1. **P0（实施前必须解决）：** 补充 `frozen_time` 的 import 方式验证、`populated_registry` fixture、golden data 回归校验
2. **P1（Phase 1 测试前解决）：** test_registry 补充 4 例、probe→registry 集成测试
3. **P2（Phase 2 测试前解决）：** test_scheduler 补充 4 例、test_verifier 补充 5 例
4. **P3（Phase 3 前解决）：** test_orchestrator 补充 5 例、集成测试清单补全 5 场景
5. **P4（v0.8 前考虑）：** 性能测试、安全测试专用文件
