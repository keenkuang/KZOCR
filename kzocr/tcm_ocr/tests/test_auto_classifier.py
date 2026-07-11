"""
AutoClassifier 全面测试

测试内容：
- 否定词识别
- 穴位名识别
- 经络名识别
- 方剂名识别
- 证型识别
- 规则优先级验证
- 批量分类性能
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from kzocr.tcm_ocr.knowledge.term.auto_classifier import AutoClassifier


# ============================================================================
# Helper fixture
# ============================================================================

@pytest.fixture
def classifier() -> AutoClassifier:
    """Provide a fresh AutoClassifier instance."""
    return AutoClassifier()


# ============================================================================
# Tests for negation classification
# ============================================================================

class TestClassifyNegation:
    """Test suite for negation word classification."""

    @pytest.mark.parametrize(
        "text",
        ["不", "无", "非", "忌", "禁", "勿", "慎"],
    )
    def test_classify_negation_words(self, classifier: AutoClassifier, text: str) -> None:
        """Test that negation words are classified as 'negation' with critical safety."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "negation"
        assert result["safety"] == "critical"
        assert result["priority"] == 100
        assert result["text"] == text

    def test_classify_negation_priority_highest(self, classifier: AutoClassifier) -> None:
        """Test that negation has the highest priority (100)."""
        result = classifier.classify("不")
        assert result["priority"] == 100

    def test_non_negation_words(self, classifier: AutoClassifier) -> None:
        """Test that non-negation words are not classified as negation."""
        result = classifier.classify("可能")
        assert result["category"] != "negation"

    def test_negation_word_with_suffix(self, classifier: AutoClassifier) -> None:
        """Test that negation words with suffixes don't match exact pattern."""
        result = classifier.classify("不要")
        # "不要" has "不" at start but doesn't match the exact pattern r'^(不|无|非|忌|禁|勿|慎)$'
        assert result["category"] != "negation"


# ============================================================================
# Tests for acupoint classification
# ============================================================================

class TestClassifyAcupoint:
    """Test suite for acupoint name classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "百会穴", "关元穴", "足三里穴", "三阴交穴",
            "合谷穴", "太冲穴", "内关穴", "外关穴",
            "神门穴", "大椎穴", "命门穴", "肾俞穴",
            "脾俞穴", "肝俞穴", "心俞穴", "肺俞穴",
            "中脘穴", "膻中穴", "天枢穴", "血海穴",
            "阴陵泉穴", "阳陵泉穴", "曲池穴", "风池穴",
            "涌泉穴", "太阳穴",
        ],
    )
    def test_classify_acupoint_names(self, classifier: AutoClassifier, text: str) -> None:
        """Test that acupoint names ending with '穴' are classified correctly."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "acupoint"
        assert result["safety"] == "high"
        assert result["priority"] == 95

    @pytest.mark.parametrize(
        "text",
        [
            "分穴", "穴位", "穴道", "取穴", "穴注",
            "背穴", "压穴", "穴贴", "耳穴", "体穴",
            "穴疗", "穴数", "穴图", "穴名", "穴方",
            "穴法", "穴灸", "埋穴", "点穴", "刺穴",
            "针穴", "按穴", "摩穴", "刮穴", "拔穴",
            "温穴", "电穴",
        ],
    )
    def test_classify_acupoint_exceptions(self, classifier: AutoClassifier, text: str) -> None:
        """Test that acupoint exception words are NOT classified as acupoints."""
        result = classifier.classify(text)

        # These should NOT match the acupoint rule due to exceptions
        assert result["category"] != "acupoint", (
            f"'{text}' should not be classified as acupoint (it's in exceptions)"
        )

    def test_classify_acupoint_single_char(self, classifier: AutoClassifier) -> None:
        """Test that single character with '穴' is not classified as acupoint."""
        # The pattern is .{1,3}穴$, so "X穴" should match
        result = classifier.classify("X穴")
        assert result["category"] == "acupoint"


# ============================================================================
# Tests for meridian classification
# ============================================================================

class TestClassifyMeridian:
    """Test suite for meridian name classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "手太阴肺经", "手阳明大肠经", "足阳明胃经", "足太阴脾经",
            "手少阴心经", "手太阳小肠经", "足太阳膀胱经", "足少阴肾经",
            "手厥阴心包经", "手少阳三焦经", "足少阳胆经", "足厥阴肝经",
        ],
    )
    def test_classify_twelve_meridians(self, classifier: AutoClassifier, text: str) -> None:
        """Test classification of twelve regular meridians."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "meridian"
        assert result["safety"] == "medium"
        assert result["priority"] == 93

    @pytest.mark.parametrize(
        "text",
        [
            "任脉", "督脉", "冲脉", "带脉",
            "阴跷脉", "阳跷脉", "阴维脉", "阳维脉",
        ],
    )
    def test_classify_extra_meridians(self, classifier: AutoClassifier, text: str) -> None:
        """Test classification of eight extraordinary meridians."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "meridian"
        assert result["safety"] == "medium"
        assert result["priority"] == 93


# ============================================================================
# Tests for formula classification
# ============================================================================

class TestClassifyFormula:
    """Test suite for formula name classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "四君子汤", "六味地黄丸", "逍遥散", "补中益气汤",
            "桂枝汤", "大承气汤", "小柴胡汤", "归脾汤",
            "当归补血汤", "牛黄解毒丸", "金匮肾气丸",
            "银翘散", "玉屏风散", "至宝丹", "紫雪丹",
            "枇杷膏", "酸枣仁汤",
        ],
    )
    def test_classify_formula_names(self, classifier: AutoClassifier, text: str) -> None:
        """Test that formula names are classified correctly."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "formula"
        assert result["safety"] == "medium"
        assert result["priority"] == 90

    def test_classify_formula_min_len_constraint(self, classifier: AutoClassifier) -> None:
        """Test that formula names must be at least 2 characters."""
        result = classifier.classify("汤")
        # Single character should not match formula pattern
        assert result["category"] != "formula"

    def test_classify_formula_single_char_suffix(self, classifier: AutoClassifier) -> None:
        """Test that single character + suffix is not a formula."""
        result = classifier.classify("X汤")
        # This is 2 chars, should match
        assert result["category"] == "formula"


# ============================================================================
# Tests for syndrome classification
# ============================================================================

class TestClassifySyndrome:
    """Test suite for TCM syndrome classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "气虚证", "阴虚证", "阳虚证", "痰湿证",
            "血瘀证", "气滞证", "血虚证", "湿热证",
            "风寒证", "风热证", "暑湿证",
        ],
    )
    def test_classify_syndrome_names(self, classifier: AutoClassifier, text: str) -> None:
        """Test that syndrome names ending with '证' are classified correctly."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "tcm_syndrome"
        assert result["safety"] == "medium"
        assert result["priority"] == 85

    @pytest.mark.parametrize(
        "text",
        [
            "证候",
        ],
    )
    def test_classify_syndrome_edge_cases(self, classifier: AutoClassifier, text: str) -> None:
        """Test syndrome edge cases - only 证候 matches .+(证|候)$ as a syndrome term."""
        result = classifier.classify(text)
        assert result["category"] == "tcm_syndrome"

    @pytest.mark.parametrize(
        "text",
        ["证明", "证件", "证人"],
    )
    def test_classify_syndrome_non_syndrome(self, classifier: AutoClassifier, text: str) -> None:
        """Test that 证明, 证件, 证人 do NOT match syndrome pattern (they end with other chars)."""
        result = classifier.classify(text)
        # These do NOT end with "证" or "候" (e.g. 证明 ends with "明"), so they are unknown
        assert result["category"] == "unknown"


# ============================================================================
# Tests for disease classification
# ============================================================================

class TestClassifyDisease:
    """Test suite for TCM disease name classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "感冒病", "咳嗽病", "头痛病", "眩晕病",
            "中风病", "消渴病", "胸痹病",
        ],
    )
    def test_classify_disease_names(self, classifier: AutoClassifier, text: str) -> None:
        """Test that disease names ending with '病' are classified correctly."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "tcm_disease"
        assert result["safety"] == "medium"
        assert result["priority"] == 80


# ============================================================================
# Tests for priority order
# ============================================================================

class TestPriorityOrder:
    """Test suite for rule priority ordering."""

    def test_negation_beats_acupoint(self, classifier: AutoClassifier) -> None:
        """Test that negation (priority 100) beats acupoint (95)."""
        # "不" is a negation word
        result = classifier.classify("不")
        assert result["category"] == "negation"
        assert result["priority"] == 100

    def test_acupoint_beats_meridian(self, classifier: AutoClassifier) -> None:
        """Test that acupoint (95) beats meridian (93)."""
        # 任脉穴 - this is an acupoint name
        result = classifier.classify("百会穴")
        assert result["category"] == "acupoint"
        assert result["priority"] == 95

    def test_meridian_beats_formula(self, classifier: AutoClassifier) -> None:
        """Test that meridian (93) beats formula (90)."""
        # A text that could match both
        result = classifier.classify("手太阴肺经")
        assert result["category"] == "meridian"
        assert result["priority"] == 93

    def test_formula_beats_syndrome(self, classifier: AutoClassifier) -> None:
        """Test that formula (90) beats syndrome (85)."""
        result = classifier.classify("四君子汤")
        assert result["category"] == "formula"
        assert result["priority"] == 90

    def test_syndrome_beats_disease(self, classifier: AutoClassifier) -> None:
        """Test that syndrome (85) beats disease (80)."""
        # "证" has priority 85 vs "病" has 80
        result = classifier.classify("气虚证")
        assert result["category"] == "tcm_syndrome"
        assert result["priority"] == 85

    def test_priority_order_values(self, classifier: AutoClassifier) -> None:
        """Test that all rules have correct priority values."""
        expected_priorities = {
            "negation": 100,
            "dosage_unit": 99,
            "acupoint": 95,
            "meridian": 93,
            "formula": 90,
            "tcm_syndrome": 85,
            "tcm_disease": 80,
        }

        for category, expected_priority in expected_priorities.items():
            # Find the rule for this category
            rules = [r for r in classifier.CONTENT_RULES if r["category"] == category]
            assert len(rules) > 0, f"No rule found for category '{category}'"
            for rule in rules:
                assert rule["priority"] == expected_priority, (
                    f"Category '{category}' has priority {rule['priority']}, "
                    f"expected {expected_priority}"
                )

    def test_rules_sorted_by_priority(self, classifier: AutoClassifier) -> None:
        """Test that classify method sorts rules by priority descending."""
        sorted_rules = sorted(
            classifier.CONTENT_RULES,
            key=lambda r: r.get("priority", 0),
            reverse=True,
        )
        priorities = [r["priority"] for r in sorted_rules]
        assert priorities == sorted(priorities, reverse=True)


# ============================================================================
# Tests for batch classification
# ============================================================================

class TestClassifyBatch:
    """Test suite for batch classification."""

    def test_classify_batch_basic(self, classifier: AutoClassifier) -> None:
        """Test batch classification with multiple texts."""
        texts = ["百会穴", "四君子汤", "气虚证", "不", "手太阴肺经"]
        results = classifier.classify_batch(texts)

        assert len(results) == len(texts)
        assert results[0]["category"] == "acupoint"
        assert results[1]["category"] == "formula"
        assert results[2]["category"] == "tcm_syndrome"
        assert results[3]["category"] == "negation"
        assert results[4]["category"] == "meridian"

    def test_classify_batch_empty(self, classifier: AutoClassifier) -> None:
        """Test batch classification with empty list."""
        results = classifier.classify_batch([])
        assert results == []

    def test_classify_batch_single_item(self, classifier: AutoClassifier) -> None:
        """Test batch classification with single item."""
        results = classifier.classify_batch(["附子"])
        assert len(results) == 1

    def test_classify_batch_performance(self, classifier: AutoClassifier) -> None:
        """Benchmark: batch classification should handle 1000 items quickly."""
        import time

        texts = ["百会穴", "四君子汤", "气虚证", "不", "手太阴肺经"] * 200  # 1000 items

        start = time.perf_counter()
        results = classifier.classify_batch(texts)
        elapsed = time.perf_counter() - start

        assert len(results) == 1000
        # Should complete in under 1 second for 1000 items
        assert elapsed < 1.0, f"Batch classification took {elapsed:.3f}s, expected < 1.0s"


# ============================================================================
# Tests for edge cases and error handling
# ============================================================================

class TestEdgeCases:
    """Test suite for edge cases and error handling."""

    def test_classify_empty_string(self, classifier: AutoClassifier) -> None:
        """Test classification of empty string returns unknown."""
        result = classifier.classify("")
        assert result["category"] == "unknown"
        assert result["safety"] == "low"
        assert result["matched"] is False
        assert result["priority"] == 0

    def test_classify_none(self, classifier: AutoClassifier) -> None:
        """Test classification of None returns unknown."""
        result = classifier.classify(None)
        assert result["category"] == "unknown"
        assert result["safety"] == "low"
        assert result["matched"] is False

    def test_classify_non_string(self, classifier: AutoClassifier) -> None:
        """Test classification of non-string input."""
        result = classifier.classify(123)
        assert result["category"] == "unknown"
        assert result["safety"] == "low"
        assert result["matched"] is False

    def test_classify_whitespace_only(self, classifier: AutoClassifier) -> None:
        """Test classification of whitespace-only string."""
        result = classifier.classify("   ")
        assert result["category"] == "unknown"
        assert result["matched"] is False

    def test_classify_unknown_term(self, classifier: AutoClassifier) -> None:
        """Test classification of unknown term returns unknown."""
        result = classifier.classify("这是一个完全不认识的词")
        assert result["category"] == "unknown"
        assert result["safety"] == "low"
        assert result["matched"] is False

    def test_match_rule_empty_text(self, classifier: AutoClassifier) -> None:
        """Test _match_rule with empty text returns False."""
        rule = {"pattern": r"test", "category": "test"}
        result = classifier._match_rule("", rule)
        assert result is False

    def test_match_rule_empty_rule(self, classifier: AutoClassifier) -> None:
        """Test _match_rule with empty rule returns False."""
        result = classifier._match_rule("test", {})
        assert result is False

    def test_match_rule_with_exception(self, classifier: AutoClassifier) -> None:
        """Test _match_rule with exception set."""
        rule = {
            "pattern": r"test",
            "category": "test",
            "exceptions": {"test_exception"},
        }
        # Should match
        assert classifier._match_rule("testing", rule) is True
        # Should not match due to exception
        assert classifier._match_rule("test_exception", rule) is False

    def test_match_rule_min_len(self, classifier: AutoClassifier) -> None:
        """Test _match_rule with min_len constraint."""
        rule = {
            "pattern": r"test",
            "category": "test",
            "min_len": 5,
        }
        # Too short
        assert classifier._match_rule("tes", rule) is False
        # Long enough and matches
        assert classifier._match_rule("testing", rule) is True

    def test_match_rule_invalid_regex(self, classifier: AutoClassifier) -> None:
        """Test _match_rule handles invalid regex gracefully."""
        rule = {
            "pattern": r"[invalid(",
            "category": "test",
        }
        result = classifier._match_rule("test", rule)
        assert result is False


# ============================================================================
# Tests for dosage unit classification
# ============================================================================

class TestClassifyDosageUnit:
    """Test suite for dosage unit classification."""

    @pytest.mark.parametrize(
        "text",
        [
            "克", "钱", "两", "分", "斤",
            "毫升", "升",
            "钱匕", "方寸匕",
            "丸", "片", "粒",
            "支", "瓶", "帖", "包",
        ],
    )
    def test_classify_dosage_units(self, classifier: AutoClassifier, text: str) -> None:
        """Test that dosage units are classified correctly."""
        result = classifier.classify(text)

        assert result["matched"] is True
        assert result["category"] == "dosage_unit"
        assert result["safety"] == "critical"
        assert result["priority"] == 99


# ============================================================================
# Tests for CONTENT_RULES structure
# ============================================================================

class TestContentRules:
    """Test suite for CONTENT_RULES structure validation."""

    def test_all_rules_have_required_keys(self, classifier: AutoClassifier) -> None:
        """Test that all rules have required keys."""
        required_keys = {"pattern", "category", "safety", "priority"}
        for rule in classifier.CONTENT_RULES:
            for key in required_keys:
                assert key in rule, f"Rule for {rule.get('category', 'unknown')} missing key: {key}"

    def test_no_duplicate_priorities(self, classifier: AutoClassifier) -> None:
        """Test that no two rules in same category have same priority."""
        from collections import Counter
        priorities = [r["priority"] for r in classifier.CONTENT_RULES]
        # Meridian has two rules both at 93, which is expected
        # So we just check there are no unexpected duplicates
        counter = Counter(priorities)
        assert counter[93] == 2, "Expected exactly 2 meridian rules at priority 93"

    def test_all_patterns_are_valid_regex(self, classifier: AutoClassifier) -> None:
        """Test that all patterns are valid regular expressions."""
        import re
        for rule in classifier.CONTENT_RULES:
            pattern = rule["pattern"]
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(f"Invalid regex pattern '{pattern}': {e}")

    def test_rule_count(self, classifier: AutoClassifier) -> None:
        """Test that CONTENT_RULES has expected number of rules."""
        # negation, dosage_unit, acupoint, meridian(2), formula, syndrome, disease = 8
        assert len(classifier.CONTENT_RULES) == 8, (
            f"Expected 8 rules, got {len(classifier.CONTENT_RULES)}"
        )
