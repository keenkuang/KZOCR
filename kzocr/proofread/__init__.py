"""kzocr.proofread — 交付式校对包独立前端（方案 B / Route 1）。

本包是"交付式校对台"的阶段 1 实现，以 Python 原生 FastAPI + Jinja2
提供面向校对人员的轻量前端，对 custom.db 做直接读写。

与 ``kzocr/web/`` 完全解耦：不依赖 BookDB、不走 adapter/ 兼容壳、
不引入 npm/JS 工具链。

数据流：
    custom.db ←── proofread UI（校对员编辑 humanFinal）
                          │
                          └──→ import_proofread_package() → BookDB（回导）
"""
from __future__ import annotations
