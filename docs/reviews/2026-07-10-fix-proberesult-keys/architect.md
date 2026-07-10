# Architect Review — ProbeResult.keys 修复 (round-fix)

**Date**: 2026-07-10
**Verdict**: 通过,附连带修订建议。

## Findings

| ID | Severity | Issue | Recommendation |
|----|----------|-------|----------------|
| ARCH-F1 | INFO(已修复) | `ProbeResult.keys` 类型对齐设计(round9 ARCH-3 关联项)。 | — |
| ARCH-F2 | MEDIUM | 同文件仍存契约漂移(round9):`PageInput.image` 应为 `img`(ARCH-2);`EngineStatus` 被设计引用但 types.py 未定义(ARCH-3);`Budget.exhausted` 在概览为死属性(ARCH-4);概览 egress 路径错(SEC-2)。 | 建议在**本 PR 修订**中一并处理,避免再次漂移。 |
| ARCH-F3 | LOW | `EngineCallRecord.glyph_status: Optional[str]`(types.py:203)应为 `Optional[GlyphStatus]`(与 `LineResult.glyph_status` 一致),属同类类型不一致。 | 修订时改为 `Optional[GlyphStatus]`。 |

## 结论
修复本身正确且最小。建议借本 PR 一次性补齐 round9 高优先级契约项(ARCH-F2/F3),降低后续返工。
