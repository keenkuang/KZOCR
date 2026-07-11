"""Dashboard 数据 API - 提供系统运行状态和质量指标"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# =============================================================================
# Demo 数据（模拟真实运行数据）
# =============================================================================

# 模拟推送决策数据（30条）
DEMO_PUSH_DECISIONS: List[Dict[str, Any]] = [
    {
        "id": 1001, "book_id": "B001", "book_title": "中医内科学",
        "page": 45, "line": 3, "priority": "P0",
        "reason": "否定词完整性破坏",
        "reason_code": "NEGATION_INCOMPLETE",
        "original": "孕妇忌服本品", "llm": "孕妇服用本品",
        "status": "pending", "time": "2026-07-06 14:32",
    },
    {
        "id": 1002, "book_id": "B001", "book_title": "中医内科学",
        "page": 67, "line": 12, "priority": "P0",
        "reason": "剂量异常(LLM修改后)",
        "reason_code": "DOSAGE_POST_ALERT",
        "original": "附子30g", "llm": "附子3g",
        "status": "pending", "time": "2026-07-06 13:15",
    },
    {
        "id": 1003, "book_id": "B002", "book_title": "中药学",
        "page": 128, "line": 5, "priority": "P0",
        "reason": "字形验证失败",
        "reason_code": "GLYPH_FAIL",
        "original": "白术12g", "llm": "白木12g",
        "status": "resolved", "time": "2026-07-06 11:45",
    },
    {
        "id": 1004, "book_id": "B003", "book_title": "伤寒论",
        "page": 89, "line": 7, "priority": "P0",
        "reason": "否定词完整性破坏",
        "reason_code": "NEGATION_INCOMPLETE",
        "original": "不可发汗", "llm": "可以发汗",
        "status": "pending", "time": "2026-07-06 10:22",
    },
    {
        "id": 1005, "book_id": "B001", "book_title": "中医内科学",
        "page": 156, "line": 22, "priority": "P1",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "柴胡10g", "llm": "柴胡10克",
        "status": "resolved", "time": "2026-07-06 09:18",
    },
    {
        "id": 1006, "book_id": "B004", "book_title": "金匮要略",
        "page": 234, "line": 8, "priority": "P1",
        "reason": "禁忌词上下文破坏",
        "reason_code": "TABOO_CONTEXT",
        "original": "孕妇禁用", "llm": "孕妇慎用",
        "status": "pending", "time": "2026-07-06 08:55",
    },
    {
        "id": 1007, "book_id": "B002", "book_title": "中药学",
        "page": 312, "line": 15, "priority": "P1",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "当归15g", "llm": "当归属15g",
        "status": "resolved", "time": "2026-07-05 17:30",
    },
    {
        "id": 1008, "book_id": "B005", "book_title": "温病学",
        "page": 56, "line": 4, "priority": "P0",
        "reason": "剂量异常(LLM修改后)",
        "reason_code": "DOSAGE_POST_ALERT",
        "original": "细辛15g", "llm": "细辛3g",
        "status": "resolved", "time": "2026-07-05 16:45",
    },
    {
        "id": 1009, "book_id": "B003", "book_title": "伤寒论",
        "page": 178, "line": 9, "priority": "P2",
        "reason": "LLM过度改写(长度>30%)",
        "reason_code": "LLM_OVER_REWRITE",
        "original": "桂枝汤主之", "llm": "桂枝汤主治太阳中风",
        "status": "pending", "time": "2026-07-05 15:20",
    },
    {
        "id": 1010, "book_id": "B001", "book_title": "中医内科学",
        "page": 289, "line": 17, "priority": "P1",
        "reason": "方剂组成完整性校验失败",
        "reason_code": "FORMULA_INCOMPLETE",
        "original": "四君子汤：人参、白术、茯苓、甘草",
        "llm": "四君子汤：人参、白术、茯苓",
        "status": "pending", "time": "2026-07-05 14:10",
    },
    {
        "id": 1011, "book_id": "B006", "book_title": "针灸学",
        "page": 45, "line": 6, "priority": "P2",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "足三里", "llm": "足三思",
        "status": "resolved", "time": "2026-07-05 11:30",
    },
    {
        "id": 1012, "book_id": "B004", "book_title": "金匮要略",
        "page": 90, "line": 11, "priority": "P0",
        "reason": "否定词完整性破坏",
        "reason_code": "NEGATION_INCOMPLETE",
        "original": "非虚寒者勿用", "llm": "虚寒者勿用",
        "status": "resolved", "time": "2026-07-05 10:05",
    },
    {
        "id": 1013, "book_id": "B002", "book_title": "中药学",
        "page": 67, "line": 19, "priority": "P3",
        "reason": "置信度低于阈值",
        "reason_code": "LOW_CONFIDENCE",
        "original": "川芎", "llm": "川弓",
        "status": "pending", "time": "2026-07-04 16:40",
    },
    {
        "id": 1014, "book_id": "B007", "book_title": "黄帝内经",
        "page": 123, "line": 8, "priority": "P1",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "阴阳者，天地之道也", "llm": "阴阳者，天地之道",
        "status": "resolved", "time": "2026-07-04 15:25",
    },
    {
        "id": 1015, "book_id": "B001", "book_title": "中医内科学",
        "page": 334, "line": 2, "priority": "P0",
        "reason": "剂量异常(LLM修改后)",
        "reason_code": "DOSAGE_POST_ALERT",
        "original": "大黄后下15g", "llm": "大黄后下5g",
        "status": "pending", "time": "2026-07-04 14:00",
    },
    {
        "id": 1016, "book_id": "B005", "book_title": "温病学",
        "page": 178, "line": 14, "priority": "P2",
        "reason": "LLM过度改写(长度>30%)",
        "reason_code": "LLM_OVER_REWRITE",
        "original": "舌红苔黄", "llm": "舌头红色舌苔黄色",
        "status": "resolved", "time": "2026-07-04 11:15",
    },
    {
        "id": 1017, "book_id": "B003", "book_title": "伤寒论",
        "page": 256, "line": 6, "priority": "P1",
        "reason": "方剂组成完整性校验失败",
        "reason_code": "FORMULA_INCOMPLETE",
        "original": "麻黄汤：麻黄、桂枝、杏仁、甘草",
        "llm": "麻黄汤：麻黄、桂枝、杏仁",
        "status": "pending", "time": "2026-07-04 09:30",
    },
    {
        "id": 1018, "book_id": "B006", "book_title": "针灸学",
        "page": 89, "line": 21, "priority": "P3",
        "reason": "置信度低于阈值",
        "reason_code": "LOW_CONFIDENCE",
        "original": "百会穴", "llm": "白会穴",
        "status": "resolved", "time": "2026-07-03 17:45",
    },
    {
        "id": 1019, "book_id": "B008", "book_title": "方剂学",
        "page": 145, "line": 10, "priority": "P0",
        "reason": "字形验证失败",
        "reason_code": "GLYPH_FAIL",
        "original": "熟地黄20g", "llm": "熟也黄20g",
        "status": "pending", "time": "2026-07-03 16:20",
    },
    {
        "id": 1020, "book_id": "B004", "book_title": "金匮要略",
        "page": 67, "line": 3, "priority": "P1",
        "reason": "禁忌词上下文破坏",
        "reason_code": "TABOO_CONTEXT",
        "original": "十八反：半蒌贝蔹及攻乌", "llm": "十八反：半蒌贝蔹及功乌",
        "status": "resolved", "time": "2026-07-03 14:55",
    },
    {
        "id": 1021, "book_id": "B001", "book_title": "中医内科学",
        "page": 412, "line": 18, "priority": "P2",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "气虚血瘀", "llm": "气卢血瘀",
        "status": "pending", "time": "2026-07-03 11:40",
    },
    {
        "id": 1022, "book_id": "B009", "book_title": "中医诊断学",
        "page": 78, "line": 7, "priority": "P0",
        "reason": "否定词完整性破坏",
        "reason_code": "NEGATION_INCOMPLETE",
        "original": "不寒不热", "llm": "寒不热",
        "status": "resolved", "time": "2026-07-03 10:10",
    },
    {
        "id": 1023, "book_id": "B007", "book_title": "黄帝内经",
        "page": 234, "line": 12, "priority": "P1",
        "reason": "方剂组成完整性校验失败",
        "reason_code": "FORMULA_INCOMPLETE",
        "original": "四物汤：当归、川芎、白芍、熟地",
        "llm": "四物汤：当归、川芎、白芍",
        "status": "pending", "time": "2026-07-02 16:30",
    },
    {
        "id": 1024, "book_id": "B002", "book_title": "中药学",
        "page": 189, "line": 25, "priority": "P0",
        "reason": "剂量异常(LLM修改后)",
        "reason_code": "DOSAGE_POST_ALERT",
        "original": "朱砂0.5g冲服", "llm": "朱砂5g冲服",
        "status": "pending", "time": "2026-07-02 14:50",
    },
    {
        "id": 1025, "book_id": "B005", "book_title": "温病学",
        "page": 301, "line": 9, "priority": "P2",
        "reason": "LLM过度改写(长度>30%)",
        "reason_code": "LLM_OVER_REWRITE",
        "original": "脉浮数", "llm": "脉搏浮而数",
        "status": "resolved", "time": "2026-07-02 11:20",
    },
    {
        "id": 1026, "book_id": "B008", "book_title": "方剂学",
        "page": 267, "line": 15, "priority": "P3",
        "reason": "置信度低于阈值",
        "reason_code": "LOW_CONFIDENCE",
        "original": "逍遥散", "llm": "逍遥散",
        "status": "pending", "time": "2026-07-01 17:10",
    },
    {
        "id": 1027, "book_id": "B009", "book_title": "中医诊断学",
        "page": 156, "line": 4, "priority": "P1",
        "reason": "多引擎识别分歧",
        "reason_code": "CONSENSUS_DISPUTE",
        "original": "面色萎黄", "llm": "面色委黄",
        "status": "resolved", "time": "2026-07-01 15:35",
    },
    {
        "id": 1028, "book_id": "B006", "book_title": "针灸学",
        "page": 345, "line": 11, "priority": "P0",
        "reason": "字形验证失败",
        "reason_code": "GLYPH_FAIL",
        "original": "合谷穴", "llm": "合古穴",
        "status": "pending", "time": "2026-07-01 13:45",
    },
    {
        "id": 1029, "book_id": "B003", "book_title": "伤寒论",
        "page": 412, "line": 19, "priority": "P1",
        "reason": "禁忌词上下文破坏",
        "reason_code": "TABOO_CONTEXT",
        "original": "十九畏：硫磺原是火中精", "llm": "十九畏：硫磺原是火中金",
        "status": "resolved", "time": "2026-07-01 10:25",
    },
    {
        "id": 1030, "book_id": "B001", "book_title": "中医内科学",
        "page": 498, "line": 8, "priority": "P2",
        "reason": "LLM过度改写(长度>30%)",
        "reason_code": "LLM_OVER_REWRITE",
        "original": "脾胃不和", "llm": "脾脏和胃腑不和谐",
        "status": "pending", "time": "2026-07-01 09:00",
    },
]

# 书籍状态数据
DEMO_BOOKS_STATUS: List[Dict[str, Any]] = [
    {"book_id": "B001", "title": "中医内科学", "total_pages": 520, "processed": 498, "pending": 5, "accuracy": 97.1},
    {"book_id": "B002", "title": "中药学", "total_pages": 450, "processed": 420, "pending": 3, "accuracy": 96.5},
    {"book_id": "B003", "title": "伤寒论", "total_pages": 398, "processed": 398, "pending": 4, "accuracy": 95.8},
    {"book_id": "B004", "title": "金匮要略", "total_pages": 350, "processed": 340, "pending": 2, "accuracy": 97.3},
    {"book_id": "B005", "title": "温病学", "total_pages": 380, "processed": 360, "pending": 3, "accuracy": 96.0},
    {"book_id": "B006", "title": "针灸学", "total_pages": 420, "processed": 400, "pending": 2, "accuracy": 95.2},
    {"book_id": "B007", "title": "黄帝内经", "total_pages": 500, "processed": 480, "pending": 3, "accuracy": 94.8},
    {"book_id": "B008", "title": "方剂学", "total_pages": 360, "processed": 340, "pending": 2, "accuracy": 96.7},
    {"book_id": "B009", "title": "中医诊断学", "total_pages": 330, "processed": 310, "pending": 1, "accuracy": 97.5},
    {"book_id": "B010", "title": "中医妇科学", "total_pages": 280, "processed": 200, "pending": 4, "accuracy": 95.5},
    {"book_id": "B011", "title": "中医儿科学", "total_pages": 260, "processed": 180, "pending": 3, "accuracy": 94.9},
    {"book_id": "B012", "title": "经络腧穴学", "total_pages": 310, "processed": 220, "pending": 5, "accuracy": 96.3},
]

# LLM 高频错误模式
DEMO_LLM_ERROR_PATTERNS: List[Dict[str, Any]] = [
    {"pattern": "忌服→服用", "description": "否定词'忌'被删除，安全性完全反转", "count": 12, "severity": "high"},
    {"pattern": "附子→父子", "description": "毒性药材名称被误识别为常见词", "count": 8, "severity": "high"},
    {"pattern": "白术→白木", "description": "中药名形近字误识别", "count": 15, "severity": "medium"},
    {"pattern": "不可→可以", "description": "否定词完整性破坏，医嘱反转", "count": 10, "severity": "high"},
    {"pattern": "禁用→慎用", "description": "禁忌等级被降级，安全风险", "count": 7, "severity": "high"},
    {"pattern": "川芎→川弓", "description": "中药名形近字误识别", "count": 11, "severity": "medium"},
    {"pattern": "大黄后下→大黄先煎", "description": "煎药方法被错误修改", "count": 6, "severity": "high"},
    {"pattern": "桂枝汤主之→桂枝汤主治", "description": "古文表述被过度现代化", "count": 9, "severity": "medium"},
    {"pattern": "舌红苔黄→舌头红色舌苔黄色", "description": "术语被过度白话展开", "count": 14, "severity": "low"},
    {"pattern": "朱砂0.5g→朱砂5g", "description": "毒性药剂量被放大10倍", "count": 5, "severity": "high"},
    {"pattern": "十八反→十八返", "description": "中药禁忌术语字形错误", "count": 8, "severity": "medium"},
    {"pattern": "气虚血瘀→气卢血瘀", "description": "医学术语形近字误识别", "count": 13, "severity": "medium"},
]

# 校对员统计
DEMO_REVIEWER_STATS: List[Dict[str, Any]] = [
    {"name": "张审校", "processed": 234, "accuracy": 98.1, "avg_time_sec": 42, "today_processed": 18},
    {"name": "李审校", "processed": 189, "accuracy": 95.3, "avg_time_sec": 38, "today_processed": 22},
    {"name": "王审校", "processed": 156, "accuracy": 97.8, "avg_time_sec": 55, "today_processed": 15},
    {"name": "赵审校", "processed": 201, "accuracy": 96.5, "avg_time_sec": 35, "today_processed": 20},
    {"name": "刘审校", "processed": 145, "accuracy": 98.5, "avg_time_sec": 48, "today_processed": 12},
    {"name": "陈审校", "processed": 178, "accuracy": 94.9, "avg_time_sec": 40, "today_processed": 16},
]

# OCR 引擎统计
DEMO_ENGINE_STATS: Dict[str, Any] = {
    "mineru": {"total": 45231, "accuracy": 94.2, "avg_time_ms": 120, "errors": 2685},
    "paddleocr": {"total": 45231, "accuracy": 93.8, "avg_time_ms": 80, "errors": 2847},
    "easyocr": {"total": 3847, "accuracy": 91.5, "avg_time_ms": 250, "errors": 327},
    "tesseract": {"total": 5200, "accuracy": 88.3, "avg_time_ms": 350, "errors": 608},
}


# =============================================================================
# 辅助函数
# =============================================================================

def _get_priority_color(priority: str) -> str:
    """获取优先级对应的颜色"""
    colors = {
        "P0": "#ff3b30",
        "P1": "#ff9500",
        "P2": "#ffcc00",
        "P3": "#007aff",
    }
    return colors.get(priority, "#666666")


def _get_severity_color(severity: str) -> str:
    """获取严重程度对应的颜色"""
    colors = {
        "high": "#ff3b30",
        "medium": "#ff9500",
        "low": "#ffcc00",
    }
    return colors.get(severity, "#666666")


def _generate_daily_trend(days: int = 7) -> Dict[str, Any]:
    """生成每日处理趋势数据"""
    base_date = datetime(2026, 7, 6)
    dates = []
    processed = []
    resolved = []
    p0_found = []

    for i in range(days - 1, -1, -1):
        date = base_date - timedelta(days=i)
        dates.append(date.strftime("%Y-%m-%d"))
        # 使用固定种子确保可重现的随机数据
        seed = int(date.strftime("%Y%m%d"))
        rng = random.Random(seed)
        processed.append(rng.randint(35, 70))
        resolved.append(rng.randint(30, 65))
        p0_found.append(rng.randint(1, 6))

    return {
        "dates": dates,
        "processed": processed,
        "resolved": resolved,
        "p0_found": p0_found,
    }


# =============================================================================
# 路由定义
# =============================================================================


@router.get("/stats")
async def get_dashboard_stats() -> Dict[str, Any]:
    """获取 Dashboard 总体统计"""
    # 从模拟数据计算统计值
    total_decisions = len(DEMO_PUSH_DECISIONS)
    pending_count = sum(1 for d in DEMO_PUSH_DECISIONS if d["status"] == "pending")
    resolved_count = sum(1 for d in DEMO_PUSH_DECISIONS if d["status"] == "resolved")
    p0_count = sum(1 for d in DEMO_PUSH_DECISIONS if d["priority"] == "P0")

    # 计算书籍总数和页面/行数
    total_books = len(DEMO_BOOKS_STATUS)
    total_pages = sum(b["processed"] for b in DEMO_BOOKS_STATUS)
    # 模拟每页平均约12行
    total_lines = total_pages * 12

    # 计算今日解决数
    resolved_today = sum(1 for d in DEMO_PUSH_DECISIONS
                        if d["status"] == "resolved" and "2026-07-06" in d["time"])

    # 准确率取平均值
    avg_accuracy = round(sum(b["accuracy"] for b in DEMO_BOOKS_STATUS) / len(DEMO_BOOKS_STATUS), 1)

    return {
        "total_books": total_books,
        "total_pages_processed": total_pages,
        "total_lines_processed": total_lines,
        "pending_decisions": pending_count,
        "resolved_today": resolved_today if resolved_today else 156,
        "p0_intercepted": p0_count,
        "avg_accuracy": avg_accuracy,
        "avg_processing_time_ms": 1250,
        "total_decisions": total_decisions,
        "resolved_total": resolved_count,
        "engine_count": len(DEMO_ENGINE_STATS),
        "reviewer_count": len(DEMO_REVIEWER_STATS),
        "active_books": sum(1 for b in DEMO_BOOKS_STATUS if b["pending"] > 0),
    }


@router.get("/decisions-by-priority")
async def get_decisions_by_priority() -> Dict[str, Any]:
    """按优先级分布的推送决策"""
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for d in DEMO_PUSH_DECISIONS:
        if d["priority"] in counts:
            counts[d["priority"]] += 1

    return {
        "labels": ["P0（严重）", "P1（重要）", "P2（一般）", "P3（提示）"],
        "values": [counts["P0"], counts["P1"], counts["P2"], counts["P3"]],
        "raw": counts,
        "colors": ["#ff3b30", "#ff9500", "#ffcc00", "#007aff"],
    }


@router.get("/decisions-by-reason")
async def get_decisions_by_reason() -> List[Dict[str, Any]]:
    """按推送原因分布"""
    reason_counts: Dict[str, Dict[str, Any]] = {}

    for d in DEMO_PUSH_DECISIONS:
        reason = d["reason"]
        if reason not in reason_counts:
            reason_counts[reason] = {
                "reason": reason,
                "reason_code": d["reason_code"],
                "count": 0,
                "priority": d["priority"],
                "color": _get_priority_color(d["priority"]),
            }
        reason_counts[reason]["count"] += 1

    # 转换为列表并按数量降序排列
    result = sorted(reason_counts.values(), key=lambda x: x["count"], reverse=True)

    # 补充更多原因类型以达到15种
    additional_reasons = [
        {"reason": "毒性药材剂量越界", "reason_code": "TOXIC_DOSAGE", "count": 6, "priority": "P0", "color": "#ff3b30"},
        {"reason": "十八反/十九畏违规", "reason_code": "CONTRA_INDICATION", "count": 5, "priority": "P0", "color": "#ff3b30"},
        {"reason": "特殊人群禁忌破坏", "reason_code": "SPECIAL_GROUP_TABOO", "count": 4, "priority": "P0", "color": "#ff3b30"},
        {"reason": "药材基原错误", "reason_code": "HERB_ORIGIN_ERROR", "count": 7, "priority": "P1", "color": "#ff9500"},
        {"reason": "煎服法关键信息丢失", "reason_code": "DECOCTION_LOST", "count": 8, "priority": "P1", "color": "#ff9500"},
        {"reason": "古籍引文完整性破坏", "reason_code": "CLASSIC_QUOTE_LOST", "count": 3, "priority": "P2", "color": "#ffcc00"},
        {"reason": "数字/剂量识别异常", "reason_code": "NUMBER_ANOMALY", "count": 9, "priority": "P1", "color": "#ff9500"},
        {"reason": "辨证分型错误", "reason_code": "SYNDROME_ERROR", "count": 4, "priority": "P2", "color": "#ffcc00"},
    ]

    # 合并现有数据和补充数据
    existing_reasons = {r["reason"] for r in result}
    for ar in additional_reasons:
        if ar["reason"] not in existing_reasons:
            result.append(ar)

    return sorted(result, key=lambda x: x["count"], reverse=True)


@router.get("/daily-trend")
async def get_daily_trend(days: int = Query(7, ge=1, le=30)) -> Dict[str, Any]:
    """每日处理趋势"""
    return _generate_daily_trend(days)


@router.get("/books-status")
async def get_books_status() -> List[Dict[str, Any]]:
    """各书籍处理状态"""
    result = []
    for book in DEMO_BOOKS_STATUS:
        progress = round((book["processed"] / book["total_pages"]) * 100, 1)
        status = "completed" if book["processed"] >= book["total_pages"] else "processing"
        result.append({
            **book,
            "progress": progress,
            "status": status,
            "pending_count": book["pending"],
        })
    return sorted(result, key=lambda x: x["progress"], reverse=True)


@router.get("/recent-decisions")
async def get_recent_decisions(
    limit: int = Query(10, ge=1, le=30),
    status_filter: str = Query(None, description="按状态筛选"),
) -> List[Dict[str, Any]]:
    """最近的推送决策"""
    decisions = sorted(DEMO_PUSH_DECISIONS, key=lambda x: x["time"], reverse=True)

    if status_filter:
        decisions = [d for d in decisions if d["status"] == status_filter]

    result = []
    for d in decisions[:limit]:
        result.append({
            "id": d["id"],
            "book_title": d["book_title"],
            "page": d["page"],
            "line": d["line"],
            "priority": d["priority"],
            "priority_color": _get_priority_color(d["priority"]),
            "reason": d["reason"],
            "original": d["original"],
            "llm": d["llm"],
            "status": d["status"],
            "time": d["time"],
            "reason_code": d["reason_code"],
        })
    return result


@router.get("/llm-error-patterns")
async def get_llm_error_patterns() -> List[Dict[str, Any]]:
    """LLM 高频错误模式"""
    result = []
    for pattern in DEMO_LLM_ERROR_PATTERNS:
        result.append({
            **pattern,
            "severity_color": _get_severity_color(pattern["severity"]),
        })
    return sorted(result, key=lambda x: x["count"], reverse=True)


@router.get("/engine-stats")
async def get_engine_stats() -> Dict[str, Any]:
    """各 OCR 引擎统计"""
    engines = []
    for name, stats in DEMO_ENGINE_STATS.items():
        engines.append({
            "name": name,
            "display_name": name.upper() if name == "mineru" else name.title(),
            **stats,
        })

    return {
        "engines": engines,
        "total_processed": sum(e["total"] for e in engines),
        "avg_accuracy": round(
            sum(e["accuracy"] * e["total"] for e in engines)
            / sum(e["total"] for e in engines),
            1,
        ),
    }


@router.get("/reviewer-stats")
async def get_reviewer_stats() -> List[Dict[str, Any]]:
    """校对员统计"""
    return sorted(
        DEMO_REVIEWER_STATS,
        key=lambda x: x["processed"],
        reverse=True,
    )


@router.get("/quality-metrics")
async def get_quality_metrics() -> Dict[str, Any]:
    """系统质量指标"""
    # 计算各指标
    decisions = DEMO_PUSH_DECISIONS
    total = len(decisions)
    p0_count = sum(1 for d in decisions if d["priority"] == "P0")
    sum(1 for d in decisions if d["priority"] == "P1")

    # 模拟历史数据用于计算趋势
    return {
        "precision": 96.2,
        "recall": 94.8,
        "f1_score": 95.5,
        "false_positive_rate": 3.8,
        "false_negative_rate": 5.2,
        "p0_capture_rate": 99.2,
        "p1_capture_rate": 97.5,
        "avg_resolution_time_sec": 42,
        "p0_trend": "stable",  # stable, rising, falling
        "accuracy_trend": "rising",
        "resolution_speed_trend": "improving",
        "weekly_comparison": {
            "this_week_decisions": total,
            "last_week_decisions": total - 5,
            "this_week_p0": p0_count,
            "last_week_p0": p0_count - 2,
            "accuracy_change": +1.2,
            "speed_change": -8,
        },
    }


@router.get("/system-health")
async def get_system_health() -> Dict[str, Any]:
    """系统健康状态"""
    return {
        "status": "healthy",  # healthy, warning, critical
        "services": [
            {"name": "OCR Pipeline", "status": "running", "latency_ms": 1250, "uptime": "99.9%"},
            {"name": "LLM Service", "status": "running", "latency_ms": 850, "uptime": "99.7%"},
            {"name": "Consensus Engine", "status": "running", "latency_ms": 320, "uptime": "99.9%"},
            {"name": "Push Decision Logger", "status": "running", "latency_ms": 45, "uptime": "100%"},
            {"name": "Reviewer Workbench", "status": "running", "latency_ms": 120, "uptime": "99.8%"},
            {"name": "Database", "status": "running", "latency_ms": 15, "uptime": "100%"},
        ],
        "alerts": [
            {"level": "warning", "message": "P0决策队列中有4条超过24小时未处理", "time": "2026-07-06 14:00"},
            {"level": "info", "message": "今日处理量较昨日增加23%", "time": "2026-07-06 10:00"},
        ],
        "last_updated": "2026-07-06 14:32:18",
    }
