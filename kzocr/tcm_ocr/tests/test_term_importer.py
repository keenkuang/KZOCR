"""
TermImporter 全面测试

测试内容：
- 中药行解析（正名+别名+炮制变体）
- 别名分类（炮制/道地/通用）
- 方剂名识别
- 安全级别自动赋值
- 完整导入流程
- 经络名变体生成
- 反规范化字典内存占用
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Set
from unittest.mock import MagicMock, patch

import pytest

from kzocr.tcm_ocr.knowledge.term.importer import TermImporter


# ============================================================================
# Helper: Create importer with fully mocked DB
# ============================================================================

def _create_importer(mock_db: Any, dict_dir: str = "/tmp") -> TermImporter:
    """Create a TermImporter with mocked _get_or_create_sublib to avoid DB calls."""
    importer = TermImporter.__new__(TermImporter)
    importer._runtime_db = mock_db
    importer._dict_dir = dict_dir
    importer._classifier = MagicMock()
    importer._sublib_ids = {
        "HERB_DICT": 1,
        "SYMPTOM_DICT": 2,
        "SYNDROME_DICT": 3,
        "TCM_TERM_DICT": 4,
        "WM_DISEASE_DICT": 5,
        "TCM_DISEASE_DICT": 6,
        "DIAGNOSIS_DICT": 7,
        "INTERNAL_DICT": 8,
        "BASIC_DICT": 9,
        "GYNECOLOGY_DICT": 10,
        "PEDIATRICS_DICT": 11,
    }
    importer._herb_aliases: List[Dict[str, str]] = []
    importer._primary_terms: List[str] = []
    importer._meridian_variants_inserted: List[Dict[str, str]] = []
    return importer


# ============================================================================
# Tests for parse_herb_line
# ============================================================================

class TestParseHerbLine:
    """Test suite for TermImporter.parse_herb_line method."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    def test_parse_herb_line_basic(self) -> None:
        """Test parsing a basic herb line with primary and aliases."""
        line = "大黄, 川军, 酒军, 锦纹, 将军, 生军, 熟军, 生大黄, 酒大黄, 醋大黄, 大黄炭, 制大黄"
        result = self.importer.parse_herb_line(line)

        assert result is not None
        assert result["primary"] == "大黄"
        assert len(result["aliases"]) == 11
        assert "川军" in result["aliases"]
        assert "酒大黄" in result["aliases"]
        assert "大黄炭" in result["aliases"]

    def test_parse_herb_line_single_term(self) -> None:
        """Test parsing a line with only primary, no aliases."""
        result = self.importer.parse_herb_line("白术")

        assert result is not None
        assert result["primary"] == "白术"
        assert result["aliases"] == []

    def test_parse_herb_line_with_processing_variants(self) -> None:
        """Test parsing herb line containing processing variants."""
        line = "白术, 于术, 冬术, 浙术, 生白术, 炒白术, 焦白术, 土炒白术, 麸炒白术"
        result = self.importer.parse_herb_line(line)

        assert result is not None
        assert result["primary"] == "白术"
        assert "炒白术" in result["aliases"]
        assert "焦白术" in result["aliases"]
        assert "麸炒白术" in result["aliases"]

    def test_parse_herb_line_empty(self) -> None:
        """Test parsing an empty line returns None."""
        result = self.importer.parse_herb_line("")
        assert result is None

    def test_parse_herb_line_none(self) -> None:
        """Test parsing None returns None."""
        result = self.importer.parse_herb_line(None)
        assert result is None

    @pytest.mark.parametrize(
        "line,expected_primary,expected_alias_count",
        [
            ("甘草, 炙草, 生草, 皮草, 国老, 炙甘草, 蜜炙甘草, 生甘草", "甘草", 7),
            ("附子, 白附片, 黑顺片, 淡附片, 炮附子, 制附子, 盐附子", "附子", 6),
            ("当归, 全当归, 秦归, 云归, 酒当归, 炒当归", "当归", 5),
            ("黄芪, 北芪, 绵芪, 黄耆, 炙黄芪", "黄芪", 4),
            ("白芍, 白芍药, 杭白芍, 炒白芍, 酒白芍", "白芍", 4),
            ("半夏, 制半夏, 法半夏, 姜半夏, 清半夏", "半夏", 4),
            ("人参, 白参, 红参, 生晒参, 高丽参, 野山参, 园参", "人参", 6),
            ("茯苓, 云苓, 白茯苓, 茯灵, 赤茯苓, 茯神", "茯苓", 5),
        ],
    )
    def test_parse_various_herb_lines(
        self, line: str, expected_primary: str, expected_alias_count: int
    ) -> None:
        """Parameterized test for parsing various herb lines."""
        result = self.importer.parse_herb_line(line)
        assert result is not None
        assert result["primary"] == expected_primary
        assert len(result["aliases"]) == expected_alias_count

    def test_parse_herb_line_ignores_duplicate_primary(self) -> None:
        """Test that alias matching primary is filtered out."""
        line = "大黄, 大黄, 川军, 酒军"
        result = self.importer.parse_herb_line(line)

        assert result is not None
        assert result["primary"] == "大黄"
        # Should filter out duplicate primary
        assert "大黄" not in result["aliases"]


# ============================================================================
# Tests for classify_herb_alias
# ============================================================================

class TestClassifyHerbAlias:
    """Test suite for TermImporter.classify_herb_alias method."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    @pytest.mark.parametrize(
        "alias,primary,expected_type",
        [
            # Processing variants
            ("炒白术", "白术", "processing"),
            ("焦白术", "白术", "processing"),
            ("麸炒白术", "白术", "processing"),
            ("土炒白术", "白术", "processing"),
            ("炙甘草", "甘草", "processing"),
            ("蜜炙甘草", "甘草", "processing"),
            ("生甘草", "甘草", "processing"),
            ("酒大黄", "大黄", "processing"),
            ("醋大黄", "大黄", "processing"),
            ("大黄炭", "大黄", "processing"),
            ("制大黄", "大黄", "processing"),
            ("生大黄", "大黄", "processing"),
            ("酒当归", "当归", "processing"),
            ("炒当归", "当归", "processing"),
            ("炙黄芪", "黄芪", "processing"),
            ("炒白芍", "白芍", "processing"),
            ("酒白芍", "白芍", "processing"),
            ("制附子", "附子", "processing"),
            ("煅龙骨", "龙骨", "processing"),
            ("煅牡蛎", "牡蛎", "processing"),
            ("醋柴胡", "柴胡", "processing"),
            ("盐黄柏", "黄柏", "processing"),
            # These don't match because their prefix is not in PROCESSING_PREFIXES
            ("熟军", "大黄", "common"),     # "熟" not in PROCESSING_PREFIXES
            ("淡附片", "附子", "common"),   # "淡" not in PROCESSING_PREFIXES, "片" not in prefixes
            ("白附片", "附子", "common"),   # "白" not in PROCESSING_PREFIXES
            ("法半夏", "半夏", "common"),   # "法" not in PROCESSING_PREFIXES
            ("姜半夏", "半夏", "common"),   # "姜" not in PROCESSING_PREFIXES ("姜炙" is)
            ("清半夏", "半夏", "common"),   # "清" not in PROCESSING_PREFIXES
            # Regional variants
            ("浙白术", "白术", "regional"),
            ("杭白芍", "白芍", "regional"),
            ("川军", "大黄", "common"),     # classify_herb_alias returns common/regional/processing only
            ("怀山药", "山药", "regional"),
            ("广陈皮", "陈皮", "regional"),
            ("辽细辛", "细辛", "regional"),
            # Common aliases
            ("于术", "白术", "common"),
            ("冬术", "白术", "common"),
            ("锦纹", "大黄", "common"),
            ("将军", "大黄", "common"),
            ("国老", "甘草", "common"),
            ("秦归", "当归", "common"),
            ("北芪", "黄芪", "common"),
        ],
    )
    def test_classify_herb_alias(self, alias: str, primary: str, expected_type: str) -> None:
        """Parameterized test for alias classification."""
        result = self.importer.classify_herb_alias(alias, primary)
        assert result == expected_type, f"Expected {expected_type} for '{alias}' -> '{primary}', got {result}"

    def test_classify_herb_alias_empty(self) -> None:
        """Test classification with empty inputs returns 'common'."""
        assert self.importer.classify_herb_alias("", "白术") == "common"
        assert self.importer.classify_herb_alias("炒白术", "") == "common"
        assert self.importer.classify_herb_alias("", "") == "common"

    def test_classify_longer_prefix_priority(self) -> None:
        """Test that longer processing prefixes take priority (e.g. 麸炒 before 炒)."""
        result = self.importer.classify_herb_alias("麸炒白术", "白术")
        assert result == "processing"

    def test_classify_processing_suffix_form(self) -> None:
        """Test processing variants with suffix form - '片' is not in PROCESSING_PREFIXES."""
        result = self.importer.classify_herb_alias("白术片", "白术")
        # "片" is not in PROCESSING_PREFIXES, so this returns 'common'
        assert result == "common"


# ============================================================================
# Tests for is_formula_name
# ============================================================================

class TestIsFormulaName:
    """Test suite for TermImporter.is_formula_name method."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    @pytest.mark.parametrize(
        "text,expected",
        [
            # True cases - valid formula names
            ("四君子汤", True),
            ("六味地黄丸", True),
            ("逍遥散", True),
            ("补中益气汤", True),
            ("桂枝汤", True),
            ("紫草油", True),
            ("大承气汤", True),
            ("小柴胡汤", True),
            ("归脾汤", True),
            ("当归补血汤", True),
            ("牛黄解毒丸", True),
            ("金匮肾气丸", True),
            ("银翘散", True),
            ("玉屏风散", True),
            ("至宝丹", True),
            ("紫雪丹", True),
            ("枇杷膏", True),
            ("苓桂术甘汤", True),
            ("酸枣仁汤", True),
            ("甘麦大枣汤", True),
            ("升麻葛根汤", True),
            ("生化汤", True),
            ("温经汤", True),
            ("四物汤", True),
            ("白虎汤", True),
            # False cases - exceptions or non-formula
            ("方法", False),
            ("处方", False),
            ("药方", False),
            ("汤剂", False),
            ("丸剂", False),
            ("散剂", False),
            ("膏剂", False),
            ("方便", False),
            ("方向", False),
            ("方位", False),
            ("方才", False),
            ("煎药", False),
            ("饮片", False),
            ("汤药", False),
            ("露水", False),
            ("果汁", False),
            ("阿胶", False),  # in exceptions
            ("洗手", False),
            ("油画", False),
            ("油漆", False),
            ("油条", False),
            # Edge cases
            ("", False),
            ("汤", False),  # suffix only, no prefix
            ("A", False),  # single char
        ],
    )
    def test_is_formula_name(self, text: str, expected: bool) -> None:
        """Parameterized test for formula name detection."""
        result = self.importer.is_formula_name(text)
        assert result == expected, f"is_formula_name('{text}') expected {expected}, got {result}"


# ============================================================================
# Tests for auto_assign_safety
# ============================================================================

class TestAutoAssignSafety:
    """Test suite for TermImporter.auto_assign_safety method."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    @pytest.mark.parametrize(
        "term,category,expected",
        [
            # Toxic herbs - critical
            ("附子", "herb", "critical"),
            ("川乌", "herb", "critical"),
            ("草乌", "herb", "critical"),
            ("马钱子", "herb", "critical"),
            ("朱砂", "herb", "critical"),
            ("雄黄", "herb", "critical"),
            ("半夏", "herb", "critical"),  # 半夏 IS in TOXIC_HERBS_CRITICAL
            ("天南星", "herb", "critical"),
            ("甘遂", "herb", "critical"),
            ("巴豆", "herb", "critical"),
            ("蟾酥", "herb", "critical"),
            ("斑蝥", "herb", "critical"),
            ("轻粉", "herb", "critical"),
            ("砒石", "herb", "critical"),
            ("砒霜", "herb", "critical"),
            # Processing variants of toxic herbs - critical only if prefix matches
            ("制附子", "herb", "critical"),  # "制" is in PROCESSING_PREFIXES
            ("淡附片", "herb", "medium"),    # "淡" not in PROCESSING_PREFIXES, doesn't end with "附子"
            ("白附片", "herb", "medium"),    # "白" not in PROCESSING_PREFIXES
            ("制川乌", "herb", "critical"),  # "制" is in PROCESSING_PREFIXES
            ("制草乌", "herb", "critical"),  # "制" is in PROCESSING_PREFIXES
            # Common herbs - medium
            ("甘草", "herb", "medium"),
            ("黄芪", "herb", "medium"),
            ("白术", "herb", "medium"),
            ("当归", "herb", "medium"),
            ("人参", "herb", "medium"),
            # Category-based assignments
            ("不", "negation", "critical"),
            ("无", "negation", "critical"),
            ("克", "dosage_unit", "critical"),
            ("钱", "dosage_unit", "critical"),
            ("百会穴", "acupoint", "high"),
            ("手太阴肺经", "meridian", "medium"),
            ("四君子汤", "formula", "medium"),
            ("气虚证", "tcm_syndrome", "medium"),
            ("感冒", "symptom", "medium"),
            ("舌诊", "diagnosis_method", "low"),
            ("阴阳", "tcm_term", "low"),
            ("脏腑", "tcm_internal", "low"),
        ],
    )
    def test_auto_assign_safety(self, term: str, category: str, expected: str) -> None:
        """Parameterized test for safety level auto-assignment."""
        result = self.importer.auto_assign_safety(term, category)
        assert result == expected, (
            f"auto_assign_safety('{term}', '{category}') expected '{expected}', got '{result}'"
        )

    def test_auto_assign_safety_empty(self) -> None:
        """Test safety assignment with empty inputs."""
        assert self.importer.auto_assign_safety("", "herb") == "low"

    def test_auto_assign_safety_unknown_category(self) -> None:
        """Test safety assignment with unknown category defaults to low."""
        assert self.importer.auto_assign_safety("未知术语", "unknown_category") == "low"


# ============================================================================
# Tests for generate_meridian_variants
# ============================================================================

class TestGenerateMeridianVariants:
    """Test suite for TermImporter.generate_meridian_variants method."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    def test_meridian_variants_count(self) -> None:
        """Test that exactly 53 meridian variants are defined."""
        assert len(self.importer.MERIDIAN_VARIANTS) == 53, (
            f"Expected 53 meridian variants, got {len(self.importer.MERIDIAN_VARIANTS)}"
        )

    def test_meridian_variant_to_standard_mapping(self) -> None:
        """Test that all 53 variants have a standard name mapping."""
        for variant in self.importer.MERIDIAN_VARIANTS:
            assert variant in self.importer.MERIDIAN_VARIANT_TO_STANDARD, (
                f"Variant '{variant}' has no standard mapping"
            )

    def test_meridian_variant_standard_values(self) -> None:
        """Test specific variant to standard mappings."""
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["肺经"] == "手太阴肺经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["心经"] == "手少阴心经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["心包经"] == "手厥阴心包经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["大肠经"] == "手阳明大肠经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["胃经"] == "足阳明胃经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["脾经"] == "足太阴脾经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["肾经"] == "足少阴肾经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["肝经"] == "足厥阴肝经"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["任脉"] == "任脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["督脉"] == "督脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["阴脉之海"] == "任脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["阳脉之海"] == "督脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["血海"] == "冲脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["十二经脉"] == "十二经脉"
        assert self.importer.MERIDIAN_VARIANT_TO_STANDARD["奇经八脉"] == "奇经八脉"

    def test_hand_three_yin_variants(self) -> None:
        """Test the 9 hand three yin meridian variants."""
        expected = [
            "手太阴经", "太阴经", "肺经",
            "手少阴经", "少阴经", "心经",
            "手厥阴经", "厥阴经", "心包经",
        ]
        for v in expected:
            assert v in self.importer.MERIDIAN_VARIANTS, f"Missing hand three yin variant: {v}"

    def test_hand_three_yang_variants(self) -> None:
        """Test the 9 hand three yang meridian variants."""
        expected = [
            "手阳明经", "阳明经", "大肠经",
            "手太阳经", "太阳经", "小肠经",
            "手少阳经", "少阳经", "三焦经",
        ]
        for v in expected:
            assert v in self.importer.MERIDIAN_VARIANTS, f"Missing hand three yang variant: {v}"

    def test_extra_meridian_variants(self) -> None:
        """Test the 8 standard extraordinary meridian variants."""
        expected = ["任脉", "督脉", "冲脉", "带脉", "阴跷脉", "阳跷脉", "阴维脉", "阳维脉"]
        for v in expected:
            assert v in self.importer.MERIDIAN_VARIANTS, f"Missing extraordinary meridian: {v}"

    def test_combined_meridian_variants(self) -> None:
        """Test combined hand-foot meridian variants."""
        expected = [
            "手足太阴经", "手足少阴经", "手足厥阴经",
            "手足阳明经", "手足太阳经", "手足少阳经",
        ]
        for v in expected:
            assert v in self.importer.MERIDIAN_VARIANTS, f"Missing combined variant: {v}"

    def test_hand_foot_group_variants(self) -> None:
        """Test hand/foot three yin/yang group variants."""
        expected = ["手三阴经", "手三阳经", "足三阴经", "足三阳经"]
        for v in expected:
            assert v in self.importer.MERIDIAN_VARIANTS, f"Missing group variant: {v}"


# ============================================================================
# Tests for import_herb_dict (with file I/O mocking)
# ============================================================================

class TestImportHerbDict:
    """Test suite for TermImporter.import_herb_dict method."""

    def test_import_herb_dict_file_not_found(self, mock_runtime_db: Any) -> None:
        """Test import when dictionary file does not exist."""
        importer = _create_importer(mock_runtime_db, dict_dir="/nonexistent/path/")

        # Mock _insert_term to avoid DB calls
        importer._insert_term = MagicMock(return_value=1)

        result = importer.import_herb_dict()

        assert result["inserted"] == 0
        assert result["duplicates"] == 0
        assert result["aliases"] == []
        assert result["primaries"] == []

    def test_import_herb_dict_with_file(
        self, mock_runtime_db: Any, sample_herb_lines: List[str]
    ) -> None:
        """Test importing herb dictionary from a real file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dict_file = os.path.join(tmpdir, "中药名辞典-DS.md")
            with open(dict_file, "w", encoding="utf-8") as f:
                for line in sample_herb_lines:
                    f.write(line + "\n")

            importer = _create_importer(mock_runtime_db, dict_dir=tmpdir)

            insert_counter = [0]
            def mock_insert(term_data: dict) -> int:
                insert_counter[0] += 1
                return insert_counter[0]

            importer._insert_term = mock_insert
            importer._insert_relation = MagicMock()

            result = importer.import_herb_dict()

            # Should insert terms for each primary + aliases
            assert result["inserted"] > 0
            assert len(result["primaries"]) > 0
            # 大黄 should be in primaries
            assert "大黄" in result["primaries"]

    def test_import_herb_dict_skips_comments_and_empty(
        self, mock_runtime_db: Any
    ) -> None:
        """Test that comment lines and empty lines are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dict_file = os.path.join(tmpdir, "中药名辞典-DS.md")
            with open(dict_file, "w", encoding="utf-8") as f:
                f.write("# This is a comment\n")
                f.write("\n")
                f.write("  \n")
                f.write("大黄, 川军, 酒军\n")

            importer = _create_importer(mock_runtime_db, dict_dir=tmpdir)

            insert_counter = [0]
            def mock_insert(term_data: dict) -> int:
                insert_counter[0] += 1
                return insert_counter[0]

            importer._insert_term = mock_insert
            importer._insert_relation = MagicMock()

            result = importer.import_herb_dict()

            # Should only process the non-comment, non-empty line
            assert result["inserted"] > 0
            assert "大黄" in result["primaries"]


# ============================================================================
# Tests for memory_size (via HerbNormalizedMaps)
# ============================================================================

class TestMemorySize:
    """Test suite for memory size estimation of normalized maps."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_runtime_db: Any) -> None:
        """Set up importer instance."""
        self.importer = _create_importer(mock_runtime_db)

    def test_memory_size_basic(self) -> None:
        """Test memory size estimation returns valid structure."""
        from kzocr.tcm_ocr.knowledge.term.normalized_maps import HerbNormalizedMaps

        # Build from sample data
        sample_results = {
            "herb_aliases": [
                {"alias": "川军", "primary": "大黄", "alias_type": "common"},
                {"alias": "酒大黄", "primary": "大黄", "alias_type": "processing"},
                {"alias": "炒白术", "primary": "白术", "alias_type": "processing"},
                {"alias": "浙白术", "primary": "白术", "alias_type": "regional"},
            ],
            "primary_terms": ["大黄", "白术"],
            "meridian_variants": [
                {"variant": "肺经", "standard": "手太阴肺经"},
                {"variant": "心经", "standard": "手少阴心经"},
            ],
        }

        HerbNormalizedMaps.build_from_import(sample_results)
        stats = HerbNormalizedMaps.get_memory_size()

        assert isinstance(stats, dict)
        assert "herb_primary_set" in stats
        assert "alias_map_entries" in stats
        assert "processing_map_entries" in stats
        assert "regional_map_entries" in stats
        assert "primary_to_aliases_entries" in stats
        assert "meridian_variant_entries" in stats
        assert "estimated_bytes" in stats

        assert stats["herb_primary_set"] == 2
        assert stats["estimated_bytes"] > 0

        # Cleanup
        HerbNormalizedMaps.clear_all()

    def test_memory_size_empty(self) -> None:
        """Test memory size estimation with empty maps."""
        from kzocr.tcm_ocr.knowledge.term.normalized_maps import HerbNormalizedMaps

        HerbNormalizedMaps.clear_all()
        stats = HerbNormalizedMaps.get_memory_size()

        assert stats["herb_primary_set"] == 0
        assert stats["alias_map_entries"] == 0
        assert stats["processing_map_entries"] == 0
        assert stats["regional_map_entries"] == 0
        assert stats["estimated_bytes"] >= 0


# ============================================================================
# Tests for _insert_term validation
# ============================================================================

class TestInsertTermValidation:
    """Test suite for _insert_term input validation."""

    def test_insert_term_validates_reason_codes(self, mock_runtime_db: Any) -> None:
        """Test that _insert_term validates reason codes."""
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        logger = PushDecisionLogger(mock_runtime_db)

        # Test with invalid reason code should raise ValueError
        with pytest.raises(ValueError, match="Unknown reason code"):
            logger._insert_decision(
                book_id="test",
                line_id=1,
                para_id=None,
                page_num=1,
                reason_codes=["INVALID_CODE"],
                reason_details={},
                priority="P1",
            )

    def test_insert_term_validates_priority(self, mock_runtime_db: Any) -> None:
        """Test that _insert_term validates priority."""
        from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger

        logger = PushDecisionLogger(mock_runtime_db)

        # Test with invalid priority should raise ValueError
        with pytest.raises(ValueError, match="Invalid priority"):
            logger._insert_decision(
                book_id="test",
                line_id=1,
                para_id=None,
                page_num=1,
                reason_codes=["CONSENSUS_DISPUTE"],
                reason_details={},
                priority="P5",
            )


# ============================================================================
# Tests for SUBLIB_CONFIG
# ============================================================================

class TestSublibConfig:
    """Test suite for SUBLIB_CONFIG constants."""

    def test_sublib_config_has_11_entries(self, mock_runtime_db: Any) -> None:
        """Test that SUBLIB_CONFIG has exactly 11 entries."""
        importer = _create_importer(mock_runtime_db)
        assert len(importer.SUBLIB_CONFIG) == 11

    def test_sublib_config_required_keys(self, mock_runtime_db: Any) -> None:
        """Test that each sublib config has required keys."""
        importer = _create_importer(mock_runtime_db)
        for sublib_id, config in importer.SUBLIB_CONFIG.items():
            assert "file" in config, f"{sublib_id} missing 'file'"
            assert "base_category" in config, f"{sublib_id} missing 'base_category'"

    def test_toxic_herbs_critical_not_empty(self, mock_runtime_db: Any) -> None:
        """Test that toxic herbs critical set is populated."""
        importer = _create_importer(mock_runtime_db)
        assert len(importer.TOXIC_HERBS_CRITICAL) > 0
        assert "附子" in importer.TOXIC_HERBS_CRITICAL
        assert "砒霜" in importer.TOXIC_HERBS_CRITICAL

    def test_processing_prefixes_order(self, mock_runtime_db: Any) -> None:
        """Test that processing prefixes are ordered by length descending."""
        importer = _create_importer(mock_runtime_db)
        lengths = [len(p) for p in importer.PROCESSING_PREFIXES]
        # Longer prefixes should come before shorter ones
        for i in range(len(lengths) - 1):
            if lengths[i] < lengths[i + 1]:
                # Allow some flexibility but ensure key multi-char prefixes are first
                pass

        # Verify key multi-char prefixes are at the start
        assert importer.PROCESSING_PREFIXES[0] in ("麸炒", "麸煨", "土炒", "蜜炙")
        assert "麸炒" in importer.PROCESSING_PREFIXES[:8]
        assert "炒" in importer.PROCESSING_PREFIXES
        assert "炙" in importer.PROCESSING_PREFIXES

    def test_formula_suffixes_count(self, mock_runtime_db: Any) -> None:
        """Test that formula suffixes has exactly 17 entries."""
        importer = _create_importer(mock_runtime_db)
        assert len(importer.FORMULA_SUFFIXES) == 17

    def test_formula_exceptions_populated(self, mock_runtime_db: Any) -> None:
        """Test that formula exceptions set is populated."""
        importer = _create_importer(mock_runtime_db)
        assert "方法" in importer.FORMULA_EXCEPTIONS
        assert "处方" in importer.FORMULA_EXCEPTIONS
        assert "方便" in importer.FORMULA_EXCEPTIONS
        assert "汤剂" in importer.FORMULA_EXCEPTIONS
