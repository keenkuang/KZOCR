# Summary — ProbeResult.keys 修复评审 (round-fix)

**Date**: 2026-07-10
**Diff**: `kzocr/engine/types.py` + `tests/test_types.py`

## 角色签署
| 角色 | 结论 |
|------|------|
| security | 通过(SEC-1 闭环,建议补 SEC-F2) |
| architect | 通过(建议连带修订 ARCH-F2/F3) |
| sweng | 通过 |

## 结论
`ProbeResult.keys` 明文阻塞项已闭环,修复最小且测试同步。**建议在本 PR 修订中一并处理 round9 高优先级契约项**:
1. `PageInput.image` → `img`(对齐设计)
2. types.py 补齐 `EngineStatus = Literal["HEALTHY","DEGRADED","UNAVAILABLE"]`
3. `EngineCallRecord.glyph_status: Optional[str]` → `Optional[GlyphStatus]`
4. 概览文档:`Budget.exhausted` 同步 DETAILED;egress 路径修正为 `kzocr.security.egress`

上述修订均为设计已规定、当前代码滞后的低风险同步,可随本 PR 合入,避免再次漂移。
