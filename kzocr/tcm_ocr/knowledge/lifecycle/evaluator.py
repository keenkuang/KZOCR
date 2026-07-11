"""
知识库生命周期评估模块

负责定期评估知识库中规则的质量和有效性，包括：
- 长期零触发的规则废弃
- 低准确率规则降级或废弃
- 药典版本过期检测
- 连续下降趋势标记审查

评估流程：
1. 遍历所有活跃规则（HerbOCRPattern / MeridianPointOCRPattern / FormulaContextPattern）
2. 跳过 is_permanent=True 且 review_status='approved' 的规则
3. 180 天零触发 → 废弃 (deprecate)
4. 准确率 < 0.5 → 废弃 (deprecate)
5. 准确率 < 0.7 → 降级 scope (demote)
6. 连续 3 次下降趋势 → 标记审查 (flag_review)
7. 药典版本过期 → 降级 scope (demote)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)

# 评估阈值常量
ZERO_TRIGGER_DAYS = 180       # 零触发天数阈值
ACCURACY_DEPRECATE = 0.5      # 废弃准确率阈值
ACCURACY_DEMOTE = 0.7         # 降级准确率阈值
DECLINE_STREAK_THRESHOLD = 3  # 连续下降次数阈值


def _calculate_accuracy(pattern: dict) -> float:
    """
    计算范式的历史准确率

    准确率 = 成功纠错次数 / 总触发次数
    如果没有触发记录，返回 1.0（默认保留）

    Args:
        pattern: 范式记录字典

    Returns:
        准确率浮点数 (0.0 ~ 1.0)
    """
    total_hits = pattern.get('hit_count', 0) + pattern.get('miss_count', 0)
    if total_hits == 0:
        return 1.0
    hit_count = pattern.get('hit_count', 0)
    return hit_count / total_hits


def _days_since_last_trigger(pattern: dict) -> int:
    """
    计算自上次触发以来的天数

    Args:
        pattern: 范式记录字典

    Returns:
        天数（整数），从未触发返回 9999
    """
    last_triggered = pattern.get('last_triggered_at')
    if last_triggered is None:
        return 9999

    if isinstance(last_triggered, str):
        # 解析 ISO 格式字符串
        try:
            last_triggered = datetime.fromisoformat(last_triggered.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return 9999

    now = datetime.now(timezone.utc)
    if last_triggered.tzinfo is None:
        last_triggered = last_triggered.replace(tzinfo=timezone.utc)

    delta = now - last_triggered
    return max(0, int(delta.total_seconds() / 86400))


def _has_declining_trend(pattern: dict) -> bool:
    """
    检查范式是否有连续下降趋势

    查看最近 3 次评估周期的准确率是否连续下降。

    Args:
        pattern: 范式记录字典

    Returns:
        True 如果连续 3 次下降
    """
    accuracy_history = pattern.get('accuracy_history', [])
    if not isinstance(accuracy_history, list) or len(accuracy_history) < DECLINE_STREAK_THRESHOLD:
        return False

    # 取最近 N 个数据点
    recent = accuracy_history[-DECLINE_STREAK_THRESHOLD:]

    # 检查是否严格递减
    for i in range(1, len(recent)):
        if recent[i] >= recent[i - 1]:
            return False
    return True


def _is_pharmacopoeia_expired(pattern: dict, db: RuntimeDB) -> bool:
    """
    检查范式关联的药典版本是否过期

    Args:
        pattern: 范式记录字典
        db: RuntimeDB 实例

    Returns:
        True 如果药典版本已过期
    """
    pharmacopoeia_version = pattern.get('pharmacopoeia_version')
    if not pharmacopoeia_version:
        return False

    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT is_current, expiry_year
                FROM PharmacopoeiaTimeline
                WHERE pharmacopoeia_version = %s
                """,
                (pharmacopoeia_version,),
            )
            result = cursor.fetchone()
            if not result:
                return False

            if result.get('is_current'):
                return False

            expiry_year = result.get('expiry_year')
            if expiry_year is None:
                return False

            current_year = datetime.now().year
            return current_year > expiry_year
    except Exception as e:
        logger.error("Error checking pharmacopoeia version: %s", e)
        return False


def _update_pattern_status(
    db: RuntimeDB,
    table_name: str,
    pattern_id: int,
    new_status: str,
    reason: str,
) -> bool:
    """
    更新范式状态

    Args:
        db: RuntimeDB 实例
        table_name: 表名
        pattern_id: 范式 ID
        new_status: 新状态
        reason: 变更原因

    Returns:
        是否更新成功
    """
    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET review_status = %s,
                    status = CASE WHEN %s = 'deprecated' THEN 'inactive' ELSE status END,
                    notes = COALESCE(notes, '') || %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (new_status, new_status, f" | {reason}", pattern_id),
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to update %s id=%d: %s", table_name, pattern_id, e)
        return False


def _demote_pattern_scope(
    db: RuntimeDB,
    table_name: str,
    pattern_id: int,
    current_scope: str,
) -> bool:
    """
    降级范式的 scope 级别

    降级顺序: book → publisher → era → global

    Args:
        db: RuntimeDB 实例
        table_name: 表名
        pattern_id: 范式 ID
        current_scope: 当前 scope

    Returns:
        是否降级成功
    """
    scope_downgrade = {
        'book': 'publisher',
        'publisher': 'era',
        'era': 'global',
        'global': 'global',
    }
    new_scope = scope_downgrade.get(current_scope, 'global')

    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {table_name}
                SET scope = %s,
                    notes = COALESCE(notes, '') || %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (new_scope, f" | Scope demoted from {current_scope} to {new_scope}", pattern_id),
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error("Failed to demote scope for %s id=%d: %s", table_name, pattern_id, e)
        return False


def _fetch_patterns(db: RuntimeDB, table_name: str) -> List[dict]:
    """
    从指定表获取所有活跃范式

    Args:
        db: RuntimeDB 实例
        table_name: 表名

    Returns:
        范式记录列表
    """
    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {table_name}
                WHERE status = 'active'
                ORDER BY id
                """,
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Failed to fetch patterns from %s: %s", table_name, e)
        return []


def _evaluate_single_pattern(
    pattern: dict,
    table_name: str,
    db: RuntimeDB,
) -> List[str]:
    """
    评估单个范式，返回执行的操作列表

    Args:
        pattern: 范式记录字典
        table_name: 表名
        db: RuntimeDB 实例

    Returns:
        执行的操作描述列表
    """
    actions: List[str] = []
    pattern_id = pattern['id']

    # 1. 跳过 is_permanent=True 且 approved 的规则
    is_permanent = pattern.get('is_permanent', False)
    review_status = pattern.get('review_status', '')
    if is_permanent and review_status == 'approved':
        return actions  # 空列表，无操作

    # 2. 检查 180 天零触发
    days_since = _days_since_last_trigger(pattern)
    if days_since >= ZERO_TRIGGER_DAYS:
        _update_pattern_status(db, table_name, pattern_id, 'deprecated',
                               f"Zero trigger for {days_since} days")
        actions.append(f"deprecated: zero_trigger_{days_since}d")
        return actions

    # 3. 检查准确率
    accuracy = _calculate_accuracy(pattern)
    if accuracy < ACCURACY_DEPRECATE:
        _update_pattern_status(db, table_name, pattern_id, 'deprecated',
                               f"Accuracy {accuracy:.2f} below threshold {ACCURACY_DEPRECATE}")
        actions.append(f"deprecated: accuracy_{accuracy:.2f}")
        return actions

    if accuracy < ACCURACY_DEMOTE:
        current_scope = pattern.get('scope', 'global')
        _demote_pattern_scope(db, table_name, pattern_id, current_scope)
        actions.append(f"demoted: accuracy_{accuracy:.2f}")

    # 4. 检查连续下降趋势
    if _has_declining_trend(pattern):
        _update_pattern_status(db, table_name, pattern_id, 'flagged_review',
                               f"Declining trend for {DECLINE_STREAK_THRESHOLD} consecutive periods")
        actions.append("flagged_review: declining_trend")

    # 5. 检查药典版本过期（仅 HerbOCRPattern）
    if table_name == 'HerbOCRPattern':
        if _is_pharmacopoeia_expired(pattern, db):
            current_scope = pattern.get('scope', 'global')
            _demote_pattern_scope(db, table_name, pattern_id, current_scope)
            actions.append("demoted: expired_pharmacopoeia")

    return actions


def evaluate_error_patterns(db: RuntimeDB) -> Dict[str, Any]:
    """
    评估所有错误模式范式，执行生命周期管理

    对 HerbOCRPattern、MeridianPointOCRPattern、FormulaContextPattern
    三个表中的活跃规则进行全面评估。

    评估逻辑：
        1. 遍历所有活跃规则
        2. 跳过 is_permanent=True 且 review_status='approved' 的规则
        3. 180 天零触发 → 废弃
        4. 准确率 < 0.5 → 废弃
        5. 准确率 < 0.7 → 降级 scope
        6. 连续 3 次下降趋势 → 标记审查
        7. 药典版本过期 → 降级 scope（仅 HerbOCRPattern）

    Args:
        db: RuntimeDB 实例

    Returns:
        评估结果摘要字典::

            {
                'total_evaluated': int,
                'tables': {
                    'HerbOCRPattern': {
                        'evaluated': int,
                        'deprecated': int,
                        'demoted': int,
                        'flagged': int,
                        'skipped': int,
                    },
                    'MeridianPointOCRPattern': { ... },
                    'FormulaContextPattern': { ... },
                },
                'details': List[str],
            }
    """
    tables = [
        'HerbOCRPattern',
        'MeridianPointOCRPattern',
        'FormulaContextPattern',
    ]

    summary: Dict[str, Any] = {
        'total_evaluated': 0,
        'tables': {},
        'details': [],
    }

    for table_name in tables:
        table_summary = {
            'evaluated': 0,
            'deprecated': 0,
            'demoted': 0,
            'flagged': 0,
            'skipped': 0,
        }

        patterns = _fetch_patterns(db, table_name)
        logger.info("Evaluating %d patterns from %s", len(patterns), table_name)

        for pattern in patterns:
            table_summary['evaluated'] += 1
            summary['total_evaluated'] += 1

            # 跳过的规则
            is_permanent = pattern.get('is_permanent', False)
            review_status = pattern.get('review_status', '')
            if is_permanent and review_status == 'approved':
                table_summary['skipped'] += 1
                continue

            # 执行评估
            actions = _evaluate_single_pattern(pattern, table_name, db)

            for action in actions:
                summary['details'].append(f"{table_name}#{pattern['id']}: {action}")
                if action.startswith('deprecated'):
                    table_summary['deprecated'] += 1
                elif action.startswith('demoted'):
                    table_summary['demoted'] += 1
                elif action.startswith('flagged_review'):
                    table_summary['flagged'] += 1

        summary['tables'][table_name] = table_summary
        logger.info(
            "%s evaluation complete: %d evaluated, %d deprecated, %d demoted, %d flagged, %d skipped",
            table_name, table_summary['evaluated'], table_summary['deprecated'],
            table_summary['demoted'], table_summary['flagged'], table_summary['skipped'],
        )

    return summary
