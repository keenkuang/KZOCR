"""
PatternCacheV2 全面测试

测试内容：
- 方剂书加载模式
- 针灸书加载模式
- 专著加载模式
- 别名解析O(1)
- 灾难性字段检测
- LRU淘汰
- 内存统计
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from kzocr.tcm_ocr.knowledge.cache.pattern_cache_v2 import PatternCacheV2


# ============================================================================
# Mock helpers
# ============================================================================

def _create_mock_db() -> MagicMock:
    """Create a mock RuntimeDB for testing PatternCacheV2."""
    mock_db = MagicMock()
    mock_cursor = MagicMock()

    # Default: return empty results for DB queries
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.return_value = None
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=None)
    mock_db.get_cursor.return_value = mock_cursor

    return mock_db


@pytest.fixture(autouse=True)
def reset_cache_state() -> None:
    """Reset all PatternCacheV2 class-level state before each test."""
    PatternCacheV2._CRITICAL_TERMS.clear()
    PatternCacheV2._CRITICAL_SET.clear()
    PatternCacheV2._LAYER0_LOADED = False
    PatternCacheV2._HERB_ALIAS_MAP.clear()
    PatternCacheV2._HERB_PRIMARY_SET.clear()
    PatternCacheV2._PROCESSING_MAP.clear()
    PatternCacheV2._REGIONAL_MAP.clear()
    PatternCacheV2._PRIMARY_TO_ALIASES.clear()
    PatternCacheV2._ACUPOINT_TERMS.clear()
    PatternCacheV2._MERIDIAN_TERMS.clear()
    PatternCacheV2._lru_cache.clear()
    PatternCacheV2._lru_maxsize = 1000
    yield
    # Cleanup after test
    PatternCacheV2._CRITICAL_TERMS.clear()
    PatternCacheV2._CRITICAL_SET.clear()
    PatternCacheV2._LAYER0_LOADED = False
    PatternCacheV2._HERB_ALIAS_MAP.clear()
    PatternCacheV2._HERB_PRIMARY_SET.clear()
    PatternCacheV2._PROCESSING_MAP.clear()
    PatternCacheV2._REGIONAL_MAP.clear()
    PatternCacheV2._PRIMARY_TO_ALIASES.clear()
    PatternCacheV2._ACUPOINT_TERMS.clear()
    PatternCacheV2._MERIDIAN_TERMS.clear()
    PatternCacheV2._lru_cache.clear()


# ============================================================================
# Tests for warm_up with different book types
# ============================================================================

class TestWarmUp:
    """Test suite for cache warm-up with different book types."""

    def test_warm_up_formula_book(self) -> None:
        """Test warming up cache for formula book type."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # Layer 0 should be loaded
        assert len(cache._CRITICAL_SET) > 0
        assert cache._LAYER0_LOADED is True

        # Layer 1: herb should be loaded, acupoint should NOT be fully loaded
        assert "herb" in cache._layer1_loaded
        assert len(cache._HERB_PRIMARY_SET) > 0
        assert len(cache._HERB_ALIAS_MAP) > 0

        # Acupoint common_only should be False for formula
        # But formula doesn't load acupoints at all
        assert "acupoint" not in cache._layer1_loaded
        assert "meridian" not in cache._layer1_loaded

    def test_warm_up_acupuncture_book(self) -> None:
        """Test warming up cache for acupuncture book type."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="acupuncture")
        cache.warm_up("acupuncture")

        # Layer 0 should be loaded
        assert len(cache._CRITICAL_SET) > 0

        # Layer 1: acupoint and meridian should be loaded, herb should NOT
        assert "acupoint" in cache._layer1_loaded
        assert "meridian" in cache._layer1_loaded
        assert "herb" not in cache._layer1_loaded

        # Meridian terms should be loaded (built-in)
        assert len(cache._MERIDIAN_TERMS) == 20  # 12 regular + 8 extraordinary

        # Acupoint terms from DB would be empty with mock (no DB data)
        # but acupoint component is marked as loaded

    def test_warm_up_internal_medicine_book(self) -> None:
        """Test warming up cache for internal medicine book type."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="internal_medicine")
        cache.warm_up("internal_medicine")

        # Layer 0 should be loaded
        assert len(cache._CRITICAL_SET) > 0

        # Layer 1: herb and acupoint(common only) should be loaded
        assert "herb" in cache._layer1_loaded
        assert "acupoint" in cache._layer1_loaded
        assert "meridian" not in cache._layer1_loaded

        # Should only have common acupoints (lightweight)
        assert len(cache._ACUPOINT_TERMS) == len(cache.COMMON_ACUPOINTS)

    def test_warm_up_monograph_book(self) -> None:
        """Test warming up cache for TCM monograph type."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="tcm_monograph")
        cache.warm_up("tcm_monograph")

        # All layers should be loaded
        assert "herb" in cache._layer1_loaded
        assert "acupoint" in cache._layer1_loaded
        assert "meridian" in cache._layer1_loaded

        # Herb data should be loaded from built-in data
        assert len(cache._HERB_PRIMARY_SET) > 0

        # Meridian terms should be loaded (built-in 12 + 8 = 20)
        assert len(cache._MERIDIAN_TERMS) == 20

        # Acupoint terms from DB would be empty with mock (no DB data)

    def test_warm_up_auto_defaults_to_monograph(self) -> None:
        """Test that 'auto' book type defaults to monograph loading."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="auto")
        cache.warm_up("auto")

        # Should load all components
        assert "herb" in cache._layer1_loaded
        assert "acupoint" in cache._layer1_loaded
        assert "meridian" in cache._layer1_loaded

    def test_warm_up_layer0_only_loaded_once(self) -> None:
        """Test that Layer 0 is only loaded once across multiple warm_ups."""
        mock_db = _create_mock_db()
        cache1 = PatternCacheV2(mock_db, book_type="formula")
        cache1.warm_up("formula")

        initial_count = len(cache1._CRITICAL_SET)

        # Second warm_up should not reload Layer 0
        cache2 = PatternCacheV2(mock_db, book_type="acupuncture")
        cache2.warm_up("acupuncture")

        assert len(cache2._CRITICAL_SET) == initial_count


# ============================================================================
# Tests for resolve_herb_alias (O(1) lookup)
# ============================================================================

class TestResolveHerbAlias:
    """Test suite for herb alias resolution (O(1) lookup)."""

    def test_resolve_alias_from_alias_map(self) -> None:
        """Test resolving alias from Layer 1 alias map."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # 川军 should resolve to 大黄
        result = cache.resolve_herb_alias("川军")
        assert result == "大黄"

    def test_resolve_alias_from_processing_map(self) -> None:
        """Test resolving processing variant."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # 炙甘草 should resolve to 甘草
        result = cache.resolve_herb_alias("炙甘草")
        assert result == "甘草"

    def test_resolve_alias_primary_name(self) -> None:
        """Test that primary names resolve to themselves."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        result = cache.resolve_herb_alias("大黄")
        assert result == "大黄"

    def test_resolve_alias_not_found(self) -> None:
        """Test that unknown aliases return None."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        result = cache.resolve_herb_alias("完全不存在的药名")
        assert result is None

    def test_resolve_alias_empty(self) -> None:
        """Test resolving empty string returns None."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        assert cache.resolve_herb_alias("") is None
        assert cache.resolve_herb_alias(None) is None  # type: ignore[arg-type]

    def test_resolve_alias_performance(self) -> None:
        """Benchmark: alias resolution should be O(1) fast."""

        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # Time 1000 lookups
        start = time.perf_counter()
        for _ in range(1000):
            cache.resolve_herb_alias("川军")
        elapsed = time.perf_counter() - start

        # Should be very fast (under 10ms for 1000 lookups)
        assert elapsed < 0.01, f"1000 alias lookups took {elapsed:.3f}s, expected < 0.01s"

    @pytest.mark.parametrize(
        "alias,expected_primary",
        [
            ("川军", "大黄"),
            ("生军", "大黄"),
            ("锦纹", "大黄"),
            ("北芪", "黄芪"),
            ("绵芪", "黄芪"),
            ("二花", "金银花"),
            ("双花", "金银花"),
            ("首乌", "何首乌"),
            ("元参", "玄参"),
            ("丹皮", "牡丹皮"),
            ("坤草", "益母草"),
            ("炙甘草", "甘草"),
            ("焦白术", "白术"),
            ("炒白芍", "白芍"),
            ("酒当归", "当归"),
            ("制附子", "附子"),
            ("淡附片", "附子"),
            ("法半夏", "半夏"),
            ("姜半夏", "半夏"),
            ("清半夏", "半夏"),
            ("煅龙骨", "龙骨"),
            ("醋柴胡", "柴胡"),
            ("盐黄柏", "黄柏"),
            ("炒山楂", "山楂"),
            ("炒麦芽", "麦芽"),
        ],
    )
    def test_resolve_various_aliases(
        self, alias: str, expected_primary: str
    ) -> None:
        """Parameterized test for alias resolution."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        result = cache.resolve_herb_alias(alias)
        assert result == expected_primary, f"Expected '{alias}' -> '{expected_primary}', got '{result}'"


# ============================================================================
# Tests for critical field detection
# ============================================================================

class TestCriticalField:
    """Test suite for critical field detection."""

    def test_is_critical_field_toxic_herbs(self) -> None:
        """Test that toxic herbs are detected as critical."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        critical_herbs = [
            "砒霜", "雄黄", "朱砂", "生川乌", "生草乌",
            "生附子", "生半夏", "生南星", "斑蝥", "蟾酥",
            "马钱子", "巴豆", "巴豆霜",
        ]
        for herb in critical_herbs:
            assert cache.is_critical_field(herb) is True, f"'{herb}' should be critical"

    def test_is_critical_field_critical_acupoints(self) -> None:
        """Test that critical acupoints are detected."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="acupuncture")
        cache.warm_up("acupuncture")

        critical_acupoints = [
            "人中穴", "百会穴", "涌泉穴", "关元穴",
            "气海穴", "神阙穴", "大椎穴", "命门穴",
            "心俞穴", "膈俞穴",
        ]
        for acupoint in critical_acupoints:
            assert cache.is_critical_field(acupoint) is True, f"'{acupoint}' should be critical"

    def test_is_critical_field_non_critical(self) -> None:
        """Test that non-critical terms are not detected as critical."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        non_critical = ["甘草", "黄芪", "白术", "当归", "普通词汇"]
        for term in non_critical:
            assert cache.is_critical_field(term) is False, f"'{term}' should NOT be critical"

    def test_is_critical_field_empty(self) -> None:
        """Test that empty/None input returns False."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        assert cache.is_critical_field("") is False


# ============================================================================
# Tests for LRU eviction
# ============================================================================

class TestLRUEviction:
    """Test suite for LRU cache eviction."""

    def test_lru_cache_basic_set_and_get(self) -> None:
        """Test basic LRU cache set and get operations."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        # Set a value
        cache._lru_set("test_key", {"resolved": "测试值", "layer": 2})

        # Get the value
        result = cache._lru_get("test_key")
        assert result is not None
        assert result["resolved"] == "测试值"

    def test_lru_cache_miss(self) -> None:
        """Test LRU cache miss returns None."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        result = cache._lru_get("nonexistent_key")
        assert result is None

    def test_lru_cache_eviction(self) -> None:
        """Test LRU cache eviction when max size is reached."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        # Temporarily set small max size
        original_maxsize = cache._lru_maxsize
        cache._lru_maxsize = 3

        # Fill cache beyond capacity
        cache._lru_set("key1", {"value": 1})
        cache._lru_set("key2", {"value": 2})
        cache._lru_set("key3", {"value": 3})

        # Access key1 to make it recently used
        cache._lru_get("key1")

        # Add key4, should evict key2 (least recently used)
        cache._lru_set("key4", {"value": 4})

        # key1 should still be there (recently accessed)
        assert cache._lru_get("key1") is not None

        # key2 should be evicted
        assert cache._lru_get("key2") is None

        # key3 and key4 should be there
        assert cache._lru_get("key3") is not None
        assert cache._lru_get("key4") is not None

        # Restore
        cache._lru_maxsize = original_maxsize

    def test_lru_cache_update_existing(self) -> None:
        """Test that updating existing key moves it to end."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        cache._lru_maxsize = 3

        cache._lru_set("key1", {"value": 1})
        cache._lru_set("key2", {"value": 2})
        cache._lru_set("key3", {"value": 3})

        # Update key1
        cache._lru_set("key1", {"value": 10})

        # key1 should be the most recently used
        result = cache._lru_get("key1")
        assert result["value"] == 10

        # Add key4, key2 should be evicted
        cache._lru_set("key4", {"value": 4})
        assert cache._lru_get("key2") is None

        cache._lru_maxsize = 1000


# ============================================================================
# Tests for memory stats
# ============================================================================

class TestMemoryStats:
    """Test suite for memory statistics."""

    def test_memory_stats_structure(self) -> None:
        """Test that memory stats returns correct structure."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="tcm_monograph")
        cache.warm_up("tcm_monograph")

        stats = cache.get_memory_stats()

        # Check overall structure
        assert "layer_0" in stats
        assert "layer_1" in stats
        assert "layer_2" in stats
        assert "overall" in stats

        # Check Layer 0 stats
        assert "term_count" in stats["layer_0"]
        assert "memory_bytes" in stats["layer_0"]
        assert "memory_kb" in stats["layer_0"]
        assert "hit_count" in stats["layer_0"]

        # Check Layer 1 stats
        assert "herb_aliases" in stats["layer_1"]
        assert "herb_primaries" in stats["layer_1"]
        assert "processing_maps" in stats["layer_1"]
        assert "acupoint_terms" in stats["layer_1"]
        assert "meridian_terms" in stats["layer_1"]
        assert "memory_total_kb" in stats["layer_1"]
        assert "loaded_components" in stats["layer_1"]

        # Check Layer 2 stats
        assert "cache_size" in stats["layer_2"]
        assert "max_size" in stats["layer_2"]
        assert "utilization_rate" in stats["layer_2"]
        assert "memory_kb" in stats["layer_2"]

        # Check overall stats
        assert "total_hit_count" in stats["overall"]
        assert "total_miss_count" in stats["overall"]
        assert "hit_rate" in stats["overall"]
        assert "total_memory_kb" in stats["overall"]
        assert "book_type" in stats["overall"]

    def test_memory_stats_after_lookups(self) -> None:
        """Test memory stats after cache lookups."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # Perform some lookups
        cache.resolve_herb_alias("川军")
        cache.resolve_herb_alias("炙甘草")
        cache.resolve_herb_alias("大黄")

        stats = cache.get_memory_stats()

        # Should have some hits
        assert stats["layer_1"]["hit_count"] > 0

    def test_memory_stats_layer0_populated(self) -> None:
        """Test that Layer 0 has critical terms loaded."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        stats = cache.get_memory_stats()

        # Layer 0 should have critical terms
        assert stats["layer_0"]["term_count"] > 0
        # Critical herbs + critical acupoints
        assert stats["layer_0"]["term_count"] == len(cache.CRITICAL_HERBS) + len(cache.CRITICAL_ACUPOINTS)

    def test_memory_stats_layer1_herb_loaded(self) -> None:
        """Test that Layer 1 has herb data loaded for formula book."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        stats = cache.get_memory_stats()

        # Should have herb aliases loaded from built-in data
        assert stats["layer_1"]["herb_aliases"] > 0
        assert stats["layer_1"]["herb_primaries"] > 0
        assert stats["layer_1"]["processing_maps"] > 0

    def test_utilization_rate(self) -> None:
        """Test LRU utilization rate calculation."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        # Set max size and add some entries
        cache._lru_maxsize = 100
        for i in range(25):
            cache._lru_set(f"key_{i}", {"value": i})

        stats = cache.get_memory_stats()
        expected_rate = round(25 / 100 * 100, 2)
        assert stats["layer_2"]["utilization_rate"] == expected_rate


# ============================================================================
# Tests for hit/miss counters
# ============================================================================

class TestHitMissCounters:
    """Test suite for cache hit/miss counters."""

    def test_hit_counter_l1_alias(self) -> None:
        """Test L1 hit counter increments on alias match."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        initial_hits = cache._hit_count_l1
        cache.resolve_herb_alias("川军")  # Should hit L1 alias map
        assert cache._hit_count_l1 == initial_hits + 1

    def test_hit_counter_l1_processing(self) -> None:
        """Test L1 hit counter increments on processing match."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        initial_hits = cache._hit_count_l1
        cache.resolve_herb_alias("炙甘草")  # Should hit L1 processing map
        assert cache._hit_count_l1 == initial_hits + 1

    def test_miss_counter(self) -> None:
        """Test miss counter increments on cache miss."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        initial_misses = cache._miss_count
        cache.resolve_herb_alias("完全不存在的药名")
        assert cache._miss_count == initial_misses + 1


# ============================================================================
# Tests for is_primary_herb
# ============================================================================

class TestIsPrimaryHerb:
    """Test suite for is_primary_herb method."""

    def test_is_primary_herb_true(self) -> None:
        """Test that primary herb names are recognized."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        assert cache.is_primary_herb("大黄") is True
        assert cache.is_primary_herb("甘草") is True
        assert cache.is_primary_herb("黄芪") is True

    def test_is_primary_herb_false(self) -> None:
        """Test that aliases are not primary names."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        assert cache.is_primary_herb("川军") is False  # alias
        assert cache.is_primary_herb("炙甘草") is False  # processing variant

    def test_is_primary_herb_empty(self) -> None:
        """Test that empty input returns False."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        assert cache.is_primary_herb("") is False


# ============================================================================
# Tests for match_terms_in_text
# ============================================================================

class TestMatchTermsInText:
    """Test suite for match_terms_in_text method."""

    def test_match_terms_basic(self) -> None:
        """Test matching terms in a text."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        text = "大黄10g，甘草6g，水煎服"
        results = cache.match_terms_in_text(text)

        # Should find 大黄 and 甘草
        terms_found = [r["term"] for r in results]
        assert "大黄" in terms_found
        assert "甘草" in terms_found

    def test_match_terms_empty_text(self) -> None:
        """Test matching terms in empty text returns empty list."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        assert cache.match_terms_in_text("") == []

    def test_match_terms_no_matches(self) -> None:
        """Test matching terms when no terms are found."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        results = cache.match_terms_in_text("这是一些完全不相关的文本")
        assert results == []


# ============================================================================
# Tests for get_term
# ============================================================================

class TestGetTerm:
    """Test suite for get_term method."""

    def test_get_term_critical(self) -> None:
        """Test getting a critical term."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        result = cache.get_term("砒霜")
        assert result is not None
        assert result["level"] == "critical"

    def test_get_term_not_found(self) -> None:
        """Test getting a non-existent term."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        result = cache.get_term("完全不存在的术语")
        assert result is None

    def test_get_term_empty(self) -> None:
        """Test getting term with empty input."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        assert cache.get_term("") is None


# ============================================================================
# Tests for clear operations
# ============================================================================

class TestClear:
    """Test suite for clear operations."""

    def test_clear(self) -> None:
        """Test clearing instance-level cache."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # Clear instance caches
        cache.clear()

        # Layer 1 should be cleared
        assert len(cache._layer1_loaded) == 0
        # Counters should be reset
        assert cache._hit_count_l0 == 0
        assert cache._hit_count_l1 == 0
        assert cache._hit_count_l2 == 0
        assert cache._miss_count == 0

    def test_clear_all_layers(self) -> None:
        """Test clearing all layers including global Layer 0."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        # Clear all layers
        cache.clear_all_layers()

        # Layer 0 should be cleared
        assert len(cache._CRITICAL_SET) == 0
        assert len(cache._CRITICAL_TERMS) == 0
        assert cache._LAYER0_LOADED is False

        # Layer 1 should be cleared
        assert len(cache._HERB_PRIMARY_SET) == 0
        assert len(cache._HERB_ALIAS_MAP) == 0
        assert len(cache._ACUPOINT_TERMS) == 0
        assert len(cache._MERIDIAN_TERMS) == 0


# ============================================================================
# Tests for BOOK_TYPE_LOAD_STRATEGY
# ============================================================================

class TestBookTypeLoadStrategy:
    """Test suite for BOOK_TYPE_LOAD_STRATEGY configuration."""

    def test_all_book_types_have_strategy(self) -> None:
        """Test that all supported book types have a load strategy."""
        expected_types = {"formula", "acupuncture", "internal_medicine", "tcm_monograph"}
        assert set(PatternCacheV2.BOOK_TYPE_LOAD_STRATEGY.keys()) == expected_types

    def test_formula_strategy(self) -> None:
        """Test formula book load strategy."""
        strategy = PatternCacheV2.BOOK_TYPE_LOAD_STRATEGY["formula"]
        assert strategy["layer1"] == ["herb"]
        assert strategy["acupoint_common_only"] is False
        assert strategy["load_meridian"] is False

    def test_acupuncture_strategy(self) -> None:
        """Test acupuncture book load strategy."""
        strategy = PatternCacheV2.BOOK_TYPE_LOAD_STRATEGY["acupuncture"]
        assert strategy["layer1"] == ["acupoint", "meridian"]
        assert strategy["acupoint_common_only"] is False
        assert strategy["load_meridian"] is True

    def test_internal_medicine_strategy(self) -> None:
        """Test internal medicine book load strategy."""
        strategy = PatternCacheV2.BOOK_TYPE_LOAD_STRATEGY["internal_medicine"]
        assert strategy["layer1"] == ["herb", "acupoint"]
        assert strategy["acupoint_common_only"] is True
        assert strategy["load_meridian"] is False

    def test_monograph_strategy(self) -> None:
        """Test monograph book load strategy."""
        strategy = PatternCacheV2.BOOK_TYPE_LOAD_STRATEGY["tcm_monograph"]
        assert strategy["layer1"] == ["herb", "acupoint", "meridian"]
        assert strategy["acupoint_common_only"] is False
        assert strategy["load_meridian"] is True


# ============================================================================
# Tests for thread safety
# ============================================================================

class TestThreadSafety:
    """Test suite for thread safety of PatternCacheV2."""

    def test_lock_initialization(self) -> None:
        """Test that locks are properly initialized."""
        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)

        assert isinstance(cache._lock, type(threading.Lock()))
        assert isinstance(cache._lru_lock, type(threading.Lock()))

    def test_concurrent_lru_access(self) -> None:
        """Test concurrent access to LRU cache."""
        import threading

        mock_db = _create_mock_db()
        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        results = []
        errors = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(100):
                    cache._lru_set(f"thread_{worker_id}_key_{i}", {"value": i})
                    val = cache._lru_get(f"thread_{worker_id}_key_{i}")
                    if val is not None:
                        results.append(val["value"])
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) > 0
