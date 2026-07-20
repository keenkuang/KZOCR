"""tcm_ocr knowledge 子模块纯逻辑单测（零 DB/零外部依赖）。

覆盖 P0 优先级函数：auto_classifier, scope_scorer,
formula/extractor 模块级函数，auto_discover 纯函数。
"""
from __future__ import annotations

import pytest

# ── term/auto_classifier ──


@pytest.fixture
def classifier():
    from kzocr.tcm_ocr.knowledge.term.auto_classifier import AutoClassifier
    return AutoClassifier()


def test_classify_negation(classifier):
    result = classifier.classify("不")
    assert result["category"] == "negation"
    assert result["safety"] == "critical"


def test_classify_dosage_unit(classifier):
    result = classifier.classify("克")
    assert result["category"] == "dosage_unit"


def test_classify_unknown_returns_default(classifier):
    result = classifier.classify("xyzzy")
    assert result["category"] == "unknown"
    # 未知术语默认 safety=low（实际实现返回 low，非 normal）
    assert result["safety"] == "low"


def test_classify_batch_returns_list(classifier):
    results = classifier.classify_batch(["不", "克", "xyzzy"])
    assert len(results) == 3
    assert results[0]["category"] == "negation"
    assert results[2]["category"] == "unknown"


# ── term/scope_scorer ──


def test_effective_scope_score():
    from kzocr.tcm_ocr.knowledge.term.scope_scorer import effective_scope_score
    # global → 1（最低，默认级）
    assert effective_scope_score("global") == 1
    # book → 1000（最高，单本书级定制）
    assert effective_scope_score("book") == 1000
    # publisher → 100
    assert effective_scope_score("publisher") == 100
    # None → 1 (global 默认)
    assert effective_scope_score(None) == 1


def test_sort_patterns_by_priority():
    from kzocr.tcm_ocr.knowledge.term.scope_scorer import sort_patterns_by_priority
    patterns = [
        {"correct_name": "c", "priority": "low", "scope": "page"},
        {"correct_name": "a", "priority": "high", "scope": "global"},
        {"correct_name": "b", "priority": "medium", "scope": "book"},
    ]
    sorted_list = sort_patterns_by_priority(patterns)
    # book → 1000 最高 → 排在首位
    assert sorted_list[0]["correct_name"] == "b"
    assert sorted_list[0]["scope"] == "book"


# ── formula/extractor 模块级函数 ──


def test_is_formula_paragraph():
    from kzocr.tcm_ocr.knowledge.formula.extractor import is_formula_paragraph
    # 含方剂标记 + 剂量 → True
    assert is_formula_paragraph("组成：桂枝三两，芍药三两")
    assert is_formula_paragraph("处方：麻黄三钱，杏仁二钱")
    # 无剂量 → False
    assert not is_formula_paragraph("这是正文，不是方剂")


def test_is_valid_herb_name():
    from kzocr.tcm_ocr.knowledge.formula.extractor import is_valid_herb_name
    assert is_valid_herb_name("桂枝") is True
    assert is_valid_herb_name("") is False


def test_extract_herb_names_module():
    from kzocr.tcm_ocr.knowledge.formula.extractor import extract_herb_names
    names = extract_herb_names("桂枝, 白芍, 甘草")
    assert "桂枝" in names
    assert "白芍" in names


# ── herb_pattern/auto_discover 纯函数 ──


def test_extract_herb_names():
    from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import extract_herb_names
    names = extract_herb_names("桂枝、白芍、炙甘草")
    assert "桂枝" in names
    assert "白芍" in names


def test_is_valid_herb_name_auto():
    from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import is_valid_herb_name
    assert is_valid_herb_name("桂枝") is True
    assert is_valid_herb_name("") is False
    assert is_valid_herb_name("123") is False


def test_calculate_confidence():
    from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import calculate_confidence
    # calculate_confidence(corr_record: dict) — 基于 correction_stage 算分
    high = calculate_confidence({"correction_stage": "golden"})
    assert high >= 0.9
    low = calculate_confidence({"correction_stage": "auto"})
    assert low <= 0.6


# ── context_pattern/auto_discover 纯函数 ──


def test_normalize_context_description():
    from kzocr.tcm_ocr.knowledge.context_pattern.auto_discover import (
        normalize_context_description,
        infer_pattern_type,
    )
    desc = normalize_context_description(" 同上（三剂） ")
    assert "同上" in desc
    assert infer_pattern_type("与上方同") == "modification_note"
    assert infer_pattern_type("加桂枝") == "modification_note"
    assert infer_pattern_type("未知模式") == "other"


def test_normalized_to_regex():
    from kzocr.tcm_ocr.knowledge.context_pattern.auto_discover import (
        _normalized_to_regex,
    )
    regex = _normalized_to_regex("加桂枝")
    assert regex is not None
    assert isinstance(regex, str)
    assert len(regex) > 0
