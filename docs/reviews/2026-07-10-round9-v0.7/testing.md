# Testing Review — v0.7 (round9)

**Date**: 2026-07-10
**Scope**: 同上
**Verdict**: 通过(设计层面)。

## Findings

| ID | Severity | Location | Issue | Recommendation |
|----|----------|----------|-------|----------------|
| TEST-1 | INFO | overview §10 | 现有 268 测试覆盖 v0.6;v0.7 新增模块需同步回归套件,尤其:egress 失败路径、B6 双闸边界、竖排跳过 T1、衰减因子、冷启动默认值。 | 按 §10.1 最低用例数(≥8/≥10)补齐,并保留 `test_regression.py` 校验 `run_engine()` 委派后旧行为不变。 |
| TEST-2 | LOW | DETAILED §6.2 | `EngineCallRecord` 设计扩展字段(breakdown / detector_chain / ts / cache_hit)需在测试 fixture 中体现,否则运维排障相关数据无覆盖。 | 实现时同步更新 `conftest.py` fixture。 |

## 结论
测试计划(分层抽样 + 集成 8 路径参数化 + 回归)设计扎实,无阻塞项。
