"""质检管道测试：规则检查、LLM 集成。"""

from __future__ import annotations

from kzocr.analysis.quality import QualityChecker
from kzocr.analysis.recipe_parser import HerbItem, ParsedRecipe


def _recipe(**kw) -> ParsedRecipe:
    """快速创建 ParsedRecipe。"""
    defaults = dict(recipe_no="1.1", title="测试方")
    defaults.update(kw)
    return ParsedRecipe(**defaults)


def test_missing_field_detected():
    """缺"组成"字段 → issues 含 missing_field。"""
    r = _recipe(fields={"主治": "感冒"})
    result = QualityChecker().check(r)
    assert result.status == "corrected"
    assert any(i.issue_type == "missing_field" and i.field == "组成" for i in result.issues)


def test_dose_suspicious():
    """剂量 > 100g → issues 含 dose_reason。"""
    r = _recipe(fields={"组成": "附子200克"}, herbs=[HerbItem(herb_name="附子", dosage="200", unit="g")])
    result = QualityChecker().check(r)
    assert result.status == "corrected"
    assert any(i.issue_type == "dose_reason" for i in result.issues)


def test_single_char_herb():
    """单字药名 → issues 含 suspicious_name。"""
    r = _recipe(fields={"组成": "草10克"}, herbs=[HerbItem(herb_name="草", dosage="10", unit="g")])
    result = QualityChecker().check(r)
    assert result.status == "corrected"
    assert any(i.issue_type == "suspicious_name" for i in result.issues)


def test_all_ok():
    """完整有效方剂 → status=verified, issues=[]。"""
    r = _recipe(fields={"组成": "金银花12克", "主治": "感冒", "来源": "伤寒论"})
    result = QualityChecker().check(r)
    assert result.status == "verified"
    assert len(result.issues) == 0


def test_llm_integration():
    """Mock LLM 返回修正建议 → status=corrected with suggestion。"""

    def mock_llm(prompt: str) -> str:
        return "组成字段建议补充：金银花12克"

    r = _recipe(fields={"主治": "感冒"}, herbs=[])
    checker = QualityChecker(llm_client=mock_llm)
    result = checker.check(r)
    # 规则已检测 missing_field，LLM 返回建议
    assert result.status == "corrected"
    assert any(i.field == "llm" for i in result.issues)


def test_llm_says_ok():
    """LLM 返回"全部正确" → 不影响规则判定结果。"""

    def mock_llm(prompt: str) -> str:
        return "全部正确"

    r = _recipe(fields={"组成": "金银花12克", "主治": "感冒"})
    checker = QualityChecker(llm_client=mock_llm)
    result = checker.check(r)
    assert result.status == "verified"
    assert len(result.issues) == 0


def test_llm_fallback_on_error():
    """LLM 抛异常时降级为规则结果。"""

    def failing_llm(prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    r = _recipe(fields={"组成": "附子200克"}, herbs=[HerbItem(herb_name="附子", dosage="200", unit="g")])
    checker = QualityChecker(llm_client=failing_llm)
    result = checker.check(r)
    assert result.status == "corrected"  # 规则仍检出问题
    assert any(i.issue_type == "dose_reason" for i in result.issues)
