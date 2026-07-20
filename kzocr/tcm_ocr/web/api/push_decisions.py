"""
推送决策 API 路由模块

提供推送原因追踪系统的完整 REST API，包括：
- 推送决策列表查询（支持分页和筛选）
- 推送决策详情查看
- 决策解决（人工校对结果提交）
- 批量处理
- 统计信息
- 推送原因字典

所有接口返回标准 JSON 格式，错误时返回 HTTP 状态码和错误详情。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from kzocr.tcm_ocr.web.schemas import (
    ApiResponse,
    BatchResolveRequest,
    BatchResolveResponse,
    DecisionChainResponse,
    PushDecisionDetail,
    PushDecisionListItem,
    PushDecisionListResponse,
    PushReasonDictItem,
    PushStats,
    ResolveRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push-decisions", tags=["push-decisions"])


# =============================================================================
# 依赖注入辅助函数
# =============================================================================

def get_runtime_db() -> Callable[..., object]:
    """
    获取 RuntimeDB 实例的依赖注入函数

    从应用状态中获取运行时数据库连接。
    在 FastAPI 应用中需要通过 app.state.runtime_db 设置。
    """
    from fastapi import Request

    def _get_db(request: Request) -> object:
        runtime_db = getattr(request.app.state, "runtime_db", None)
        if runtime_db is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runtime database not available",
            )
        return runtime_db

    return _get_db


def get_push_decision_logger(runtime_db: object = Depends(get_runtime_db())) -> object:
    """
    获取 PushDecisionLogger 实例的依赖注入函数
    """
    from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

    return PushDecisionLogger(runtime_db)


# =============================================================================
# 辅助函数
# =============================================================================

def _row_to_list_item(row: Dict[str, Any]) -> PushDecisionListItem:
    """将数据库行转换为列表项模型"""
    return PushDecisionListItem(
        id=row["id"],
        book_id=row["book_id"],
        line_id=row.get("line_id"),
        para_id=row.get("para_id"),
        page_num=row.get("page_num"),
        reason_codes=row.get("reason_codes", []),
        priority=row["priority"],
        status=row["status"],
        action=row.get("action"),
        created_at=row["created_at"],
        resolved_at=row.get("resolved_at"),
        resolution_time_sec=row.get("resolution_time_sec"),
        reason_count=len(row.get("reason_codes", [])),
    )


def _row_to_detail(row: Dict[str, Any]) -> PushDecisionDetail:
    """将数据库行转换为详情模型"""
    return PushDecisionDetail(
        id=row["id"],
        book_id=row["book_id"],
        line_id=row.get("line_id"),
        para_id=row.get("para_id"),
        page_num=row.get("page_num"),
        reason_codes=row.get("reason_codes", []),
        reason_details=row.get("reason_details", {}),
        priority=row["priority"],
        status=row["status"],
        engine_snapshots=row.get("engine_snapshots"),
        llm_snapshots=row.get("llm_snapshots"),
        action=row.get("action"),
        final_text=row.get("final_text"),
        note=row.get("note"),
        reviewer_id=row.get("reviewer_id"),
        parent_decision_id=row.get("parent_decision_id"),
        decision_chain=row.get("decision_chain"),
        created_at=row["created_at"],
        resolved_at=row.get("resolved_at"),
        resolution_time_sec=row.get("resolution_time_sec"),
    )


# =============================================================================
# 路由定义
# =============================================================================


@router.get("/", response_model=PushDecisionListResponse)
async def list_push_decisions(
    book_id: Optional[str] = Query(None, description="按书籍ID筛选"),
    status_filter: Optional[str] = Query(
        "pending",
        alias="status",
        description="按状态筛选: pending/resolved/rejected/auto_resolved/escalated",
    ),
    priority: Optional[str] = Query(None, description="按优先级筛选: P0/P1/P2/P3"),
    reason_code: Optional[str] = Query(None, description="按原因代码筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小"),
    runtime_db: object = Depends(get_runtime_db()),
) -> PushDecisionListResponse:
    """
    获取推送决策列表

    支持按书籍ID、状态、优先级、原因代码筛选，支持分页。
    默认返回待处理（pending）的决策，按优先级和时间排序。
    """
    try:
        # 构建查询条件
        conditions = []
        params = []

        if status_filter:
            conditions.append("status = %s")
            params.append(status_filter)
        if book_id:
            conditions.append("book_id = %s")
            params.append(book_id)
        if priority:
            conditions.append("priority = %s")
            params.append(priority)
        if reason_code:
            conditions.append("%s = ANY(reason_codes)")
            params.append(reason_code)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # 获取总数
        count_sql = f"SELECT COUNT(*) as total FROM PushDecisionLog {where_clause}"
        with runtime_db.get_cursor() as cursor:
            cursor.execute(count_sql, tuple(params))
            total = cursor.fetchone()["total"]

        # 计算分页
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        offset = (page - 1) * page_size

        # 获取数据
        data_sql = f"""
            SELECT id, book_id, line_id, para_id, page_num,
                   reason_codes, priority, status, action,
                   created_at, resolved_at, resolution_time_sec
            FROM PushDecisionLog
            {where_clause}
            ORDER BY
                CASE priority
                    WHEN 'P0' THEN 1
                    WHEN 'P1' THEN 2
                    WHEN 'P2' THEN 3
                    WHEN 'P3' THEN 4
                END,
                created_at DESC
            LIMIT %s OFFSET %s
        """
        with runtime_db.get_cursor() as cursor:
            cursor.execute(data_sql, tuple(params) + (page_size, offset))
            rows = [dict(row) for row in cursor.fetchall()]

        items = [_row_to_list_item(row) for row in rows]

        return PushDecisionListResponse(
            items=items,
            pagination={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            filters={
                "status": status_filter,
                "book_id": book_id,
                "priority": priority,
                "reason_code": reason_code,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list push decisions: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list push decisions: {str(e)}",
        )


@router.get("/{decision_id}", response_model=PushDecisionDetail)
async def get_push_decision(
    decision_id: int,
    runtime_db: object = Depends(get_runtime_db()),
) -> PushDecisionDetail:
    """
    获取推送决策详情

    返回指定ID的推送决策完整信息，包括原因详情、引擎快照、LLM快照等。
    """
    try:
        with runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM PushDecisionLog WHERE id = %s
                """,
                (decision_id,),
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Push decision not found: {decision_id}",
            )

        return _row_to_detail(dict(row))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get push decision %d: %s", decision_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get push decision: {str(e)}",
        )


@router.post("/{decision_id}/resolve", response_model=ApiResponse)
async def resolve_push_decision(
    decision_id: int,
    body: ResolveRequest,
    runtime_db: object = Depends(get_runtime_db()),
) -> ApiResponse:
    """
    提交校对结果解决推送决策

    人工审校后提交处理结果，标记决策为已解决。
    支持 accept（接受）、reject（拒绝）、modify（修改）、escalate（升级）等动作。
    """
    try:
        # 检查决策是否存在
        with runtime_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, status FROM PushDecisionLog WHERE id = %s",
                (decision_id,),
            )
            existing = cursor.fetchone()

        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Push decision not found: {decision_id}",
            )

        if existing["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Decision {decision_id} is already {existing['status']}",
            )

        # 执行解决
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        decision_logger = PushDecisionLogger(runtime_db)
        success = decision_logger.resolve_decision(
            decision_id=decision_id,
            action=body.action.value,
            final_text=body.final_text or "",
            note=body.note or "",
            reviewer_id=body.reviewer_id,
        )

        if success:
            return ApiResponse(
                success=True,
                message=f"Decision {decision_id} resolved with action '{body.action.value}'",
                data={"decision_id": decision_id, "action": body.action.value},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to resolve decision",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to resolve push decision %d: %s", decision_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resolve decision: {str(e)}",
        )


@router.get("/stats", response_model=PushStats)
async def get_push_decision_stats(
    book_id: Optional[str] = Query(None, description="按书籍ID筛选统计"),
    runtime_db: object = Depends(get_runtime_db()),
) -> PushStats:
    """
    获取推送决策统计信息

    返回总体统计、按优先级统计、按原因统计、按状态统计等。
    可指定 book_id 获取单本书的统计。
    """
    try:
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        decision_logger = PushDecisionLogger(runtime_db)
        stats = decision_logger.get_decision_stats(book_id=book_id)

        # 转换 by_priority 为模型列表
        by_priority = [
            {
                "priority": item["priority"],
                "total_count": item.get("total_count", 0) or 0,
                "pending_count": item.get("pending_count", 0) or 0,
                "resolved_count": item.get("resolved_count", 0) or 0,
            }
            for item in stats.get("by_priority", [])
        ]

        # 转换 by_reason 为模型列表
        by_reason = []

        # 从 PushReasonDict 获取原因元数据
        reason_meta = {}
        try:
            with runtime_db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT reason_code, name, priority, color FROM PushReasonDict"
                )
                for row in cursor.fetchall():
                    reason_meta[row["reason_code"]] = dict(row)
        except Exception:
            # 如果 PushReasonDict 表不存在，使用内建元数据
            from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger as PDL

            reason_meta = {
                code: {
                    "reason_code": code,
                    "name": info["name"],
                    "priority": info["priority"],
                    "color": info["color"],
                }
                for code, info in PDL.REASON_CODES.items()
            }

        for item in stats.get("by_reason", []):
            code = item.get("reason_code", "")
            meta = reason_meta.get(code, {})
            by_reason.append(
                {
                    "reason_code": code,
                    "reason_name": meta.get("name", code),
                    "priority": meta.get("priority", "P3"),
                    "color": meta.get("color", "#666666"),
                    "total_count": item.get("total_count", 0) or 0,
                    "pending_count": 0,  # 将在下面查询
                    "resolved_count": item.get("resolved_count", 0) or 0,
                    "rejected_count": 0,
                    "auto_resolved_count": 0,
                    "avg_resolution_time_sec": None,
                }
            )

        # 按状态统计
        by_status = [
            {"status": item["status"], "count": item.get("count", 0) or 0}
            for item in stats.get("by_status", [])
        ]

        # 按书籍统计（仅当未指定book_id时）
        by_book = None
        if not book_id:
            try:
                with runtime_db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            book_id,
                            COUNT(*) as total_count,
                            COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                            COUNT(*) FILTER (WHERE status IN ('resolved', 'auto_resolved')) as resolved_count
                        FROM PushDecisionLog
                        GROUP BY book_id
                        ORDER BY total_count DESC
                        LIMIT 20
                        """
                    )
                    by_book = [
                        {
                            "book_id": row["book_id"],
                            "total_count": row.get("total_count", 0) or 0,
                            "pending_count": row.get("pending_count", 0) or 0,
                            "resolved_count": row.get("resolved_count", 0) or 0,
                        }
                        for row in cursor.fetchall()
                    ]
            except Exception:
                by_book = []

        return PushStats(
            total_decisions=stats.get("total_decisions", 0) or 0,
            pending_count=stats.get("pending_count", 0) or 0,
            resolved_count=stats.get("resolved_count", 0) or 0,
            rejected_count=stats.get("rejected_count", 0) or 0,
            auto_resolved_count=stats.get("auto_resolved_count", 0) or 0,
            escalated_count=stats.get("escalated_count", 0) or 0,
            by_priority=by_priority,
            by_reason=by_reason,
            by_status=by_status,
            by_book=by_book,
            avg_resolution_time_sec=stats.get("avg_resolution_time_sec"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get push decision stats: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get stats: {str(e)}",
        )


@router.get("/reasons", response_model=List[PushReasonDictItem])
async def get_push_reason_dict(
    runtime_db: object = Depends(get_runtime_db()),
) -> list[PushReasonDictItem]:
    """
    获取推送原因字典

    返回所有15种推送原因的完整定义，包括代码、名称、优先级、颜色、描述等。
    """
    try:
        with runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT reason_code, name, priority, color, description, auto_resolve
                FROM PushReasonDict
                ORDER BY
                    CASE priority
                        WHEN 'P0' THEN 1
                        WHEN 'P1' THEN 2
                        WHEN 'P2' THEN 3
                        WHEN 'P3' THEN 4
                    END,
                    reason_code
                """
            )
            rows = cursor.fetchall()

        if not rows:
            # 如果数据库表为空，返回内建字典
            from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger as PDL

            return [
                PushReasonDictItem(
                    reason_code=code,
                    name=info["name"],
                    priority=info["priority"],
                    color=info["color"],
                    description=info.get("description"),
                    auto_resolve=info.get("auto_resolve", False),
                )
                for code, info in PDL.REASON_CODES.items()
            ]

        return [
            PushReasonDictItem(
                reason_code=row["reason_code"],
                name=row["name"],
                priority=row["priority"],
                color=row["color"],
                description=row.get("description"),
                auto_resolve=row.get("auto_resolve", False),
            )
            for row in rows
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get push reason dict: %s", e)
        # 返回内建字典作为降级
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger as PDL

        return [
            PushReasonDictItem(
                reason_code=code,
                name=info["name"],
                priority=info["priority"],
                color=info["color"],
                description=info.get("description"),
                auto_resolve=info.get("auto_resolve", False),
            )
            for code, info in PDL.REASON_CODES.items()
        ]


@router.post("/batch-resolve", response_model=BatchResolveResponse)
async def batch_resolve(
    body: BatchResolveRequest,
    runtime_db: object = Depends(get_runtime_db()),
) -> BatchResolveResponse:
    """
    批量处理推送决策

    一次性提交多个决策的校对结果，提高审校效率。
    最多支持100条批量处理。
    """
    try:
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        decision_logger = PushDecisionLogger(runtime_db)

        success_count = 0
        failed_items: List[Dict[str, Any]] = []

        for item in body.items:
            try:
                # 检查决策是否存在且状态为pending
                with runtime_db.get_cursor() as cursor:
                    cursor.execute(
                        "SELECT id, status FROM PushDecisionLog WHERE id = %s",
                        (item.decision_id,),
                    )
                    existing = cursor.fetchone()

                if not existing:
                    failed_items.append(
                        {
                            "decision_id": item.decision_id,
                            "error": "Decision not found",
                        }
                    )
                    continue

                if existing["status"] != "pending":
                    failed_items.append(
                        {
                            "decision_id": item.decision_id,
                            "error": f"Decision already {existing['status']}",
                        }
                    )
                    continue

                # 拼接备注
                note = item.note or ""
                if body.common_note and note:
                    note = f"{note} | {body.common_note}"
                elif body.common_note:
                    note = body.common_note

                success = decision_logger.resolve_decision(
                    decision_id=item.decision_id,
                    action=item.action.value,
                    final_text=item.final_text or "",
                    note=note,
                    reviewer_id=body.reviewer_id,
                )

                if success:
                    success_count += 1
                else:
                    failed_items.append(
                        {
                            "decision_id": item.decision_id,
                            "error": "Failed to resolve",
                        }
                    )

            except Exception as item_error:
                logger.error(
                    "Failed to resolve decision %d in batch: %s",
                    item.decision_id,
                    item_error,
                )
                failed_items.append(
                    {
                        "decision_id": item.decision_id,
                        "error": str(item_error),
                    }
                )

        return BatchResolveResponse(
            success=len(failed_items) == 0,
            processed_count=len(body.items),
            success_count=success_count,
            failed_count=len(failed_items),
            failed_items=failed_items,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to batch resolve push decisions: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to batch resolve: {str(e)}",
        )


@router.get("/{decision_id}/chain", response_model=DecisionChainResponse)
async def get_decision_chain(
    decision_id: int,
    runtime_db: object = Depends(get_runtime_db()),
) -> DecisionChainResponse:
    """
    获取决策的完整链路信息

    返回当前决策详情、父决策链、子决策列表和决策生命周期事件。
    用于追踪决策从创建到解决的完整路径。
    """
    try:
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        decision_logger = PushDecisionLogger(runtime_db)
        chain_data = decision_logger.get_decision_chain(decision_id)

        return DecisionChainResponse(
            decision=_row_to_detail(chain_data["decision"]),
            parent_chain=[_row_to_detail(d) for d in chain_data["parent_chain"]],
            child_decisions=[_row_to_detail(d) for d in chain_data["child_decisions"]],
            events=[
                {
                    "id": e["id"],
                    "event_type": e["event_type"],
                    "event_data": e.get("event_data", {}),
                    "created_at": e["created_at"],
                }
                for e in chain_data["events"]
            ],
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get decision chain for %d: %s", decision_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get decision chain: {str(e)}",
        )
