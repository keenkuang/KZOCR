# KZOCR v0.7 E1 实现评审 — 架构师（architect）

- **评审角色**：架构师
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`，分支 `feat/v0.7-registry`）、`tests/test_registry.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择；竖排/egress/衰减/预算/overrides 属 E2/E4 未实现，**不计入本 E1 阻塞**。

---

## 1. 【阻塞 / 必须修复】

**`record()` 的 `glyph` 类型契约已断裂**（`registry.py:102-126` vs 设计 §6.2）
- 现象：`glyph: Optional[str]`，用 `if glyph == "PASS"` 字面比较；但 §6.2 编排层调用 `registry.record(…, glyph=verdict)` 传的是 `GlyphVerdict` 对象。E2 接入后 `verdict == "PASS"` 恒为 False，**字形计数静默失效**。
- 同时 `GlyphStatus` 有 5 值（PASS/RARE/UNKNOWN/FAIL/UNCERTAIN），当前仅处理 3 值，RARE/UNCERTAIN 既不计入成功也不计入分母，`glyph_pass_rate` 失真。
- 建议：参数改为 `Optional[GlyphStatus]`（或 `GlyphVerdict` 取 `.status`）；PASS/RARE→pass、FAIL/UNKNOWN→fail、UNCERTAIN→unknown，与设计成功集（§6.2: PASS/RARE）对齐。

## 2. 【重要 / 强烈建议】

- **`adapter` 应为 `Optional[EngineRunner]`**（`registry.py:46` vs §2.2）。`EngineRunner` 协议已在 `types.py` 定义，改用它可在静态期约束 Orchestrator 未来调用，防止误注入非协议对象。
- **`config` 与 `EngineConfig` 契约冲突**（`registry.py:43/78` vs `types.py` 中 `EngineConfig` 注释「替代裸 dict」）。实现用裸 `dict`，且 §6.2 用 `engine.config.get("base_url")`（仅 dict 可用）。两处真相冲突，E2 必二选一。建议以 `EngineConfig` 为准并改 §6.2 为 `engine.config.base_url`，或显式否决 `EngineConfig`。
- **`select_candidates` 缺 E2 扩展预留点**（`registry.py:143-162` vs §4.2/§4.5）。演进到 E2 的 `(registry, tier, page, budget, overrides)` 将改签名、波及所有调用方。建议：`registry, tier, *, page=None, budget=None, overrides=None, prefer=None` 关键字预留；`decay` 预留为 `_bayesian_score(reg, decay_fn=None)` 钩子，E2 仅注入 `decay(last_seen)`。

## 3. 【优化 / 可选】

- `record()` 中 `total_latency_ms += int(latency_ms)`（`registry.py:119`）多次截断致均值偏差，建议内部 float 累加。
- 累加值/派生值分离（§3.1）贯彻良好，`glyph_pass_rate`、`avg_latency_per_page_ms` 均实时计算、稳定排序保留注册顺序，无问题。
