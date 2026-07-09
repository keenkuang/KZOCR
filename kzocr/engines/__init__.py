"""KZOCR 引擎适配器包 — 统一 OCR 引擎接口（阶段 1 实施）。

依据 v0.3 FREEZE B2 裁决：
- 所有适配器返回 AdapterPageResult，不得自行折算 LineResult。
- adapter_to_line_result() 是唯一的 AdapterPageResult → LineResult 转换入口。
"""
