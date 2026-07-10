# Domain Review — v0.7 (round9, 中医古籍 OCR)

**Date**: 2026-07-10
**Scope**: 同上
**Verdict**: 通过。

## Findings

| ID | Severity | Location | Issue | Recommendation |
|----|----------|----------|-------|----------------|
| DOM-1 | LOW | DETAILED §5.3 | `ToxinDoseDetector` 正则 `(药名)\s*(\d+)\s*g` 对单位 "g" 硬编码,未覆盖 "克 / mg / 钱" 等中医常用单位。 | 单位白名单化(`g|克|mg|毫克|钱`),并做数值归一化后再比对 `max_dosage_g`。 |
| DOM-2 | LOW | DETAILED §5.6 | `feedback_apply()` 将人工修正反向同步到 `confusion_set` / `rare_allowlist`,需防误标污染知识库。 | 人工确认前仅入暂存,确认后才落盘;保留来源溯源。 |

## 结论
字形验证、竖排感知、毒药剂量、人工反馈闭环等中医领域逻辑设计清晰,无阻塞项。
