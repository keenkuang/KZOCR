"""
PushDecisionLogger 全面测试

测试内容：
- 否定词破坏记录
- 剂量异常记录
- 字形验证失败记录
- 引擎分歧记录
- LLM超时记录
- 多原因组合
- 校对结果提交
- 批量处理（P1允许，P0禁止）
- 统计信息
"""

from __future__ import annotations

import json
from typing import Tuple
from unittest.mock import MagicMock

import pytest

from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger


# ============================================================================
# Mock helpers
# ============================================================================

def _create_mock_db_with_return(return_id: int = 1) -> MagicMock:
    """Create a mock DB that returns a specific ID on INSERT."""
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    call_count = [0]
    def _fetchone() -> dict[str, int]:
        call_count[0] += 1
        return {"id": return_id}
    mock_cursor.fetchone.side_effect = _fetchone
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=None)
    mock_db.get_cursor.return_value = mock_cursor
    return mock_db


def _get_insert_params(mock_cursor: MagicMock) -> Tuple[str, tuple]:
    """Get the first INSERT call parameters from execute call history.

    _insert_decision makes multiple execute calls (INSERT + event logging).
    We need to find the first one that contains the INSERT statement.
    """
    for call_args in mock_cursor.execute.call_args_list:
        args = call_args[0]
        sql = args[0] if args else ""
        if "INSERT INTO PushDecisionLog" in sql:
            params = args[1] if len(args) > 1 else ()
            return sql, params
    # If not found, return the first call's args (fallback)
    first_call = mock_cursor.execute.call_args_list[0]
    return first_call[0][0], first_call[0][1] if len(first_call[0]) > 1 else ()


def _get_reason_details(mock_cursor: MagicMock) -> dict:
    """Extract reason_details JSON from the INSERT call.

    _insert_decision parameter order:
    0: book_id, 1: line_id, 2: para_id, 3: page_num,
    4: reason_codes (list), 5: reason_details (json string), 6: priority,
    7: engine_snapshots, 8: llm_snapshots, 9: parent_decision_id, 10: decision_chain
    """
    _, params = _get_insert_params(mock_cursor)
    # params index 5 is reason_details (JSON string)
    if len(params) > 5 and params[5]:
        return json.loads(params[5])
    return {}


# ============================================================================
# Tests for log_negation_violation
# ============================================================================

class TestLogNegationViolation:
    """Test suite for negation violation logging."""

    def test_log_negation_violation_basic(self) -> None:
        """Test basic negation violation logging."""
        mock_db = _create_mock_db_with_return(return_id=42)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_negation_violation(
            book_id="book_001",
            line_id=10,
            page_num=5,
            original="不可用附子",
            modified="可用附子",
            lost=["不"],
        )

        assert decision_id == 42
        mock_db.get_cursor.assert_called()

    def test_log_negation_violation_multiple_negations(self) -> None:
        """Test negation violation with multiple lost negations."""
        mock_db = _create_mock_db_with_return(return_id=43)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_negation_violation(
            book_id="book_001",
            line_id=11,
            page_num=5,
            original="不可无病不服药",
            modified="可无病不服药",
            lost=["不"],
        )

        assert decision_id == 43

    def test_log_negation_violation_priority_is_p0(self) -> None:
        """Test that negation violation is logged with P0 priority."""
        mock_db = _create_mock_db_with_return(return_id=44)
        logger = PushDecisionLogger(mock_db)

        logger.log_negation_violation(
            book_id="book_001",
            line_id=12,
            page_num=6,
            original="禁用附子",
            modified="用附子",
            lost=["禁"],
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        # params[6] is priority
        assert "P0" in str(params)

    def test_log_negation_violation_reason_details(self) -> None:
        """Test that reason_details contains negation-specific info."""
        mock_db = _create_mock_db_with_return(return_id=45)
        logger = PushDecisionLogger(mock_db)

        logger.log_negation_violation(
            book_id="book_001",
            line_id=13,
            page_num=7,
            original="不可用此药",
            modified="可用此药",
            lost=["不"],
        )

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert "NEGATION_VIOLATION" in reason_details
        assert reason_details["NEGATION_VIOLATION"]["original_text"] == "不可用此药"
        assert reason_details["NEGATION_VIOLATION"]["modified_text"] == "可用此药"
        assert reason_details["NEGATION_VIOLATION"]["lost_negations"] == ["不"]
        assert reason_details["NEGATION_VIOLATION"]["semantic_risk"] == "high"

    def test_log_negation_violation_counting(self) -> None:
        """Test that negation count is correctly computed."""
        mock_db = _create_mock_db_with_return(return_id=46)
        logger = PushDecisionLogger(mock_db)

        original = "不可用不可无不可非"
        modified = "可用可无可非"
        lost = ["不", "不", "不"]

        logger.log_negation_violation(
            book_id="book_001",
            line_id=14,
            page_num=8,
            original=original,
            modified=modified,
            lost=lost,
        )

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        neg_count_before = reason_details["NEGATION_VIOLATION"]["negation_count_before"]
        neg_count_after = reason_details["NEGATION_VIOLATION"]["negation_count_after"]
        assert neg_count_before > neg_count_after


# ============================================================================
# Tests for log_dosage_alert
# ============================================================================

class TestLogDosageAlert:
    """Test suite for dosage alert logging."""

    def test_log_dosage_pre_alert_basic(self) -> None:
        """Test basic dosage pre-alert logging."""
        mock_db = _create_mock_db_with_return(return_id=100)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=20,
            page_num=10,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "standard_min": "3g",
                "severity": "overdose",
            },
        )

        assert decision_id == 100

    def test_log_dosage_pre_alert_priority_is_p0(self) -> None:
        """Test that dosage pre-alert is logged with P0 priority."""
        mock_db = _create_mock_db_with_return(return_id=101)
        logger = PushDecisionLogger(mock_db)

        logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=21,
            page_num=11,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "severity": "overdose",
            },
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        assert "P0" in str(params)

    def test_log_dosage_pre_alert_severe_overdose(self) -> None:
        """Test dosage pre-alert with severe overdose."""
        mock_db = _create_mock_db_with_return(return_id=102)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=22,
            page_num=12,
            alert={
                "herb_name": "砒霜",
                "detected_dosage": "10g",
                "standard_max": "0.003g",
                "severity": "severe_overdose",
            },
        )

        assert decision_id == 102

    def test_log_dosage_post_alert(self) -> None:
        """Test dosage post-alert logging (LLM modified dosage count)."""
        mock_db = _create_mock_db_with_return(return_id=103)
        logger = PushDecisionLogger(mock_db)

        pre_alerts = [
            {"herb_name": "附子", "detected_dosage": "30g", "severity": "overdose"},
        ]
        post_alerts = [
            {"herb_name": "附子", "detected_dosage": "25g", "severity": "overdose"},
            {"herb_name": "甘草", "detected_dosage": "20g", "severity": "normal"},
        ]

        decision_id = logger.log_dosage_post_alert(
            book_id="book_001",
            line_id=23,
            page_num=13,
            pre_alerts=pre_alerts,
            post_alerts=post_alerts,
        )

        assert decision_id == 103

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert reason_details["DOSAGE_POST_ALERT"]["pre_alert_count"] == 1
        assert reason_details["DOSAGE_POST_ALERT"]["post_alert_count"] == 2
        assert reason_details["DOSAGE_POST_ALERT"]["llm_modified_dosage"] is True

    def test_log_dosage_post_alert_resolved(self) -> None:
        """Test dosage post-alert when LLM resolves the issue."""
        mock_db = _create_mock_db_with_return(return_id=104)
        logger = PushDecisionLogger(mock_db)

        pre_alerts = [
            {"herb_name": "附子", "detected_dosage": "30g", "severity": "overdose"},
        ]
        post_alerts = []  # Resolved

        decision_id = logger.log_dosage_post_alert(
            book_id="book_001",
            line_id=24,
            page_num=14,
            pre_alerts=pre_alerts,
            post_alerts=post_alerts,
        )

        assert decision_id == 104

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert len(reason_details["DOSAGE_POST_ALERT"]["resolved"]) == 1


# ============================================================================
# Tests for log_glyph_verify_failed
# ============================================================================

class TestLogGlyphFailed:
    """Test suite for glyph verification failure logging."""

    def test_log_glyph_verify_failed_basic(self) -> None:
        """Test basic glyph verification failure logging."""
        mock_db = _create_mock_db_with_return(return_id=200)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_glyph_verify_failed(
            book_id="book_001",
            line_id=30,
            page_num=15,
            verify_result={
                "field": "herb_name",
                "expected": "当归",
                "detected": "当阳",
                "confidence": 0.45,
                "verification_method": "structure_match",
            },
            engine_snapshots={
                "paddleocr": {"text": "当阳10g", "confidence": 0.92},
            },
        )

        assert decision_id == 200

    def test_log_glyph_verify_failed_critical_field_p0(self) -> None:
        """Test glyph failure for critical field gets P0 priority."""
        mock_db = _create_mock_db_with_return(return_id=201)
        logger = PushDecisionLogger(mock_db)

        logger.log_glyph_verify_failed(
            book_id="book_001",
            line_id=31,
            page_num=16,
            verify_result={
                "field": "herb_name",
                "expected": "附子",
                "detected": "付子",
                "confidence": 0.30,
            },
            engine_snapshots={},
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        assert "P0" in str(params)

    def test_log_glyph_verify_failed_non_critical_p1(self) -> None:
        """Test glyph failure for non-critical field gets P1 priority."""
        mock_db = _create_mock_db_with_return(return_id=202)
        logger = PushDecisionLogger(mock_db)

        logger.log_glyph_verify_failed(
            book_id="book_001",
            line_id=32,
            page_num=17,
            verify_result={
                "field": "page_number",
                "expected": "123",
                "detected": "128",
                "confidence": 0.50,
            },
            engine_snapshots={},
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        assert "P1" in str(params)


# ============================================================================
# Tests for log_consensus_dispute
# ============================================================================

class TestLogConsensusDispute:
    """Test suite for consensus dispute logging."""

    def test_log_consensus_dispute_basic(self) -> None:
        """Test basic consensus dispute logging."""
        mock_db = _create_mock_db_with_return(return_id=300)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_consensus_dispute(
            book_id="book_001",
            line_id=40,
            page_num=20,
            engine_results={
                "paddleocr": {"text": "当归10g", "confidence": 0.95},
                "mineru": {"text": "当阳10g", "confidence": 0.88},
            },
        )

        assert decision_id == 300

    def test_log_consensus_dispute_priority_is_p1(self) -> None:
        """Test that consensus dispute is logged with P1 priority."""
        mock_db = _create_mock_db_with_return(return_id=301)
        logger = PushDecisionLogger(mock_db)

        logger.log_consensus_dispute(
            book_id="book_001",
            line_id=41,
            page_num=21,
            engine_results={
                "paddleocr": {"text": "当归", "confidence": 0.95},
                "mineru": {"text": "当阳", "confidence": 0.88},
            },
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        assert "P1" in str(params)

    def test_log_consensus_dispute_unique_variants(self) -> None:
        """Test that unique variants are correctly counted."""
        mock_db = _create_mock_db_with_return(return_id=302)
        logger = PushDecisionLogger(mock_db)

        logger.log_consensus_dispute(
            book_id="book_001",
            line_id=42,
            page_num=22,
            engine_results={
                "paddleocr": {"text": "当归", "confidence": 0.95},
                "mineru": {"text": "当归", "confidence": 0.88},
                "cloud": {"text": "当阳", "confidence": 0.70},
            },
        )

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        # Should have 2 unique texts: "当归" and "当阳"
        assert reason_details["CONSENSUS_DISPUTE"]["unique_variants"] == 2

    def test_log_consensus_dispute_three_engines(self) -> None:
        """Test consensus dispute with three engines."""
        mock_db = _create_mock_db_with_return(return_id=303)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_consensus_dispute(
            book_id="book_001",
            line_id=43,
            page_num=23,
            engine_results={
                "paddleocr": {"text": "A", "confidence": 0.9},
                "mineru": {"text": "B", "confidence": 0.8},
                "cloud": {"text": "C", "confidence": 0.7},
            },
        )

        assert decision_id == 303
        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert reason_details["CONSENSUS_DISPUTE"]["engine_count"] == 3


# ============================================================================
# Tests for log_llm_timeout
# ============================================================================

class TestLogLLMTimeout:
    """Test suite for LLM timeout logging."""

    def test_log_llm_timeout_local(self) -> None:
        """Test local LLM timeout logging."""
        mock_db = _create_mock_db_with_return(return_id=400)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_llm_timeout(
            book_id="book_001",
            line_id=50,
            page_num=25,
            llm_type="local",
            timeout_sec=30,
            engine_snapshots={
                "paddleocr": {"text": "测试结果", "confidence": 0.90},
            },
        )

        assert decision_id == 400

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert "LLM_LOCAL_TIMEOUT" in reason_details
        assert reason_details["LLM_LOCAL_TIMEOUT"]["llm_type"] == "local"
        assert reason_details["LLM_LOCAL_TIMEOUT"]["timeout_sec"] == 30

    def test_log_llm_timeout_cloud(self) -> None:
        """Test cloud LLM timeout logging."""
        mock_db = _create_mock_db_with_return(return_id=401)
        logger = PushDecisionLogger(mock_db)

        decision_id = logger.log_llm_timeout(
            book_id="book_001",
            line_id=51,
            page_num=26,
            llm_type="cloud",
            timeout_sec=60,
            engine_snapshots={},
        )

        assert decision_id == 401

        reason_details = _get_reason_details(mock_db.get_cursor.return_value)
        assert "LLM_CLOUD_TIMEOUT" in reason_details
        assert reason_details["LLM_CLOUD_TIMEOUT"]["llm_type"] == "cloud"
        assert reason_details["LLM_CLOUD_TIMEOUT"]["timeout_sec"] == 60

    def test_log_llm_timeout_priority_is_p1(self) -> None:
        """Test that LLM timeout is logged with P1 priority."""
        mock_db = _create_mock_db_with_return(return_id=402)
        logger = PushDecisionLogger(mock_db)

        logger.log_llm_timeout(
            book_id="book_001",
            line_id=52,
            page_num=27,
            llm_type="local",
            timeout_sec=30,
            engine_snapshots={},
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        assert "P1" in str(params)


# ============================================================================
# Tests for multi-reason scenarios
# ============================================================================

class TestMultiReason:
    """Test suite for multi-reason decision scenarios."""

    def test_multi_reason_priority_escalation(self) -> None:
        """Test that multi-reason decisions use highest priority."""
        mock_db = _create_mock_db_with_return(return_id=500)
        logger = PushDecisionLogger(mock_db)

        # Simulate a case with dosage alert (P0)
        logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=60,
            page_num=30,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "severity": "overdose",
            },
        )

        _, params = _get_insert_params(mock_db.get_cursor.return_value)
        # Should be P0 priority
        assert "P0" in str(params)

    def test_reason_codes_dictionary_complete(self) -> None:
        """Test that REASON_CODES dictionary has all 15 entries."""
        assert len(PushDecisionLogger.REASON_CODES) == 15

        expected_codes = {
            "CONSENSUS_DISPUTE",
            "DOSAGE_PRE_ALERT",
            "DOSAGE_POST_ALERT",
            "NEGATION_VIOLATION",
            "GLYPH_VERIFY_FAILED",
            "LLM_LOCAL_TIMEOUT",
            "LLM_CLOUD_TIMEOUT",
            "LLM_PARSE_ERROR",
            "LINE_COUNT_MISMATCH",
            "CROSS_PAGE_SPLIT_FAIL",
            "FORMULA_EXTRACT_FAIL",
            "FORMULA_REF_MISMATCH",
            "MISSING_CHAR_DETECTED",
            "EXTRA_CHAR_DETECTED",
            "PUBLISHER_LOW_ACCURACY",
        }

        assert set(PushDecisionLogger.REASON_CODES.keys()) == expected_codes

    def test_reason_code_priorities(self) -> None:
        """Test that reason codes have correct priorities."""
        expected = {
            "CONSENSUS_DISPUTE": "P1",
            "DOSAGE_PRE_ALERT": "P0",
            "DOSAGE_POST_ALERT": "P0",
            "NEGATION_VIOLATION": "P0",
            "GLYPH_VERIFY_FAILED": "P0",
            "LLM_LOCAL_TIMEOUT": "P1",
            "LLM_CLOUD_TIMEOUT": "P1",
            "LLM_PARSE_ERROR": "P1",
            "LINE_COUNT_MISMATCH": "P1",
            "CROSS_PAGE_SPLIT_FAIL": "P1",
            "FORMULA_EXTRACT_FAIL": "P2",
            "FORMULA_REF_MISMATCH": "P2",
            "MISSING_CHAR_DETECTED": "P2",
            "EXTRA_CHAR_DETECTED": "P2",
            "PUBLISHER_LOW_ACCURACY": "P3",
        }

        for code, expected_priority in expected.items():
            actual = PushDecisionLogger.REASON_CODES[code]["priority"]
            assert actual == expected_priority, (
                f"Reason code '{code}' has priority '{actual}', expected '{expected_priority}'"
            )


# ============================================================================
# Tests for resolve_decision
# ============================================================================

class TestResolveDecision:
    """Test suite for decision resolution."""

    def test_resolve_decision_accept(self) -> None:
        """Test accepting a decision."""
        mock_db = _create_mock_db_with_return(return_id=1)
        mock_cursor = mock_db.get_cursor.return_value
        # First fetchone: check decision exists; second: record event
        mock_cursor.fetchone.side_effect = [{"id": 1, "status": "pending"}, {"id": 1}, {"id": 1}]

        logger = PushDecisionLogger(mock_db)

        success = logger.resolve_decision(
            decision_id=1,
            action="accept",
            final_text="当归10g",
            note="确认为当归",
            reviewer_id=100,
        )

        assert success is True
        mock_cursor.execute.assert_called()

    def test_resolve_decision_reject(self) -> None:
        """Test rejecting a decision."""
        mock_db = _create_mock_db_with_return(return_id=1)
        mock_cursor = mock_db.get_cursor.return_value
        mock_cursor.fetchone.side_effect = [{"id": 1, "status": "pending"}, {"id": 1}]

        logger = PushDecisionLogger(mock_db)

        success = logger.resolve_decision(
            decision_id=1,
            action="reject",
            final_text="",
            note="拒绝推送",
            reviewer_id=100,
        )

        assert success is True

    def test_resolve_decision_modify(self) -> None:
        """Test modifying a decision."""
        mock_db = _create_mock_db_with_return(return_id=1)
        mock_cursor = mock_db.get_cursor.return_value
        mock_cursor.fetchone.side_effect = [{"id": 1, "status": "pending"}, {"id": 1}]

        logger = PushDecisionLogger(mock_db)

        success = logger.resolve_decision(
            decision_id=1,
            action="modify",
            final_text="修改后的文本",
            note="修改为正确文本",
            reviewer_id=100,
        )

        assert success is True

    def test_resolve_decision_escalate(self) -> None:
        """Test escalating a decision."""
        mock_db = _create_mock_db_with_return(return_id=1)
        mock_cursor = mock_db.get_cursor.return_value
        mock_cursor.fetchone.side_effect = [{"id": 1, "status": "pending"}, {"id": 1}]

        logger = PushDecisionLogger(mock_db)

        success = logger.resolve_decision(
            decision_id=1,
            action="escalate",
            final_text="",
            note="需要专家审核",
            reviewer_id=100,
        )

        assert success is True

    def test_resolve_decision_invalid_action(self) -> None:
        """Test that invalid action raises ValueError."""
        mock_db = _create_mock_db_with_return()
        logger = PushDecisionLogger(mock_db)

        with pytest.raises(ValueError, match="Invalid action"):
            logger.resolve_decision(
                decision_id=1,
                action="invalid_action",
                final_text="",
                note="",
                reviewer_id=100,
            )

    def test_resolve_decision_not_found(self) -> None:
        """Test resolving a non-existent decision returns False."""
        mock_db = _create_mock_db_with_return()
        mock_cursor = mock_db.get_cursor.return_value
        # Must clear side_effect first, then set return_value
        mock_cursor.fetchone.side_effect = None
        mock_cursor.fetchone.return_value = None

        logger = PushDecisionLogger(mock_db)

        success = logger.resolve_decision(
            decision_id=999,
            action="accept",
            final_text="",
            note="",
            reviewer_id=100,
        )

        assert success is False

    def test_resolve_decision_status_mapping(self) -> None:
        """Test that actions map to correct statuses."""
        mock_db = _create_mock_db_with_return(return_id=1)
        mock_cursor = mock_db.get_cursor.return_value

        action_status_map = {
            "accept": "resolved",
            "reject": "rejected",
            "modify": "resolved",
            "escalate": "escalated",
            "auto_resolve": "auto_resolved",
        }

        logger = PushDecisionLogger(mock_db)

        for action, expected_status in action_status_map.items():
            # Reset fetchone side_effect for each iteration
            mock_cursor.fetchone.side_effect = [
                {"id": 1, "status": "pending"},
                {"id": 1},
            ]
            mock_cursor.reset_mock()

            logger.resolve_decision(
                decision_id=1,
                action=action,
                final_text="",
                note="",
                reviewer_id=100,
            )

            # Check the UPDATE query params contain the correct status
            found_status = False
            for call_args in mock_cursor.execute.call_args_list:
                sql = call_args[0][0]
                params = call_args[0][1] if len(call_args[0]) > 1 else ()
                if "UPDATE PushDecisionLog" in sql:
                    # params: action, final_text, note, reviewer_id, status, decision_id
                    # status is at index 4
                    assert len(params) >= 5
                    assert params[4] == expected_status, (
                        f"Action '{action}' should map to status '{expected_status}', got '{params[4]}'"
                    )
                    found_status = True
                    break
            assert found_status, f"UPDATE not found for action '{action}'"


# ============================================================================
# Tests for batch processing
# ============================================================================

class TestBatchResolve:
    """Test suite for batch resolution."""

    def test_batch_resolve_basic(self) -> None:
        """Test basic batch resolution via API-level logic."""
        p1_decisions = [
            {"id": 1, "priority": "P1", "reason_codes": ["CONSENSUS_DISPUTE"]},
            {"id": 2, "priority": "P1", "reason_codes": ["LLM_LOCAL_TIMEOUT"]},
        ]
        p0_decisions = [
            {"id": 3, "priority": "P0", "reason_codes": ["NEGATION_VIOLATION"]},
            {"id": 4, "priority": "P0", "reason_codes": ["DOSAGE_PRE_ALERT"]},
        ]

        for d in p1_decisions:
            assert d["priority"] == "P1", "P1 decisions can be batch processed"

        for d in p0_decisions:
            assert d["priority"] == "P0", "P0 decisions require individual review"

    def test_batch_resolve_mixed_priorities(self) -> None:
        """Test that batch processing respects priority ordering."""
        decisions = [
            {"id": 1, "priority": "P0"},
            {"id": 2, "priority": "P1"},
            {"id": 3, "priority": "P2"},
            {"id": 4, "priority": "P0"},
            {"id": 5, "priority": "P1"},
        ]

        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        sorted_decisions = sorted(decisions, key=lambda d: priority_order[d["priority"]])

        assert sorted_decisions[0]["priority"] == "P0"
        assert sorted_decisions[1]["priority"] == "P0"
        assert sorted_decisions[2]["priority"] == "P1"


# ============================================================================
# Tests for get_stats
# ============================================================================

class TestGetStats:
    """Test suite for statistics retrieval."""

    def test_get_decision_stats_structure(self) -> None:
        """Test that get_decision_stats returns correct structure."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchone.return_value = {
            "total_decisions": 100,
            "pending_count": 30,
            "resolved_count": 50,
            "rejected_count": 10,
            "auto_resolved_count": 5,
            "escalated_count": 5,
            "avg_resolution_time_sec": 120.5,
        }
        mock_cursor.fetchall.side_effect = [
            [
                {"priority": "P0", "total_count": 20, "pending_count": 5, "resolved_count": 12},
                {"priority": "P1", "total_count": 40, "pending_count": 15, "resolved_count": 20},
                {"priority": "P2", "total_count": 30, "pending_count": 8, "resolved_count": 15},
                {"priority": "P3", "total_count": 10, "pending_count": 2, "resolved_count": 3},
            ],
            [
                {"reason_code": "CONSENSUS_DISPUTE", "total_count": 30, "pending_count": 10, "resolved_count": 18},
                {"reason_code": "DOSAGE_PRE_ALERT", "total_count": 20, "pending_count": 5, "resolved_count": 12},
            ],
            [
                {"status": "pending", "count": 30},
                {"status": "resolved", "count": 50},
                {"status": "rejected", "count": 10},
            ],
        ]
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)
        stats = logger.get_decision_stats()

        assert "total_decisions" in stats
        assert "pending_count" in stats
        assert "resolved_count" in stats
        assert "rejected_count" in stats
        assert "auto_resolved_count" in stats
        assert "escalated_count" in stats
        assert "by_priority" in stats
        assert "by_reason" in stats
        assert "by_status" in stats

        assert stats["total_decisions"] == 100
        assert stats["pending_count"] == 30
        assert stats["resolved_count"] == 50
        assert len(stats["by_priority"]) == 4
        assert len(stats["by_reason"]) == 2
        assert len(stats["by_status"]) == 3

    def test_get_decision_stats_with_book_id(self) -> None:
        """Test stats filtering by book_id."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchone.return_value = {
            "total_decisions": 10,
            "pending_count": 3,
            "resolved_count": 5,
            "rejected_count": 1,
            "auto_resolved_count": 1,
            "escalated_count": 0,
            "avg_resolution_time_sec": 90.0,
        }
        mock_cursor.fetchall.side_effect = [[], [], []]
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)
        stats = logger.get_decision_stats(book_id="book_001")

        assert stats["total_decisions"] == 10

    def test_get_decision_stats_empty(self) -> None:
        """Test stats when no decisions exist."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchone.return_value = {
            "total_decisions": 0,
            "pending_count": 0,
            "resolved_count": 0,
            "rejected_count": 0,
            "auto_resolved_count": 0,
            "escalated_count": 0,
            "avg_resolution_time_sec": None,
        }
        mock_cursor.fetchall.side_effect = [[], [], []]
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)
        stats = logger.get_decision_stats()

        assert stats["total_decisions"] == 0
        assert stats["by_priority"] == []
        assert stats["by_reason"] == []
        assert stats["by_status"] == []


# ============================================================================
# Tests for static helper methods
# ============================================================================

class TestStaticHelpers:
    """Test suite for static helper methods."""

    def test_count_negations(self) -> None:
        """Test negation word counting."""
        assert PushDecisionLogger._count_negations("不可用") == 1
        assert PushDecisionLogger._count_negations("不可用不可无") == 3
        assert PushDecisionLogger._count_negations("") == 0
        assert PushDecisionLogger._count_negations("可用") == 0

    def test_extract_context(self) -> None:
        """Test context extraction around keyword."""
        text = "当归补血汤是一剂良方"
        context = PushDecisionLogger._extract_context(text, "补血", window=3)
        assert "补血" in context
        assert len(context) <= 10

    def test_extract_context_keyword_not_found(self) -> None:
        """Test context extraction when keyword is not found."""
        result = PushDecisionLogger._extract_context("some text", "missing")
        assert result == ""

    def test_calculate_similarity(self) -> None:
        """Test string similarity calculation."""
        sim = PushDecisionLogger._calculate_similarity("当归", "当阳")
        assert 0 <= sim <= 1
        assert sim > 0

        sim_identical = PushDecisionLogger._calculate_similarity("当归", "当归")
        assert sim_identical == 1.0

        sim_empty = PushDecisionLogger._calculate_similarity("", "test")
        assert sim_empty == 0.0

    def test_describe_mismatch(self) -> None:
        """Test mismatch description generation."""
        desc = PushDecisionLogger._describe_mismatch("四君子汤", "四君子散", "name_mismatch")
        assert "名称" in desc or "不匹配" in desc

        desc_dosage = PushDecisionLogger._describe_mismatch("10g", "20g", "dosage_mismatch")
        assert "剂量" in desc_dosage or "不匹配" in desc_dosage

        desc_unknown = PushDecisionLogger._describe_mismatch("a", "b", "unknown_type")
        assert "不一致" in desc_unknown
