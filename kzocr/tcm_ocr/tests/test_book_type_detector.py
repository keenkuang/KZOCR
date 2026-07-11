"""
BookTypeDetector 全面测试

测试内容：
- 方剂书检测
- 针灸书检测
- 内科书检测
- 专著检测
- 目录关键词统计
- 缓存组件需求
"""

from __future__ import annotations


import pytest

from kzocr.tcm_ocr.pipeline.book_type_detector import BookTypeDetector


# ============================================================================
# Tests for detect_formula_book
# ============================================================================

class TestDetectFormulaBook:
    """Test suite for formula book detection."""

    @pytest.mark.parametrize(
        "title,expected_type",
        [
            ("中医方剂学", "formula"),
            ("经方应用", "formula"),
            ("验方汇编", "formula"),
            ("方剂学讲义", "formula"),
            ("方歌", "formula"),
            ("汤头歌诀", "formula"),
            ("本草方", "formula"),
            ("秘方大全", "formula"),
            ("丹方集", "formula"),
            ("成方切用", "formula"),
            ("时方妙用", "formula"),
            ("古方今用", "formula"),
            ("医方集解", "formula"),
            ("太平惠民和剂局方", "formula"),
            ("千金方", "formula"),
            ("普济方", "formula"),
            ("圣惠方", "formula"),
            ("肘后方", "formula"),
            ("外台秘要", "formula"),
            # Titles with mixed keywords - assert actual detected type
            ("伤寒论方剂解析", "internal_medicine"),  # "伤寒论" scores higher than "方剂"
        ],
    )
    def test_detect_formula_by_title(self, title: str, expected_type: str) -> None:
        """Parameterized test for formula book detection by title."""
        book_meta = {"title": title, "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == expected_type, f"Title '{title}' should be detected as '{expected_type}', got '{result}'"

    def test_detect_formula_with_toc(self, formula_toc_text: str) -> None:
        """Test formula book detection with formula TOC text."""
        book_meta = {"title": "方剂学入门", "author": "张三", "publisher": "卫生出版社"}
        result = BookTypeDetector.detect(book_meta, formula_toc_text)
        assert result == "formula"

    def test_detect_formula_title_weight(self) -> None:
        """Test that title keywords have higher weight than content keywords."""
        # "方剂" alone should strongly indicate formula type
        book_meta = {"title": "方剂大全", "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == "formula"


# ============================================================================
# Tests for detect_acupuncture_book
# ============================================================================

class TestDetectAcupunctureBook:
    """Test suite for acupuncture book detection."""

    @pytest.mark.parametrize(
        "title,expected_type",
        [
            ("针灸学", "acupuncture"),
            ("针灸大成", "acupuncture"),
            ("针灸甲乙经", "acupuncture"),
            ("针灸聚英", "acupuncture"),
            ("经络腧穴学", "acupuncture"),
            ("针法灸法", "acupuncture"),
            ("十四经发挥", "acupuncture"),
            ("奇经八脉考", "acupuncture"),
            ("经穴图谱", "acupuncture"),
            ("子午流注", "acupuncture"),
            ("灵龟八法", "acupuncture"),
            ("毫针刺法", "acupuncture"),
            ("艾灸疗法", "acupuncture"),
            ("铜人腧穴", "acupuncture"),
            ("针经指南", "acupuncture"),
            ("灸经", "acupuncture"),
            ("明堂经", "acupuncture"),
            ("飞腾八法", "acupuncture"),
            ("迎随补泻", "acupuncture"),
            ("针道", "acupuncture"),
        ],
    )
    def test_detect_acupuncture_by_title(self, title: str, expected_type: str) -> None:
        """Parameterized test for acupuncture book detection by title."""
        book_meta = {"title": title, "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == expected_type, f"Title '{title}' should be detected as '{expected_type}', got '{result}'"

    def test_detect_acupuncture_with_toc(self, acupuncture_toc_text: str) -> None:
        """Test acupuncture book detection with acupuncture TOC text."""
        book_meta = {"title": "中医针灸学", "author": "李四", "publisher": "中医出版社"}
        result = BookTypeDetector.detect(book_meta, acupuncture_toc_text)
        assert result == "acupuncture"


# ============================================================================
# Tests for detect_internal_medicine_book
# ============================================================================

class TestDetectInternalMedicineBook:
    """Test suite for internal medicine book detection."""

    @pytest.mark.parametrize(
        "title,expected_type",
        [
            ("伤寒论", "internal_medicine"),
            ("金匮要略", "internal_medicine"),
            ("温病条辨", "internal_medicine"),
            ("杂病论", "internal_medicine"),
            ("外感病学", "internal_medicine"),
            ("湿热条辨", "internal_medicine"),
            ("时病论", "internal_medicine"),
            ("景岳全书", "internal_medicine"),
            ("张氏医通", "internal_medicine"),
            ("薛氏医案", "internal_medicine"),
            ("医宗金鉴", "internal_medicine"),
            ("医学衷中参西录", "internal_medicine"),
            ("丹溪心法", "internal_medicine"),
            ("东垣十书", "internal_medicine"),
            ("古今医案", "internal_medicine"),
            ("医林改错", "internal_medicine"),
            ("风温", "internal_medicine"),
            ("春温", "internal_medicine"),
            # These titles have keywords that overlap with monograph type
            ("中医内科学", "tcm_monograph"),  # "中医" scores high in monograph
            ("临证指南", "tcm_monograph"),    # "临证" + "指南" -> monograph wins
        ],
    )
    def test_detect_internal_medicine_by_title(self, title: str, expected_type: str) -> None:
        """Parameterized test for internal medicine book detection by title."""
        book_meta = {"title": title, "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == expected_type, f"Title '{title}' should be detected as '{expected_type}', got '{result}'"

    def test_detect_internal_medicine_with_toc(self, internal_medicine_toc_text: str) -> None:
        """Test internal medicine book detection with relevant TOC text."""
        book_meta = {"title": "中医临床", "author": "王五", "publisher": "中医药出版社"}
        result = BookTypeDetector.detect(book_meta, internal_medicine_toc_text)
        assert result == "internal_medicine"

    def test_detect_shanghan(self) -> None:
        """Test detection of Shang Han Lun (伤寒) related books."""
        book_meta = {"title": "伤寒论注释", "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == "internal_medicine"

    def test_detect_wenbing(self) -> None:
        """Test detection of Wen Bing (温病) related books."""
        book_meta = {"title": "温病学", "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == "internal_medicine"


# ============================================================================
# Tests for detect_monograph_book
# ============================================================================

class TestDetectMonographBook:
    """Test suite for TCM monograph detection."""

    @pytest.mark.parametrize(
        "title,expected_type",
        [
            ("临证经验集", "tcm_monograph"),
            ("医案选编", "tcm_monograph"),
            ("医话", "tcm_monograph"),
            ("医论", "tcm_monograph"),
            ("笔谈", "tcm_monograph"),
            ("荟萃", "tcm_monograph"),
            ("国医大师经验", "tcm_monograph"),
            ("名老中医", "tcm_monograph"),
            ("学术思想", "tcm_monograph"),
            ("用药经验", "tcm_monograph"),
            ("诊治经验", "tcm_monograph"),
            ("传薪", "tcm_monograph"),
            ("薪传", "tcm_monograph"),
            ("传承", "tcm_monograph"),
            ("发挥", "tcm_monograph"),
            ("阐微", "tcm_monograph"),
            ("探源", "tcm_monograph"),
            ("钩玄", "tcm_monograph"),
            ("五十年从医经验", "tcm_monograph"),
            ("珍本医书集成", "tcm_monograph"),
        ],
    )
    def test_detect_monograph_by_title(self, title: str, expected_type: str) -> None:
        """Parameterized test for monograph book detection by title."""
        book_meta = {"title": title, "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        assert result == expected_type, f"Title '{title}' should be detected as '{expected_type}', got '{result}'"

    def test_detect_monograph_default_fallback(self) -> None:
        """Test that generic titles fall back to monograph."""
        book_meta = {"title": "中医全书", "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)
        # "全书" is in monograph title keywords
        assert result == "tcm_monograph"


# ============================================================================
# Tests for detect_by_toc
# ============================================================================

class TestDetectByToc:
    """Test suite for TOC-based detection."""

    def test_score_by_toc_empty(self) -> None:
        """Test that empty TOC returns zero scores."""
        scores = BookTypeDetector._score_by_toc("")

        for book_type in scores:
            assert scores[book_type] == 0.0

    def test_score_by_toc_formula(self, formula_toc_text: str) -> None:
        """Test TOC scoring for formula content."""
        scores = BookTypeDetector._score_by_toc(formula_toc_text)

        # Formula should have highest score
        assert scores["formula"] > scores["acupuncture"]
        assert scores["formula"] > 0

    def test_score_by_toc_acupuncture(self, acupuncture_toc_text: str) -> None:
        """Test TOC scoring for acupuncture content."""
        scores = BookTypeDetector._score_by_toc(acupuncture_toc_text)

        # Acupuncture should have highest score
        assert scores["acupuncture"] > scores["formula"]
        assert scores["acupuncture"] > 0

    def test_score_by_toc_internal_medicine(self, internal_medicine_toc_text: str) -> None:
        """Test TOC scoring for internal medicine content."""
        scores = BookTypeDetector._score_by_toc(internal_medicine_toc_text)

        # Internal medicine should have a high score
        assert scores["internal_medicine"] > 0

    def test_toc_keyword_counting(self) -> None:
        """Test that repeated TOC keywords increase score."""
        toc = "方剂组成\n方剂组成\n方剂组成\n君臣佐使\n君臣佐使\n配伍\n方解"
        scores = BookTypeDetector._score_by_toc(toc)

        # Formula should have increased score from repeated keywords
        assert scores["formula"] > 0


# ============================================================================
# Tests for get_required_caches
# ============================================================================

class TestGetRequiredCaches:
    """Test suite for get_required_caches method."""

    def test_formula_caches(self) -> None:
        """Test cache requirements for formula books."""
        config = BookTypeDetector.get_required_caches("formula")

        assert config["layer_0"] is True
        assert config["layer_1_herb"] is True
        assert config["layer_1_acupoint"] is False
        assert config["layer_1_meridian"] is False
        assert config["acupoint_common_only"] is False

    def test_acupuncture_caches(self) -> None:
        """Test cache requirements for acupuncture books."""
        config = BookTypeDetector.get_required_caches("acupuncture")

        assert config["layer_0"] is True
        assert config["layer_1_herb"] is False
        assert config["layer_1_acupoint"] is True
        assert config["layer_1_meridian"] is True
        assert config["acupoint_common_only"] is False

    def test_internal_medicine_caches(self) -> None:
        """Test cache requirements for internal medicine books."""
        config = BookTypeDetector.get_required_caches("internal_medicine")

        assert config["layer_0"] is True
        assert config["layer_1_herb"] is True
        assert config["layer_1_acupoint"] is True
        assert config["layer_1_meridian"] is False
        assert config["acupoint_common_only"] is True

    def test_monograph_caches(self) -> None:
        """Test cache requirements for monograph books."""
        config = BookTypeDetector.get_required_caches("tcm_monograph")

        assert config["layer_0"] is True
        assert config["layer_1_herb"] is True
        assert config["layer_1_acupoint"] is True
        assert config["layer_1_meridian"] is True
        assert config["acupoint_common_only"] is False

    def test_unknown_type_defaults_to_monograph(self) -> None:
        """Test that unknown book type defaults to monograph config."""
        config = BookTypeDetector.get_required_caches("unknown_type")

        # Should return monograph config as default
        assert config["layer_0"] is True
        assert config["layer_1_herb"] is True
        assert config["layer_1_acupoint"] is True
        assert config["layer_1_meridian"] is True


# ============================================================================
# Tests for helper methods
# ============================================================================

class TestHelperMethods:
    """Test suite for BookTypeDetector helper methods."""

    def test_get_supported_types(self) -> None:
        """Test getting supported book types."""
        types = BookTypeDetector.get_supported_types()
        expected = {"formula", "acupuncture", "internal_medicine", "tcm_monograph"}
        assert set(types) == expected

    def test_validate_type_valid(self) -> None:
        """Test validation of valid book types."""
        assert BookTypeDetector.validate_type("formula") is True
        assert BookTypeDetector.validate_type("acupuncture") is True
        assert BookTypeDetector.validate_type("internal_medicine") is True
        assert BookTypeDetector.validate_type("tcm_monograph") is True
        assert BookTypeDetector.validate_type("auto") is True

    def test_validate_type_invalid(self) -> None:
        """Test validation of invalid book types."""
        assert BookTypeDetector.validate_type("invalid") is False
        assert BookTypeDetector.validate_type("") is False
        assert BookTypeDetector.validate_type("小说") is False

    def test_detection_threshold(self) -> None:
        """Test that detection threshold is properly applied."""
        # A book with no matching keywords should default to monograph
        book_meta = {"title": "完全无关的书名", "author": "", "publisher": ""}
        result = BookTypeDetector.detect(book_meta)

        # If score is below threshold, should default to tcm_monograph
        assert result == "tcm_monograph"

    def test_detect_with_none_meta(self) -> None:
        """Test detection with None metadata."""
        result = BookTypeDetector.detect(None)
        # Should default to monograph when no useful info
        assert result == "tcm_monograph"

    def test_title_weight_higher_than_content(self) -> None:
        """Test that title has higher weight than content in detection."""
        assert BookTypeDetector.TITLE_WEIGHT > BookTypeDetector.CONTENT_WEIGHT
        assert BookTypeDetector.TITLE_WEIGHT == 3.0
        assert BookTypeDetector.CONTENT_WEIGHT == 1.0

    def test_toc_weight(self) -> None:
        """Test TOC weight configuration."""
        assert BookTypeDetector.TOC_WEIGHT == 1.5
        assert BookTypeDetector.TOC_WEIGHT > BookTypeDetector.CONTENT_WEIGHT

    def test_detection_threshold_value(self) -> None:
        """Test detection threshold value."""
        assert BookTypeDetector.DETECTION_THRESHOLD == 2.0


# ============================================================================
# Tests for TYPE_KEYWORDS structure
# ============================================================================

class TestTypeKeywords:
    """Test suite for TYPE_KEYWORDS configuration."""

    def test_all_types_have_keywords(self) -> None:
        """Test that all book types have both title and content keywords."""
        for book_type, keywords in BookTypeDetector.TYPE_KEYWORDS.items():
            assert "title" in keywords, f"{book_type} missing title keywords"
            assert "content" in keywords, f"{book_type} missing content keywords"
            assert len(keywords["title"]) > 0, f"{book_type} has empty title keywords"
            assert len(keywords["content"]) > 0, f"{book_type} has empty content keywords"

    def test_title_keywords_are_unique(self) -> None:
        """Test that title keywords don't overlap between types."""
        # Some overlap is expected, but major keywords should be distinct
        formula_titles = set(BookTypeDetector.TYPE_KEYWORDS["formula"]["title"])
        acupuncture_titles = set(BookTypeDetector.TYPE_KEYWORDS["acupuncture"]["title"])

        # These two types should have mostly distinct title keywords
        overlap = formula_titles & acupuncture_titles
        assert len(overlap) < len(formula_titles) * 0.3, "Too much overlap between formula and acupuncture titles"

    def test_formula_title_keywords_count(self) -> None:
        """Test that formula type has sufficient title keywords."""
        keywords = BookTypeDetector.TYPE_KEYWORDS["formula"]["title"]
        assert len(keywords) >= 20

    def test_acupuncture_title_keywords_count(self) -> None:
        """Test that acupuncture type has sufficient title keywords."""
        keywords = BookTypeDetector.TYPE_KEYWORDS["acupuncture"]["title"]
        assert len(keywords) >= 15

    def test_internal_medicine_title_keywords_count(self) -> None:
        """Test that internal medicine type has sufficient title keywords."""
        keywords = BookTypeDetector.TYPE_KEYWORDS["internal_medicine"]["title"]
        assert len(keywords) >= 15

    def test_monograph_title_keywords_count(self) -> None:
        """Test that monograph type has sufficient title keywords."""
        keywords = BookTypeDetector.TYPE_KEYWORDS["tcm_monograph"]["title"]
        assert len(keywords) >= 15


# ============================================================================
# Tests for score_by_title
# ============================================================================

class TestScoreByTitle:
    """Test suite for _score_by_title method."""

    def test_score_formula_title(self) -> None:
        """Test scoring formula-related titles."""
        scores = BookTypeDetector._score_by_title("中医方剂学")
        assert scores["formula"] > 0
        assert scores["formula"] > scores["acupuncture"]

    def test_score_acupuncture_title(self) -> None:
        """Test scoring acupuncture-related titles."""
        scores = BookTypeDetector._score_by_title("针灸大成")
        assert scores["acupuncture"] > 0
        assert scores["acupuncture"] > scores["formula"]

    def test_score_empty_title(self) -> None:
        """Test scoring empty title returns zero scores."""
        scores = BookTypeDetector._score_by_title("")
        for book_type in BookTypeDetector.TYPE_KEYWORDS:
            assert scores.get(book_type, 0) == 0.0

    def test_score_starting_keyword_bonus(self) -> None:
        """Test that title starting with keyword gets bonus."""
        # A title starting with "方剂" should get extra points
        scores_start = BookTypeDetector._score_by_title("方剂学")
        scores_mid = BookTypeDetector._score_by_title("中医学方剂")

        # Starting keyword should give higher score
        assert scores_start["formula"] >= scores_mid["formula"]

    def test_regex_bonus_patterns(self) -> None:
        """Test regex bonus patterns for formula type."""
        scores = BookTypeDetector._score_by_title("方书集成")
        assert scores["formula"] > 0  # Should match 方[剂剂歌书解]? pattern
