# KZOCR v0.7 E1 实现评审 — 测试（testing）

- **评审角色**：测试
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`tests/test_registry.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择；probe/benchmark/状态转换/去重实现未达，列**覆盖缺口**而非本 E1 阻塞。

---

pytest 实际结果：`10 passed in 0.07s`，无失败。对照 §10.1，当前 10 用例已覆盖：数据类构造、冷启动默认值、record 统计与派生、glyph 计数、未注册 KeyError、tier 过滤排序、prefer 三路径、空 tier。缺：probe 探测、benchmark save/load/append、状态转换、去重（属 E1 范围未达，列缺口）。

## 1. 【阻塞 / 必须修复】

- 位置 `registry.py:140`（`_bayesian_score` 的 `1.0 / latency`）；依据 §3.5/§4.3。现象：当引擎 `total_pages>0` 且 `total_latency_ms==0` 时，`avg_latency_per_page_ms`（`registry.py:63-65`）返回 0，默认 `select_candidates` 走贝叶斯路径即 `ZeroDivisionError`。`record` 缺省 `latency_ms=None`，仅 `record(name, pages=5)` 即可触发——极常见路径却无用例。建议补：注册引擎后 `reg.record("x", success=True, pages=5)`（不传 latency），再 `select_candidates(reg, tier)` 验证不崩溃且排序正确。

## 2. 【重要 / 强烈建议】（§10.1 覆盖缺口）

- 去重：`register`（`registry.py:74-76`）同名覆盖无验证。建议：同 name 两次 `register_adapter`，断言后者覆盖、`list()` 数量不变。
- probe 探测（全部/部分/零可用）：§10.1 明确列出，E1 未实现 → 排期后续。
- benchmark save/load/append：缺口，排期后续。
- 状态转换 `mark_unavailable` / `EngineStatus` 流转：缺口，排期后续。

## 3. 【优化 / 可选】

- 断言质量：仅 1 个错误路径用例（`test_record_unknown_engine_raises`）。建议补 `select_candidates` 同分稳定性断言（§4.3 声明稳定排序），构造两引擎评分/延迟相同验证顺序。
- glyph 语义：`test_record_unknown_and_fail_counts` 已覆盖 FAIL+UNKNOWN 计入分母；但缺 PASS 与 UNKNOWN 混合（通过率应 = pass/(pass+unknown)）用例，建议补 PASS1+UNKNOWN1→0.5。
- mock 约定：当前测试纯构造 `AdapterMeta`、无真实引擎/网络，符合「外部依赖必须 mock」，是合格单元测试 ✓。
