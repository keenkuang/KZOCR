# Security Review — ProbeResult.keys 修复 (round-fix)

**Date**: 2026-07-10
**Diff**: `kzocr/engine/types.py`(`ProbeResult.keys: dict[str, str]` → `dict[str, bool]`)+ `tests/test_types.py`
**Verdict**: 通过,建议补强。

## Findings

| ID | Severity | Issue | Recommendation |
|----|----------|-------|----------------|
| SEC-F1 | INFO(已修复) | `ProbeResult.keys` 明文 `sk-xxx` 残留已消除,改为仅存存在性 `bool`,符合"不存明文凭证"原则(SEC-1 闭环)。 | 实现 `probe_engines()` 时务必只写入 `True/False`,禁止回填密钥值。 |
| SEC-F2 | MEDIUM | 同类风险仍在:`EngineCallRecord.error: Optional[str]`(types.py:204)写入 trace 前需 sanitize;当前无约束。 | 本 PR 修订时加注释/约定:trace 落盘前经 `sanitize()` 剥离凭证(详见 round9 SEC-3)。 |

## 结论
核心阻塞项已闭环。SEC-F2 为同类防御,建议在修订中显式标注。
