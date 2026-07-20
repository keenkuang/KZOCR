"""
人工校对工作台 API
提供校对员查看任务、提交校对结果、质量统计等功能
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid

router = APIRouter(prefix="/api/proofread", tags=["proofread-workbench"])


# ============== 数据模型 ==============

class ProofreadTask(BaseModel):
    """校对任务"""
    task_id: str
    book_id: str
    book_title: str
    page_num: int
    line_num: int
    original_text: str           # OCR原始文本
    consensus_text: str          # 多引擎共识结果
    llm_corrected: Optional[str] # LLM修改后文本
    priority: str                # P0/P1/P2/P3
    reason_codes: List[str]      # 推送原因
    status: str                  # pending/assigned/completed
    assigned_to: Optional[str]   # 校对员ID
    created_at: str
    thumbnail_url: Optional[str] # 行图缩略图


class ProofreadSubmit(BaseModel):
    """校对提交"""
    task_id: str
    final_text: str              # 校对后的最终文本
    action: str                  # accept/reject/modify
    note: str                    # 校对备注
    reviewer_id: str
    time_spent_seconds: int


class ReviewerStats(BaseModel):
    """校对员统计"""
    reviewer_id: str
    name: str
    total_processed: int
    accepted_count: int
    rejected_count: int
    modified_count: int
    accuracy_rate: float
    avg_time_per_task: float


# ============== 演示数据 ==============

DEMO_TASKS = [
    {
        "task_id": "T-2026-001",
        "book_id": "B001",
        "book_title": "《中医内科学》",
        "page_num": 45,
        "line_num": 3,
        "original_text": "孕妇服用本品，气虚者慎用",
        "consensus_text": "孕妇忌服本品，气虚者慎用",
        "llm_corrected": "孕妇服用本品，气虚者慎用",
        "priority": "P0",
        "reason_codes": ["NEGATION_VIOLATION", "GLYPH_VERIFY_FAILED"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:32:00",
        "thumbnail_url": "/static/images/demo_line_001.jpg"
    },
    {
        "task_id": "T-2026-002",
        "book_id": "B001",
        "book_title": "《中医内科学》",
        "page_num": 67,
        "line_num": 1,
        "original_text": "附子30g",
        "consensus_text": "附子10g",
        "llm_corrected": "附子30g",
        "priority": "P0",
        "reason_codes": ["DOSAGE_POST_ALERT"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:30:00",
        "thumbnail_url": "/static/images/demo_line_002.jpg"
    },
    {
        "task_id": "T-2026-003",
        "book_id": "B002",
        "book_title": "《方剂学》",
        "page_num": 89,
        "line_num": 5,
        "original_text": "父子10g",
        "consensus_text": "附子10g",
        "llm_corrected": "父子10g",
        "priority": "P0",
        "reason_codes": ["GLYPH_VERIFY_FAILED"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:28:00",
        "thumbnail_url": "/static/images/demo_line_003.jpg"
    },
    {
        "task_id": "T-2026-004",
        "book_id": "B002",
        "book_title": "《方剂学》",
        "page_num": 120,
        "line_num": 2,
        "original_text": "术... [3引擎分歧]",
        "consensus_text": "白术 (共识)",
        "llm_corrected": None,
        "priority": "P1",
        "reason_codes": ["CONSENSUS_DISPUTE"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:25:00",
        "thumbnail_url": "/static/images/demo_line_004.jpg"
    },
    {
        "task_id": "T-2026-005",
        "book_id": "B003",
        "book_title": "《针灸学》",
        "page_num": 145,
        "line_num": 4,
        "original_text": "白木 10g [LLM超时]",
        "consensus_text": "白术 10g",
        "llm_corrected": None,
        "priority": "P1",
        "reason_codes": ["LLM_LOCAL_TIMEOUT"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:20:00",
        "thumbnail_url": "/static/images/demo_line_005.jpg"
    },
    {
        "task_id": "T-2026-006",
        "book_id": "B001",
        "book_title": "《中医内科学》",
        "page_num": 200,
        "line_num": 1,
        "original_text": "上方加白术3g",
        "consensus_text": "上方加白术3g",
        "llm_corrected": None,
        "priority": "P2",
        "reason_codes": ["FORMULA_REF_MISMATCH"],
        "status": "pending",
        "assigned_to": None,
        "created_at": "2026-07-06T14:15:00",
        "thumbnail_url": "/static/images/demo_line_006.jpg"
    },
    {
        "task_id": "T-2026-007",
        "book_id": "B004",
        "book_title": "《金匮要略》",
        "page_num": 234,
        "line_num": 5,
        "original_text": "甘草30g",
        "consensus_text": "甘草3g",
        "llm_corrected": "甘草30g",
        "priority": "P0",
        "reason_codes": ["DOSAGE_POST_ALERT"],
        "status": "completed",
        "assigned_to": "reviewer_001",
        "created_at": "2026-07-06T13:00:00",
        "thumbnail_url": "/static/images/demo_line_007.jpg"
    },
    {
        "task_id": "T-2026-008",
        "book_id": "B004",
        "book_title": "《金匮要略》",
        "page_num": 235,
        "line_num": 2,
        "original_text": "黄芹10g",
        "consensus_text": "黄芩10g",
        "llm_corrected": "黄芹10g",
        "priority": "P0",
        "reason_codes": ["GLYPH_VERIFY_FAILED"],
        "status": "completed",
        "assigned_to": "reviewer_001",
        "created_at": "2026-07-06T12:30:00",
        "thumbnail_url": "/static/images/demo_line_008.jpg"
    },
]

# 校对结果存储
PROOFREAD_RESULTS = {}

# 校对员统计
REVIEWER_STATS = {
    "reviewer_001": {
        "reviewer_id": "reviewer_001",
        "name": "张审校",
        "total_processed": 45,
        "accepted_count": 12,
        "rejected_count": 28,
        "modified_count": 5,
        "accuracy_rate": 97.8,
        "avg_time_per_task": 42.5,
    },
    "reviewer_002": {
        "reviewer_id": "reviewer_002",
        "name": "李审校",
        "total_processed": 38,
        "accepted_count": 8,
        "rejected_count": 25,
        "modified_count": 5,
        "accuracy_rate": 95.2,
        "avg_time_per_task": 38.2,
    },
}


# ============== API 路由 ==============

@router.get("/tasks")
async def list_tasks(
    status: str = "pending",
    priority: Optional[str] = None,
    book_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    """获取校对任务列表"""
    filtered = [t for t in DEMO_TASKS if t["status"] == status]
    
    if priority:
        filtered = [t for t in filtered if t["priority"] == priority]
    if book_id:
        filtered = [t for t in filtered if t["book_id"] == book_id]
    
    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": filtered[start:end]
    }


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: str) -> dict[str, object]:
    """获取单个任务详情（含推送原因详情）"""
    task = next((t for t in DEMO_TASKS if t["task_id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 组装推送原因详情
    reason_details = []
    for code in task["reason_codes"]:
        reason_map = {
            "NEGATION_VIOLATION": {
                "code": "NEGATION_VIOLATION",
                "name": "否定词完整性破坏",
                "priority": "P0",
                "description": "LLM修改导致否定词（不/无/忌/禁/勿/慎）丢失或改变",
                "suggested_action": "逐字比对原句和修改句，确认否定含义未被改变"
            },
            "DOSAGE_POST_ALERT": {
                "code": "DOSAGE_POST_ALERT",
                "name": "剂量异常(LLM修改后)",
                "priority": "P0",
                "description": "LLM修改后的剂量超出药典范围或将正常剂量改异常",
                "suggested_action": "重点检查LLM是否错误修改了剂量，对照原书图片"
            },
            "GLYPH_VERIFY_FAILED": {
                "code": "GLYPH_VERIFY_FAILED",
                "name": "字形验证失败",
                "priority": "P0",
                "description": "关键字段字形与标准字体库差异过大，LLM可能改错字",
                "suggested_action": "对照原书图片中该字的字形，确认LLM修改是否正确"
            },
            "CONSENSUS_DISPUTE": {
                "code": "CONSENSUS_DISPUTE",
                "name": "多引擎识别分歧",
                "priority": "P1",
                "description": "多个OCR引擎对同一行文本识别结果不一致",
                "suggested_action": "查看各引擎输出，参考上下文和术语库判断正确文本"
            },
            "LLM_LOCAL_TIMEOUT": {
                "code": "LLM_LOCAL_TIMEOUT",
                "name": "本地LLM超时",
                "priority": "P1",
                "description": "本地ShizhenGPT模型在60秒内未完成校对",
                "suggested_action": "参考其他引擎结果和原书图片自行判断"
            },
            "FORMULA_REF_MISMATCH": {
                "code": "FORMULA_REF_MISMATCH",
                "name": "方剂引用不一致",
                "priority": "P2",
                "description": "方剂的上下文引用链不一致",
                "suggested_action": "追溯被引用方剂，确认引用关系是否正确"
            },
        }
        reason_details.append(reason_map.get(code, {"code": code, "name": code}))
    
    task_detail = dict(task)
    task_detail["reason_details"] = reason_details
    return task_detail


@router.post("/tasks/{task_id}/submit")
async def submit_proofread(task_id: str, submit: ProofreadSubmit) -> dict[str, object]:
    """提交校对结果"""
    task = next((t for t in DEMO_TASKS if t["task_id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 存储校对结果
    result = {
        "result_id": f"R-{uuid.uuid4().hex[:8]}",
        "task_id": task_id,
        "original_text": task["original_text"],
        "consensus_text": task["consensus_text"],
        "llm_corrected": task.get("llm_corrected"),
        "final_text": submit.final_text,
        "action": submit.action,
        "note": submit.note,
        "reviewer_id": submit.reviewer_id,
        "time_spent_seconds": submit.time_spent_seconds,
        "submitted_at": datetime.now().isoformat(),
        "priority": task["priority"],
        "reason_codes": task["reason_codes"],
    }
    PROOFREAD_RESULTS[task_id] = result
    
    # 更新任务状态
    task["status"] = "completed"
    task["assigned_to"] = submit.reviewer_id
    
    # 更新校对员统计
    stats = REVIEWER_STATS.get(submit.reviewer_id)
    if stats:
        stats["total_processed"] += 1
        if submit.action == "accept":
            stats["accepted_count"] += 1
        elif submit.action == "reject":
            stats["rejected_count"] += 1
        elif submit.action == "modify":
            stats["modified_count"] += 1
    
    return {
        "success": True,
        "result_id": result["result_id"],
        "message": "校对结果已提交",
        "next_task_id": _get_next_pending_task_id(task_id)
    }


def _get_next_pending_task_id(current_id: str) -> Optional[str]:
    """获取下一个待处理任务ID"""
    pending = [t for t in DEMO_TASKS if t["status"] == "pending"]
    if not pending:
        return None
    # 按优先级排序
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    pending.sort(key=lambda x: priority_order.get(x["priority"], 99))
    return pending[0]["task_id"]


@router.post("/tasks/{task_id}/assign")
async def assign_task(task_id: str, reviewer_id: str) -> dict[str, object]:
    """分配任务给校对员"""
    task = next((t for t in DEMO_TASKS if t["task_id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != "pending":
        raise HTTPException(status_code=400, detail="Task already assigned or completed")
    
    task["status"] = "assigned"
    task["assigned_to"] = reviewer_id
    return {"success": True, "message": f"任务已分配给 {reviewer_id}"}


@router.get("/results")
async def list_results(
    reviewer_id: Optional[str] = None,
    book_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    """获取校对结果列表（用于导入/审核）"""
    results = list(PROOFREAD_RESULTS.values())
    
    if reviewer_id:
        results = [r for r in results if r["reviewer_id"] == reviewer_id]
    
    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": results[start:end]
    }


@router.post("/results/import")
async def import_results(results: List[dict[str, object]]) -> dict[str, object]:
    """
    批量导入校对结果
    支持从外部系统导入已完成的校对数据
    """
    imported = 0
    for r in results:
        task_id = r.get("task_id")
        if task_id:
            PROOFREAD_RESULTS[task_id] = {
                **r,
                "imported_at": datetime.now().isoformat(),
                "imported": True,
            }
            imported += 1
    
    return {"success": True, "imported_count": imported}


@router.get("/stats/reviewers")
async def get_reviewer_stats() -> list[dict[str, object]]:
    """获取校对员统计"""
    return list(REVIEWER_STATS.values())


@router.get("/stats/overview")
async def get_proofread_overview() -> dict[str, object]:
    """获取校对概况"""
    total_tasks = len(DEMO_TASKS)
    pending = len([t for t in DEMO_TASKS if t["status"] == "pending"])
    completed = len([t for t in DEMO_TASKS if t["status"] == "completed"])
    assigned = len([t for t in DEMO_TASKS if t["status"] == "assigned"])
    
    by_priority = {}
    for p in ["P0", "P1", "P2", "P3"]:
        by_priority[p] = {
            "total": len([t for t in DEMO_TASKS if t["priority"] == p]),
            "pending": len([t for t in DEMO_TASKS if t["priority"] == p and t["status"] == "pending"]),
            "completed": len([t for t in DEMO_TASKS if t["priority"] == p and t["status"] == "completed"]),
        }
    
    return {
        "total_tasks": total_tasks,
        "pending": pending,
        "completed": completed,
        "assigned": assigned,
        "by_priority": by_priority,
        "total_reviewers": len(REVIEWER_STATS),
        "total_results_stored": len(PROOFREAD_RESULTS),
    }


@router.get("/reason-dict")
async def get_reason_dictionary() -> list[dict[str, object]]:
    """获取推送原因字典"""
    return [
        {"code": "NEGATION_VIOLATION", "name": "否定词完整性破坏", "priority": "P0", "color": "#FF3B30"},
        {"code": "DOSAGE_POST_ALERT", "name": "剂量异常(LLM修改后)", "priority": "P0", "color": "#FF3B30"},
        {"code": "DOSAGE_PRE_ALERT", "name": "剂量异常(原始文本)", "priority": "P0", "color": "#FF3B30"},
        {"code": "GLYPH_VERIFY_FAILED", "name": "字形验证失败", "priority": "P0", "color": "#FF3B30"},
        {"code": "CONSENSUS_DISPUTE", "name": "多引擎识别分歧", "priority": "P1", "color": "#FF9500"},
        {"code": "LLM_LOCAL_TIMEOUT", "name": "本地LLM超时", "priority": "P1", "color": "#FF9500"},
        {"code": "LLM_CLOUD_TIMEOUT", "name": "云端LLM超时", "priority": "P1", "color": "#FF9500"},
        {"code": "LINE_COUNT_MISMATCH", "name": "行数不守恒", "priority": "P1", "color": "#FF9500"},
        {"code": "FORMULA_EXTRACT_FAIL", "name": "方剂提取异常", "priority": "P2", "color": "#FFCC00"},
        {"code": "FORMULA_REF_MISMATCH", "name": "方剂引用不一致", "priority": "P2", "color": "#FFCC00"},
        {"code": "MISSING_CHAR_DETECTED", "name": "疑似漏字", "priority": "P2", "color": "#FFCC00"},
        {"code": "PUBLISHER_LOW_ACCURACY", "name": "出版社低准确率", "priority": "P3", "color": "#007AFF"},
    ]
