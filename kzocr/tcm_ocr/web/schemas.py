"""
TCM-Modern-OCR Web API Pydantic Schema Definitions

本模块定义推送原因追踪系统的所有Pydantic数据模型，
用于FastAPI的请求验证和响应序列化。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# 枚举类型
# =============================================================================

class DecisionStatus(str, Enum):
    """推送决策状态枚举"""
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    AUTO_RESOLVED = "auto_resolved"
    ESCALATED = "escalated"


class DecisionPriority(str, Enum):
    """推送决策优先级枚举"""
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ResolveAction(str, Enum):
    """决策解决动作枚举"""
    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"
    ESCALATE = "escalate"
    AUTO_RESOLVE = "auto_resolve"


# =============================================================================
# 推送原因字典 Schema
# =============================================================================

class PushReasonDictItem(BaseModel):
    """推送原因字典项"""
    reason_code: str = Field(..., description="原因代码")
    name: str = Field(..., description="原因名称（中文）")
    priority: DecisionPriority = Field(..., description="优先级")
    color: str = Field(..., description="显示颜色（HEX）")
    description: Optional[str] = Field(None, description="原因详细描述")
    auto_resolve: bool = Field(False, description="是否支持自动解决")

    class Config:
        json_schema_extra = {
            "example": {
                "reason_code": "CONSENSUS_DISPUTE",
                "name": "多引擎识别分歧",
                "priority": "P1",
                "color": "#FF9500",
                "description": "多个OCR引擎对同一行文本的识别结果存在分歧",
                "auto_resolve": False,
            }
        }


# =============================================================================
# 决策详情 Schema
# =============================================================================

class PushDecisionDetail(BaseModel):
    """推送决策详情 - 单条推送决策的完整信息"""
    id: int = Field(..., description="决策记录ID")
    book_id: str = Field(..., description="书籍ID")
    line_id: Optional[int] = Field(None, description="关联行ID")
    para_id: Optional[int] = Field(None, description="关联段落ID")
    page_num: Optional[int] = Field(None, description="页码")

    # 推送原因
    reason_codes: List[str] = Field(..., description="原因代码列表")
    reason_details: Dict[str, Any] = Field(
        default_factory=dict, description="各原因详细信息"
    )

    # 优先级与状态
    priority: DecisionPriority = Field(..., description="优先级")
    status: DecisionStatus = Field(..., description="当前状态")

    # 快照数据
    engine_snapshots: Optional[Dict[str, Any]] = Field(
        None, description="OCR引擎结果快照"
    )
    llm_snapshots: Optional[Dict[str, Any]] = Field(
        None, description="LLM输入输出快照"
    )

    # 处理结果
    action: Optional[ResolveAction] = Field(None, description="最终处理动作")
    final_text: Optional[str] = Field(None, description="校对后的最终文本")
    note: Optional[str] = Field(None, description="审校备注")
    reviewer_id: Optional[int] = Field(None, description="审校者用户ID")

    # 决策链
    parent_decision_id: Optional[int] = Field(None, description="父决策ID")
    decision_chain: Optional[Dict[str, Any]] = Field(
        None, description="完整决策链路快照"
    )

    # 时间戳
    created_at: datetime = Field(..., description="创建时间")
    resolved_at: Optional[datetime] = Field(None, description="解决时间")
    resolution_time_sec: Optional[int] = Field(None, description="处理耗时（秒）")

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "book_id": "book_001",
                "line_id": 42,
                "para_id": 5,
                "page_num": 12,
                "reason_codes": ["DOSAGE_PRE_ALERT"],
                "reason_details": {
                    "DOSAGE_PRE_ALERT": {
                        "herb_name": "附子",
                        "detected_dosage": "30g",
                        "standard_max": "15g",
                        "severity": "overdose",
                    }
                },
                "priority": "P0",
                "status": "pending",
                "engine_snapshots": {
                    "paddleocr": {"text": "附子30g", "confidence": 0.92},
                    "mineru": {"text": "附子30克", "confidence": 0.88},
                },
                "created_at": "2024-01-15T10:30:00Z",
            }
        }


# =============================================================================
# 列表响应 Schema
# =============================================================================

class PushDecisionListItem(BaseModel):
    """推送决策列表项 - 列表视图用的简化模型"""
    id: int = Field(..., description="决策记录ID")
    book_id: str = Field(..., description="书籍ID")
    line_id: Optional[int] = Field(None, description="关联行ID")
    para_id: Optional[int] = Field(None, description="关联段落ID")
    page_num: Optional[int] = Field(None, description="页码")
    reason_codes: List[str] = Field(..., description="原因代码列表")
    priority: DecisionPriority = Field(..., description="优先级")
    status: DecisionStatus = Field(..., description="当前状态")
    action: Optional[ResolveAction] = Field(None, description="处理动作")
    created_at: datetime = Field(..., description="创建时间")
    resolved_at: Optional[datetime] = Field(None, description="解决时间")
    resolution_time_sec: Optional[int] = Field(None, description="处理耗时（秒）")
    reason_count: int = Field(1, description="关联原因数量")


class PaginationInfo(BaseModel):
    """分页信息"""
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页大小")
    total: int = Field(..., description="总记录数")
    total_pages: int = Field(..., description="总页数")


class PushDecisionListResponse(BaseModel):
    """推送决策列表响应"""
    items: List[PushDecisionListItem] = Field(..., description="决策列表")
    pagination: PaginationInfo = Field(..., description="分页信息")
    filters: Dict[str, Any] = Field(
        default_factory=dict, description="应用的筛选条件"
    )


# =============================================================================
# 请求 Body Schema
# =============================================================================

class ResolveRequest(BaseModel):
    """单个决策解决请求"""
    action: ResolveAction = Field(..., description="处理动作")
    final_text: Optional[str] = Field(None, description="校对后的最终文本")
    note: Optional[str] = Field(None, description="审校备注")
    reviewer_id: int = Field(..., description="审校者用户ID")

    @field_validator("final_text")
    @classmethod
    def validate_final_text_for_modify(cls, v: Optional[str], info) -> Optional[str]:
        """当action为modify时，final_text不能为空"""
        values = info.data
        if values.get("action") == ResolveAction.MODIFY and (v is None or v.strip() == ""):
            raise ValueError("当action为modify时，final_text不能为空")
        return v


class BatchResolveItem(BaseModel):
    """批量处理单项"""
    decision_id: int = Field(..., description="决策记录ID")
    action: ResolveAction = Field(..., description="处理动作")
    final_text: Optional[str] = Field(None, description="校对后的最终文本")
    note: Optional[str] = Field(None, description="审校备注")


class BatchResolveRequest(BaseModel):
    """批量决策解决请求"""
    items: List[BatchResolveItem] = Field(..., description="批量处理列表")
    reviewer_id: int = Field(..., description="审校者用户ID")
    common_note: Optional[str] = Field(None, description="通用备注（会追加到每条备注后）")

    @field_validator("items")
    @classmethod
    def validate_items_not_empty(cls, v: List[BatchResolveItem]) -> List[BatchResolveItem]:
        """确保批量列表不为空"""
        if len(v) == 0:
            raise ValueError("批量处理列表不能为空")
        if len(v) > 100:
            raise ValueError("单次批量处理最多100条")
        return v


# =============================================================================
# 统计 Schema
# =============================================================================

class ReasonStatsItem(BaseModel):
    """按原因统计项"""
    reason_code: str = Field(..., description="原因代码")
    reason_name: str = Field(..., description="原因名称")
    priority: DecisionPriority = Field(..., description="优先级")
    color: str = Field(..., description="显示颜色")
    total_count: int = Field(0, description="总数量")
    pending_count: int = Field(0, description="待处理数量")
    resolved_count: int = Field(0, description="已解决数量")
    rejected_count: int = Field(0, description="已拒绝数量")
    auto_resolved_count: int = Field(0, description="自动解决数量")
    avg_resolution_time_sec: Optional[float] = Field(None, description="平均处理耗时（秒）")


class PriorityStatsItem(BaseModel):
    """按优先级统计项"""
    priority: DecisionPriority = Field(..., description="优先级")
    total_count: int = Field(0, description="总数量")
    pending_count: int = Field(0, description="待处理数量")
    resolved_count: int = Field(0, description="已解决数量")


class StatusStatsItem(BaseModel):
    """按状态统计项"""
    status: DecisionStatus = Field(..., description="状态")
    count: int = Field(0, description="数量")


class BookStatsItem(BaseModel):
    """按书籍统计项"""
    book_id: str = Field(..., description="书籍ID")
    total_count: int = Field(0, description="总推送数")
    pending_count: int = Field(0, description="待处理数")
    resolved_count: int = Field(0, description="已解决数")


class PushStats(BaseModel):
    """推送决策完整统计信息"""
    # 总体统计
    total_decisions: int = Field(0, description="总决策数")
    pending_count: int = Field(0, description="待处理数")
    resolved_count: int = Field(0, description="已解决数")
    rejected_count: int = Field(0, description="已拒绝数")
    auto_resolved_count: int = Field(0, description="自动解决数")
    escalated_count: int = Field(0, description="已升级数")

    # 按优先级统计
    by_priority: List[PriorityStatsItem] = Field(
        default_factory=list, description="按优先级统计"
    )

    # 按原因统计
    by_reason: List[ReasonStatsItem] = Field(
        default_factory=list, description="按原因统计"
    )

    # 按状态统计
    by_status: List[StatusStatsItem] = Field(
        default_factory=list, description="按状态统计"
    )

    # 按书籍统计（仅当未指定book_id时返回）
    by_book: Optional[List[BookStatsItem]] = Field(
        None, description="按书籍统计"
    )

    # 平均处理时间
    avg_resolution_time_sec: Optional[float] = Field(
        None, description="平均处理耗时（秒）"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "total_decisions": 150,
                "pending_count": 45,
                "resolved_count": 95,
                "rejected_count": 5,
                "auto_resolved_count": 3,
                "escalated_count": 2,
                "by_priority": [
                    {"priority": "P0", "total_count": 30, "pending_count": 10, "resolved_count": 20},
                    {"priority": "P1", "total_count": 60, "pending_count": 20, "resolved_count": 40},
                    {"priority": "P2", "total_count": 45, "pending_count": 12, "resolved_count": 33},
                    {"priority": "P3", "total_count": 15, "pending_count": 3, "resolved_count": 12},
                ],
                "by_reason": [],
                "avg_resolution_time_sec": 120.5,
            }
        }


# =============================================================================
# 决策链路 Schema
# =============================================================================

class DecisionChainEventItem(BaseModel):
    """决策链路事件"""
    id: int = Field(..., description="事件ID")
    event_type: str = Field(..., description="事件类型")
    event_data: Dict[str, Any] = Field(default_factory=dict, description="事件数据")
    created_at: datetime = Field(..., description="事件时间")


class DecisionChainResponse(BaseModel):
    """决策链路完整响应"""
    decision: PushDecisionDetail = Field(..., description="当前决策详情")
    parent_chain: List[PushDecisionDetail] = Field(
        default_factory=list, description="父决策链（从根到父）"
    )
    child_decisions: List[PushDecisionDetail] = Field(
        default_factory=list, description="子决策列表"
    )
    events: List[DecisionChainEventItem] = Field(
        default_factory=list, description="决策生命周期事件"
    )


# =============================================================================
# 通用响应包装
# =============================================================================

class ApiResponse(BaseModel):
    """通用API响应包装"""
    success: bool = Field(True, description="是否成功")
    message: Optional[str] = Field(None, description="提示消息")
    data: Optional[Any] = Field(None, description="响应数据")


class BatchResolveResponse(BaseModel):
    """批量处理响应"""
    success: bool = Field(True, description="是否全部成功")
    processed_count: int = Field(0, description="处理数量")
    success_count: int = Field(0, description="成功数量")
    failed_count: int = Field(0, description="失败数量")
    failed_items: List[Dict[str, Any]] = Field(
        default_factory=list, description="失败项详情"
    )
