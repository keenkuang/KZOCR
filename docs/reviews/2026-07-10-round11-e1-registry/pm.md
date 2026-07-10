# KZOCR v0.7 E1 实现评审 — 项目管理（pm）

- **评审角色**：项目管理
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`，分支 `feat/v0.7-registry`）、`tests/test_registry.py`、`docs/plans/ocr-engine-unification.v0.7.md`
- **范围声明**：E1 聚焦首版，`__init__.py` 已声明仅落地注册中心与候选选择；E2/E3/E4 未实现属既定分期，不计入本 E1 阻塞。

---

## 1. 【阻塞 / 必须修复】

**无。** 在「E1 聚焦首版」既定分期下，本提交范围收缩合理：`__init__.py` 已声明仅落地注册中心与候选选择，未触及 `run_engine` 委派（§7.2），未破坏 260+ 现有测试，E2/E3/E4 未实现属预期，不作阻塞。

## 2. 【重要 / 强烈建议】

1. **文档与现实矛盾（§8 Phase 1 交付标准 / 步骤 1.5·1.6·1.8）**：交付标准写明"benchmark 能从空→写入→读出→追加"，且步骤 1.5（NDJSON 持久化）、1.6（`SchedulerError`）、1.8（conftest 9 引擎 fixture）均缺失。建议：在 `v0.7.md` 显式拆分 Phase 1，标注已落地项与延期项归属，避免后续评审误判"Phase 1 已完成"。
2. **`select_candidates` 位置与文档不符（§2.1 / §8 步骤 2.1）**：设计将其归入 `EngineScheduler`，现置于 `registry.py` 作模块函数。建议文档注明"早期落地 registry，E2 复用/封装"，防 E2 重复实现返工（风险低，但需明确）。
3. **潜在除零缺陷（`registry.py:140` `_bayesian_score`）**：`record()` 仅当 `latency_ms` 非 `None` 才累加 `total_latency_ms`，若引擎 `pages>0` 却从未记录延迟，`avg_latency=0 → 1/0` 崩溃。建议对 `latency==0` 回退到 `AVG_LATENCY_DEFAULT_MS`，与冷启动默认值对齐。
4. **`SchedulerError` 未定义（§7 / §8 步骤 1.6）**：`record()` 当前抛 `KeyError`。E4 接管 `run_engine`（§10.2 回归策略）时异常类型需统一，建议本期即落地轻量 `SchedulerError(OcrError)` 基类，降低 E2–E4 集成返工成本。

## 3. 【优化 / 可选】

1. **E1 无调用方**：CLI（`--engine`/`--prefer`/`--tier-order`，§7 E5）尚未接入，端到端验证推迟至 Phase 3.4。建议在 E2 起即加 import/组合烟雾测试，确认 `select_candidates` 被消费路径可触发，避免成"永久内部模块"。
2. **测试差距（§10.1）**：`test_registry.py` 仅 10 例，缺 probe 探测、持久化 roundtrip、状态转换用例。建议补最小探针与状态转换测试，为 E2 提供稳定地基。
