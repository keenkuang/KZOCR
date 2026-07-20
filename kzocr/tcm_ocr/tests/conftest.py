"""
TCM-Modern-OCR System - Pytest Fixtures

Provides shared fixtures for all test modules including:
- Mock RuntimeDB with cursor context manager
- Mock book metadata
- Sample herb line data
- Sample classification texts
- Temporary database fixtures
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, Generator, List, Optional

import pytest


# ============================================================================
# Mock RuntimeDB Fixture
# ============================================================================

class MockCursor:
    """Mock database cursor for testing."""

    def __init__(self, rows: Optional[List[dict]] = None) -> None:
        self.rows = rows or []
        self._idx = 0
        self.executed_queries: List[tuple] = []
        self._return_queue: List[list] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        """Record executed query and parameters."""
        self.executed_queries.append((query, params))
        self._idx = 0

    def fetchone(self) -> Optional[dict]:
        """Return next row or None."""
        if self._idx < len(self.rows):
            row = self.rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self) -> List[dict]:
        """Return all rows."""
        return list(self.rows)

    def __enter__(self) -> "MockCursor":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class MockRuntimeDB:
    """Mock RuntimeDB for unit testing without PostgreSQL."""

    def __init__(self, cursor: Optional[MockCursor] = None) -> None:
        self._cursor = cursor or MockCursor()
        self._terms: Dict[str, dict] = {}
        self._sublibs: Dict[str, int] = {}
        self._decisions: Dict[int, dict] = {}
        self._decision_id_counter = 0
        self._events: List[dict] = []

    def get_cursor(self) -> MockCursor:
        """Return cursor context manager."""
        return self._cursor

    def add_mock_rows(self, rows: List[dict]) -> None:
        """Set mock rows to be returned by cursor."""
        self._cursor.rows = rows

    def get_executed_queries(self) -> List[tuple]:
        """Get all executed queries for verification."""
        return self._cursor.executed_queries

    def reset_queries(self) -> None:
        """Clear executed query log."""
        self._cursor.executed_queries = []


@pytest.fixture
def mock_runtime_db() -> MockRuntimeDB:
    """
    Provide a MockRuntimeDB instance with default empty cursor.

    Returns:
        MockRuntimeDB: Fresh mock database instance.
    """
    return MockRuntimeDB()


@pytest.fixture
def mock_cursor_with_sublib() -> MockCursor:
    """
    Provide a MockCursor pre-configured with a Sublib row.

    Returns:
        MockCursor: Cursor that returns a Sublib record.
    """
    return MockCursor(rows=[{"id": 1, "name": "HERB_DICT", "description": "herb"}])


# ============================================================================
# Mock Book Metadata Fixtures
# ============================================================================

@pytest.fixture
def mock_book_meta() -> Dict[str, str]:
    """
    Provide a default book metadata dictionary.

    Returns:
        dict: Book metadata with title, author, publisher.
    """
    return {
        "title": "中医方剂学",
        "author": "张三",
        "publisher": "人民卫生出版社",
        "year": "2020",
    }


@pytest.fixture
def formula_book_meta() -> Dict[str, str]:
    """Book metadata for a formula book."""
    return {
        "title": "伤寒论方剂解析",
        "author": "张仲景",
        "publisher": "古籍出版社",
    }


@pytest.fixture
def acupuncture_book_meta() -> Dict[str, str]:
    """Book metadata for an acupuncture book."""
    return {
        "title": "针灸大成",
        "author": "杨继洲",
        "publisher": "中医出版社",
    }


@pytest.fixture
def internal_medicine_book_meta() -> Dict[str, str]:
    """Book metadata for an internal medicine book."""
    return {
        "title": "中医内科学",
        "author": "王永炎",
        "publisher": "中国中医药出版社",
    }


@pytest.fixture
def monograph_book_meta() -> Dict[str, str]:
    """Book metadata for a TCM monograph."""
    return {
        "title": "临证医案集要",
        "author": "名医",
        "publisher": "中华医学出版社",
    }


# ============================================================================
# Sample Herb Line Data Fixtures
# ============================================================================

@pytest.fixture
def sample_herb_lines() -> List[str]:
    """
    Provide sample herb line data from dictionary files.

    Returns:
        list: List of herb dictionary lines with primary and aliases.
    """
    return [
        # 正名 + 别名（通用 + 炮制 + 道地）
        "大黄, 川军, 酒军, 锦纹, 将军, 生军, 熟军, 生大黄, 酒大黄, 醋大黄, 大黄炭, 制大黄",
        "白术, 于术, 冬术, 浙术, 生白术, 炒白术, 焦白术, 土炒白术, 麸炒白术",
        "甘草, 炙草, 生草, 皮草, 国老, 炙甘草, 蜜炙甘草, 生甘草",
        "附子, 白附片, 黑顺片, 淡附片, 炮附子, 制附子, 盐附子",
        "当归, 全当归, 秦归, 云归, 酒当归, 炒当归",
        "黄芪, 北芪, 绵芪, 黄耆, 炙黄芪",
        "白芍, 白芍药, 杭白芍, 炒白芍, 酒白芍",
        "半夏, 制半夏, 法半夏, 姜半夏, 清半夏",
    ]


@pytest.fixture
def sample_single_herb_line() -> str:
    """Single herb line for focused testing."""
    return "大黄, 川军, 酒军, 锦纹, 将军, 生军, 熟军, 生大黄, 酒大黄, 醋大黄, 大黄炭, 制大黄"


@pytest.fixture
def sample_dahuang_aliases() -> List[str]:
    """Aliases for 大黄 (Rhubarb)."""
    return ["川军", "酒军", "锦纹", "将军", "生军", "熟军", "生大黄", "酒大黄", "醋大黄", "大黄炭", "制大黄"]


# ============================================================================
# Sample Classification Text Fixtures
# ============================================================================

@pytest.fixture
def sample_classification_texts() -> Dict[str, List[str]]:
    """
    Provide sample texts for classification testing.

    Returns:
        dict: Texts grouped by expected category.
    """
    return {
        "negation": ["不", "无", "非", "忌", "禁", "勿", "慎"],
        "dosage_unit": ["克", "钱", "两", "分", "斤", "毫升", "升", "钱匕", "丸", "片", "粒"],
        "acupoint": ["百会穴", "关元穴", "足三里穴", "三阴交穴", "合谷穴"],
        "meridian": ["手太阴肺经", "足阳明胃经", "手少阴心经", "任脉", "督脉"],
        "formula": ["四君子汤", "六味地黄丸", "逍遥散", "补中益气汤", "桂枝汤"],
        "tcm_syndrome": ["气虚证", "阴虚证", "阳虚证", "痰湿证", "血瘀证"],
        "tcm_disease": ["感冒病", "咳嗽病", "头痛病", "眩晕病"],
        "unknown": ["方法", "处方", "一般词汇", "未知术语"],
    }


# ============================================================================
# Sample TOC Text Fixtures
# ============================================================================

@pytest.fixture
def formula_toc_text() -> str:
    """Table of contents text for a formula book."""
    return """
    第一章 方剂学总论
    1.1 方剂组成 1.2 君臣佐使 1.3 配伍意义
    第二章 解表剂
    2.1 麻黄汤 2.2 桂枝汤 2.3 九味羌活汤
    第三章 泻下剂
    3.1 大承气汤 3.2 温脾汤 3.3 十枣汤
    第四章 清热剂
    4.1 白虎汤 4.2 黄连解毒汤 4.3 清营汤
    第五章 和解剂
    5.1 小柴胡汤 5.2 逍遥散 5.3 半夏泻心汤
    第六章 补益剂
    6.1 四君子汤 6.2 四物汤 6.3 六味地黄丸
    """


@pytest.fixture
def acupuncture_toc_text() -> str:
    """Table of contents text for an acupuncture book."""
    return """
    第一章 经络总论
    1.1 经络系统概述 1.2 十二经脉 1.3 奇经八脉
    第二章 手太阴肺经
    2.1 经脉循行 2.2 主要病候 2.3 腧穴
    中府穴 云门穴 天府穴 侠白穴 尺泽穴
    第三章 手阳明大肠经
    3.1 经脉循行 3.2 主要病候 3.3 腧穴
    商阳穴 二间穴 三间穴 合谷穴 阳溪穴
    第四章 足阳明胃经
    4.1 经脉循行 4.2 主要病候 4.3 腧穴
    第四章 针刺补泻
    4.1 补泻手法 4.2 迎随补泻 4.3 呼吸补泻
    第五章 艾灸疗法
    5.1 艾炷灸 5.2 艾条灸 5.3 温和灸 5.4 雀啄灸
    """


@pytest.fixture
def internal_medicine_toc_text() -> str:
    """Table of contents text for an internal medicine book."""
    return """
    第一章 中医内科总论
    1.1 辨证论治 1.2 脏腑辨证 1.3 气血津液辨证
    第二章 感冒
    2.1 病因病机 2.2 辨证分型 2.3 治法方药
    风寒感冒 风热感冒 暑湿感冒 气虚感冒
    第三章 咳嗽
    3.1 病因病机 3.2 辨证分型 3.3 治法方药
    外感咳嗽 内伤咳嗽
    第四章 脾胃病证
    4.1 胃痛 4.2 痞满 4.3 呕吐 4.4 泄泻
    第五章 肝胆病证
    5.1 胁痛 5.2 黄疸 5.3 积聚
    六经辨证 卫气营血 三焦辨证
    太阳病 阳明病 少阳病 太阴病 少阴病 厥阴病
    """


# ============================================================================
# Sample Decision Data Fixtures
# ============================================================================

@pytest.fixture
def sample_negation_violation_data() -> dict:
    """Sample data for negation violation decision."""
    return {
        "book_id": "book_001",
        "line_id": 42,
        "page_num": 12,
        "original": "不可用附子",
        "modified": "可用附子",
        "lost": ["不"],
    }


@pytest.fixture
def sample_dosage_alert_data() -> dict:
    """Sample data for dosage alert decision."""
    return {
        "book_id": "book_001",
        "line_id": 43,
        "page_num": 12,
        "alert": {
            "herb_name": "附子",
            "detected_dosage": "30g",
            "standard_max": "15g",
            "standard_min": "3g",
            "severity": "overdose",
        },
    }


@pytest.fixture
def sample_consensus_dispute_data() -> dict:
    """Sample data for consensus dispute decision."""
    return {
        "book_id": "book_001",
        "line_id": 44,
        "page_num": 13,
        "engine_results": {
            "paddleocr": {"text": "当归10g", "confidence": 0.95},
            "mineru": {"text": "当阳10g", "confidence": 0.88},
        },
    }


@pytest.fixture
def sample_glyph_verify_data() -> dict:
    """Sample data for glyph verification failure."""
    return {
        "book_id": "book_001",
        "line_id": 45,
        "page_num": 14,
        "verify_result": {
            "field": "herb_name",
            "expected": "当归",
            "detected": "当阳",
            "confidence": 0.45,
            "verification_method": "structure_match",
        },
        "engine_snapshots": {
            "paddleocr": {"text": "当归10g", "confidence": 0.95},
        },
    }


@pytest.fixture
def sample_llm_timeout_data() -> dict:
    """Sample data for LLM timeout decision."""
    return {
        "book_id": "book_001",
        "line_id": 46,
        "page_num": 15,
        "llm_type": "local",
        "timeout_sec": 30,
        "engine_snapshots": {
            "paddleocr": {"text": "测试结果", "confidence": 0.90},
        },
    }


# ============================================================================
# Temporary Directory Fixture
# ============================================================================

@pytest.fixture
def temp_dict_dir() -> Generator[str, None, None]:
    """
    Create a temporary directory for dictionary files.

    Yields:
        str: Path to temporary directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ============================================================================
# Herb Dictionary File Fixture
# ============================================================================

@pytest.fixture
def herb_dict_file(temp_dict_dir: str, sample_herb_lines: List[str]) -> str:
    """
    Create a temporary herb dictionary file.

    Args:
        temp_dict_dir: Temporary directory path.
        sample_herb_lines: Sample herb lines.

    Returns:
        str: Path to created herb dictionary file.
    """
    file_path = os.path.join(temp_dict_dir, "中药名辞典-DS.md")
    with open(file_path, "w", encoding="utf-8") as f:
        for line in sample_herb_lines:
            f.write(line + "\n")
    return file_path
