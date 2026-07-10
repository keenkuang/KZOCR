# Performance Review — v0.7 (round9)

**Date**: 2026-07-10
**Scope**: 同上
**Verdict**: 通过。

## Findings

| ID | Severity | Location | Issue | Recommendation |
|----|----------|----------|-------|----------------|
| PERF-1 | LOW | overview §6.3 vs DETAILED §5.2 | 概览 `Budget.exhausted` 恒为 False,导致 Tier 2/3 内部 `if budget.exhausted` 永不触发,预算闸失效;DETAILED 已修复。 | 同步概览(见 ARCH-4)。 |
| PERF-2 | INFO | overview §6.4 | 并行策略(默认串行 + GPU opt-in `KZOCR_ENGINE_PARALLEL`)合理;该开关仅在 `ProbeResult.gpu=True` 生效,需配置校验防误开。 | 在 `probe_environments()` 中校验 gpu 标志后再允许并行。 |

## 结论
预算双闸、贝叶斯评分、衰减因子的性能模型合理,无阻塞项。
