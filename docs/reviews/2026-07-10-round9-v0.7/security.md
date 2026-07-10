# Security Review — v0.7 自适应 OCR 引擎编排层 (round9)

**Date**: 2026-07-10
**Scope**: 同上
**Verdict**: 条件通过 — 密钥处理原则整体正确,但 `ProbeResult.keys` 明文残留为阻塞项。

## Findings

| ID | Severity | Location | Issue | Recommendation |
|----|----------|----------|-------|----------------|
| SEC-1 | **HIGH (阻塞)** | types.py:140 vs DETAILED §2(446) / overview §3.3 | `ProbeResult.keys: dict[str, str]`,注释示例含明文 `sk-xxx`;而设计规定应为 `dict[str, bool]`(仅存"是否存在",不存值)。`ProbeResult` 可被 `probe_environment()` 返回并流经 trace/日志序列化链路,明文密钥值存在泄露面,且违反 `EngineConfig`"只存环境变量名引用,不存明文凭证"原则。 | 将 types.py `ProbeResult.keys` 改为 `dict[str, bool]`,删除 `sk-xxx` 示例。(即本 PR 修复项) |
| SEC-2 | MEDIUM（已修复） | overview §4.5 vs DETAILED §4.5 / kzocr/security/egress.py | 概览原写 `from kzocr.engines.egress import validate_url`;正确路径为 `kzocr.security.egress`。 | 已在 PR #1 修正:概览 §4.5 现写为 `from kzocr.security.egress import validate_url`(与 DETAILED 及 `khub/client.py:16` 一致)。无需再处理。 |
| SEC-3 | MEDIUM | DETAILED §6.2 / types.py:204 | `EngineCallRecord.error: Optional[str]` 写入 trace 前需 sanitize(DETAILED 已要求"凭证过滤"),types.py 当前无约束。实现 Orchestrator 时必须加过滤,防 API key 泄露到 trace 文件。 | 实现 egress/trace 时统一经 `sanitize()` 后再落盘。 |

## 结论
安全基线(环境变量引用、egress allowlist、原子写入)设计到位。唯一阻塞项是 `ProbeResult.keys` 明文残留,修复后即消除该泄漏面。
