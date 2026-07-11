"""
TCM-Modern-OCR Web API 路由注册模块

集中注册所有 API 路由子模块，便于在主应用中统一引入。

使用示例:
    from kzocr.tcm_ocr.web.api import api_router
    app.include_router(api_router)
"""

from __future__ import annotations

from fastapi import APIRouter

from kzocr.tcm_ocr.web.api.push_decisions import router as push_decisions_router

# =============================================================================
# 主 API 路由器
# =============================================================================

api_router = APIRouter()

# 注册推送决策路由
api_router.include_router(push_decisions_router)


__all__ = ["api_router"]
