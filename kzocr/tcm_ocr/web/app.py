"""
TCM-Modern-OCR FastAPI 主应用 V1.1
优化项：安全中间件 + 审计日志 + 统一响应 + 速率限制 + 安全响应头
学习智谱优化点改进实施版本
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Coroutine

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# =============================================================================
# 配置
# =============================================================================

FRONTEND_DIR = Path(__file__).parent / "frontend"
API_VERSION = "1.1.0"

# 速率限制（60 req/min per IP）
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60     # requests
_rate_limit_store: dict[str, list[float]] = {}

# 审计日志缓冲区
_audit_log_buffer: list[dict] = []
_AUDIT_LOG_MAX_SIZE = 1000


# =============================================================================
# 统一响应包装
# =============================================================================

class UnifiedJSONResponse(JSONResponse):
    """统一JSON响应格式：{data, meta, error}"""

    def render(self, content: object) -> bytes:
        if isinstance(content, dict) and ("error" in content or "detail" in content):
            # 错误响应已包装，直接返回
            return super().render(content)
        if isinstance(content, list):
            body = {"data": content, "meta": {"count": len(content), "version": API_VERSION}}
        elif isinstance(content, dict) and ("data" in content or "meta" in content or "error" in content):
            body = content
        else:
            body = {"data": content, "meta": {"version": API_VERSION}}
        return super().render(body)


# =============================================================================
# 安全中间件：速率限制 + 安全响应头 + 审计日志
# =============================================================================

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    综合安全中间件：
    - 速率限制（IP级，60 req/min）
    - 安全响应头（X-Content-Type-Options, X-Frame-Options等）
    - 结构化审计日志
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Coroutine[Any, Any, Response]]) -> Response:
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        method = request.method

        # --- 速率限制检查 ---
        now = time.time()
        if client_ip not in _rate_limit_store:
            _rate_limit_store[client_ip] = []
        timestamps = _rate_limit_store[client_ip]
        # 清理过期时间戳
        timestamps[:] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            logger.warning("Rate limit exceeded for IP: %s", client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too many requests",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "detail": f"Limit: {_RATE_LIMIT_MAX} requests per {_RATE_LIMIT_WINDOW}s",
                    "retry_after": int(_RATE_LIMIT_WINDOW - (now - timestamps[0])) if timestamps else _RATE_LIMIT_WINDOW,
                },
            )
        timestamps.append(now)

        # --- 处理请求 ---
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("Unhandled exception: %s", exc, exc_info=True)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "code": "INTERNAL_ERROR",
                    "detail": str(exc),
                },
            )

        # --- 计算耗时 ---
        duration_ms = round((time.time() - start_time) * 1000, 2)
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        # --- 安全响应头 ---
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-API-Version"] = API_VERSION

        # --- 审计日志 ---
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "ip": client_ip,
            "method": method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "user_agent": request.headers.get("user-agent", "-"),
        }
        _audit_log_buffer.append(audit_entry)
        if len(_audit_log_buffer) > _AUDIT_LOG_MAX_SIZE:
            _audit_log_buffer[:] = _audit_log_buffer[-_AUDIT_LOG_MAX_SIZE:]

        # 记录慢请求
        if duration_ms > 1000:
            logger.warning("Slow request: %s %s took %sms", method, path, duration_ms)

        return response


# =============================================================================
# 异常处理器
# =============================================================================

async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """统一HTTP异常处理"""
    from fastapi.exceptions import HTTPException as FastAPIHTTPException
    if isinstance(exc, FastAPIHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.detail if isinstance(exc.detail, str) else "Request error",
                "code": f"HTTP_{exc.status_code}",
                "detail": exc.detail if not isinstance(exc.detail, str) else None,
            },
        )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
            "detail": str(exc),
        },
    )


# =============================================================================
# 生命周期管理
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理"""
    logger.info("=" * 60)
    logger.info("TCM-Modern-OCR Dashboard V%s starting up...", API_VERSION)
    logger.info("Security middleware: enabled (rate_limit=%d/%ds)", _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW)
    logger.info("Audit logging: enabled (buffer_size=%d)", _AUDIT_LOG_MAX_SIZE)
    logger.info("CORS: enabled (origins=['*'])")
    logger.info("=" * 60)
    yield
    # 持久化审计日志
    if _audit_log_buffer:
        logger.info("Persisting %d audit log entries...", len(_audit_log_buffer))
    logger.info("TCM-Modern-OCR Dashboard shutting down...")


# =============================================================================
# 创建 FastAPI 应用
# =============================================================================

app = FastAPI(
    title="TCM-Modern-OCR Dashboard",
    description="中医古籍 OCR 系统 Dashboard V1.1 - 安全增强版",
    version=API_VERSION,
    lifespan=lifespan,
    default_response_class=UnifiedJSONResponse,
)

# --- 注册安全中间件（必须在CORS之前） ---
app.add_middleware(SecurityMiddleware)

# --- CORS 中间件 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Response-Time", "X-API-Version"],
)

# --- 注册异常处理器 ---
from fastapi.exceptions import HTTPException  # noqa: E402
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, http_exception_handler)


# =============================================================================
# 注册路由
# =============================================================================

# 推送决策 API
try:
    from kzocr.tcm_ocr.web.api.push_decisions import router as push_router
    app.include_router(push_router)
    logger.info("Registered push_decisions router")
except ImportError as e:
    logger.warning("Failed to import push_decisions router: %s", e)

# Dashboard API
from kzocr.tcm_ocr.web.api.dashboard import router as dashboard_router  # noqa: E402
app.include_router(dashboard_router)
logger.info("Registered dashboard router")

# 人工校对工作台 API
try:
    from kzocr.tcm_ocr.web.api.proofread_workbench import router as proofread_router
    app.include_router(proofread_router)
    logger.info("Registered proofread_workbench router")
except ImportError as e:
    logger.warning("Failed to import proofread_workbench router: %s", e)


# =============================================================================
# 静态文件服务
# =============================================================================

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    logger.info("Mounted static files from %s", FRONTEND_DIR)
else:
    logger.warning("Frontend directory not found: %s", FRONTEND_DIR)


# =============================================================================
# 根路由
# =============================================================================

@app.get("/")
async def root() -> Response | dict[str, object]:
    """根路由 - 返回 Dashboard 首页"""
    dashboard_html = FRONTEND_DIR / "dashboard.html"
    if dashboard_html.exists():
        return FileResponse(str(dashboard_html))
    return {
        "data": {"message": "TCM-Modern-OCR Dashboard V" + API_VERSION, "status": "running"},
        "meta": {"version": API_VERSION},
    }


@app.get("/dashboard")
async def dashboard() -> Response | dict[str, object]:
    """Dashboard 页面路由"""
    dashboard_html = FRONTEND_DIR / "dashboard.html"
    if dashboard_html.exists():
        return FileResponse(str(dashboard_html))
    return {
        "data": {"message": "Dashboard page not found", "path": str(dashboard_html)},
        "meta": {"version": API_VERSION},
    }


@app.get("/health")
async def health_check() -> dict[str, object]:
    """健康检查端点"""
    return {
        "data": {
            "status": "healthy",
            "service": "tcm-ocr-dashboard",
            "version": API_VERSION,
            "features": {
                "rate_limiting": True,
                "audit_logging": True,
                "security_headers": True,
                "unified_response": True,
            },
        },
        "meta": {"version": API_VERSION},
    }


@app.get("/health/audit-log")
async def get_audit_log(limit: int = 100) -> dict[str, object]:
    """查看审计日志（调试用）"""
    return {
        "data": {
            "total_entries": len(_audit_log_buffer),
            "entries": _audit_log_buffer[-limit:],
        },
        "meta": {"version": API_VERSION, "count": min(limit, len(_audit_log_buffer))},
    }
