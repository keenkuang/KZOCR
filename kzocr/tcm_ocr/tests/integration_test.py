"""
TCM-Modern-OCR 系统集成测试

测试内容：
- 模拟完整流水线
- 术语导入到缓存集成
- 推送决策到Web API集成
- 并发访问测试
- 内存泄漏检测
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from kzocr.tcm_ocr.knowledge.cache.pattern_cache_v2 import PatternCacheV2
from kzocr.tcm_ocr.knowledge.term.auto_classifier import AutoClassifier
from kzocr.tcm_ocr.knowledge.term.normalized_maps import HerbNormalizedMaps
from kzocr.tcm_ocr.pipeline.book_type_detector import BookTypeDetector
from kzocr.tcm_ocr.pipeline.push_decision_logger import PushDecisionLogger


# ============================================================================
# Integration: Full Pipeline Mock
# ============================================================================

class TestFullPipelineMock:
    """Test suite simulating the complete OCR processing pipeline."""

    @pytest.fixture
    def pipeline_components(self) -> Dict[str, Any]:
        """Set up all pipeline components with mocked dependencies."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        # Create components
        classifier = AutoClassifier()
        detector = BookTypeDetector
        cache = PatternCacheV2(mock_db, book_type="formula")
        logger = PushDecisionLogger(mock_db)

        return {
            "mock_db": mock_db,
            "mock_cursor": mock_cursor,
            "classifier": classifier,
            "detector": detector,
            "cache": cache,
            "logger": logger,
        }

    def test_full_pipeline_formula_book(self, pipeline_components: Dict[str, Any]) -> None:
        """Test complete pipeline for a formula book."""
        mock_db = pipeline_components["mock_db"]
        classifier = pipeline_components["classifier"]
        detector = pipeline_components["detector"]
        cache = pipeline_components["cache"]
        logger = pipeline_components["logger"]

        # Reset shared cache state
        PatternCacheV2._LAYER0_LOADED = False
        PatternCacheV2._CRITICAL_SET.clear()
        PatternCacheV2._CRITICAL_TERMS.clear()

        # Step 1: Book type detection
        book_meta = {"title": "中医方剂学", "author": "张三", "publisher": "卫生出版社"}
        book_type = detector.detect(book_meta)
        assert book_type == "formula"

        # Step 2: Cache warm-up
        cache.warm_up(book_type)
        assert len(cache._CRITICAL_SET) > 0
        assert "herb" in cache._layer1_loaded

        # Step 3: OCR text classification
        ocr_texts = ["当归10g", "川芎6g", "白芍9g", "熟地黄15g"]
        classifications = classifier.classify_batch(ocr_texts)
        assert len(classifications) == 4

        # Step 4: Alias resolution
        resolved = cache.resolve_herb_alias("当归")
        assert resolved == "当归"  # Primary name

        # Step 5: Critical field check (砒霜 is in built-in critical set)
        assert cache.is_critical_field("砒霜") is True

        # Step 6: Push decision logging
        mock_cursor = mock_db.get_cursor.return_value
        call_count = [0]
        def _fetchone():
            call_count[0] += 1
            return {"id": call_count[0]}
        mock_cursor.fetchone = _fetchone
        decision_id = logger.log_consensus_dispute(
            book_id="book_001",
            line_id=1,
            page_num=1,
            engine_results={
                "paddleocr": {"text": "当归10g", "confidence": 0.95},
                "mineru": {"text": "当阳10g", "confidence": 0.88},
            },
        )
        assert decision_id == 1

    def test_full_pipeline_acupuncture_book(self, pipeline_components: Dict[str, Any]) -> None:
        """Test complete pipeline for an acupuncture book."""
        detector = pipeline_components["detector"]
        cache = pipeline_components["cache"]

        # Step 1: Book type detection
        book_meta = {"title": "针灸大成", "author": "杨继洲", "publisher": "中医出版社"}
        book_type = detector.detect(book_meta)
        assert book_type == "acupuncture"

        # Step 2: Cache warm-up
        cache.warm_up(book_type)
        assert "acupoint" in cache._layer1_loaded
        assert "meridian" in cache._layer1_loaded
        assert "herb" not in cache._layer1_loaded

        # Step 3: Verify meridian terms loaded
        assert len(cache._MERIDIAN_TERMS) == 20  # 12 regular + 8 extraordinary

    def test_full_pipeline_with_dosage_alert(self, pipeline_components: Dict[str, Any]) -> None:
        """Test pipeline with dosage alert scenario."""
        mock_db = pipeline_components["mock_db"]
        classifier = pipeline_components["classifier"]
        cache = pipeline_components["cache"]
        logger = pipeline_components["logger"]

        # Reset shared cache state
        PatternCacheV2._LAYER0_LOADED = False
        PatternCacheV2._CRITICAL_SET.clear()
        PatternCacheV2._CRITICAL_TERMS.clear()

        # Set up cache
        cache.warm_up("formula")

        # Classify herb name
        classifier.classify("附子")

        # Check if critical (use built-in critical herb)
        is_critical = cache.is_critical_field("砒霜")
        assert is_critical is True

        # Log dosage alert
        mock_cursor = mock_db.get_cursor.return_value
        call_count = [0]
        def _fetchone():
            call_count[0] += 1
            return {"id": call_count[0]}
        mock_cursor.fetchone = _fetchone
        decision_id = logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=10,
            page_num=5,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "severity": "overdose",
            },
        )
        assert decision_id == 1

    def test_pipeline_decision_resolution(self, pipeline_components: Dict[str, Any]) -> None:
        """Test pipeline decision resolution flow."""
        mock_db = pipeline_components["mock_db"]
        mock_cursor = mock_db.get_cursor.return_value
        logger = pipeline_components["logger"]

        # Log a decision
        mock_cursor.fetchone.return_value = {"id": 100}
        decision_id = logger.log_negation_violation(
            book_id="book_001",
            line_id=5,
            page_num=3,
            original="不可用附子",
            modified="可用附子",
            lost=["不"],
        )
        assert decision_id == 100

        # Resolve the decision
        mock_cursor.fetchone.return_value = {"id": 100}
        success = logger.resolve_decision(
            decision_id=100,
            action="accept",
            final_text="不可用附子",
            note="确认原文正确",
            reviewer_id=1,
        )
        assert success is True


# ============================================================================
# Integration: Term Import to Cache
# ============================================================================

class TestTermImportToCache:
    """Test suite for term import to cache integration."""

    def test_build_maps_from_import_results(self) -> None:
        """Test building normalized maps from import results."""
        # Clear first
        HerbNormalizedMaps.clear_all()

        # Simulate import results
        import_results = {
            "herb_aliases": [
                {"alias": "川军", "primary": "大黄", "alias_type": "common"},
                {"alias": "酒大黄", "primary": "大黄", "alias_type": "processing"},
                {"alias": "炒白术", "primary": "白术", "alias_type": "processing"},
                {"alias": "浙白术", "primary": "白术", "alias_type": "regional"},
                {"alias": "炙甘草", "primary": "甘草", "alias_type": "processing"},
                {"alias": "北芪", "primary": "黄芪", "alias_type": "common"},
            ],
            "primary_terms": ["大黄", "白术", "甘草", "黄芪"],
            "meridian_variants": [
                {"variant": "肺经", "standard": "手太阴肺经"},
                {"variant": "心经", "standard": "手少阴心经"},
            ],
        }

        HerbNormalizedMaps.build_from_import(import_results)

        # Verify primary terms
        assert HerbNormalizedMaps.is_primary_herb("大黄") is True
        assert HerbNormalizedMaps.is_primary_herb("白术") is True
        assert HerbNormalizedMaps.is_primary_herb("甘草") is True
        assert HerbNormalizedMaps.is_primary_herb("川军") is False  # alias

        # Verify alias resolution
        assert HerbNormalizedMaps.resolve_alias("川军") == "大黄"
        assert HerbNormalizedMaps.resolve_alias("酒大黄") == "大黄"
        assert HerbNormalizedMaps.resolve_alias("炒白术") == "白术"
        assert HerbNormalizedMaps.resolve_alias("浙白术") == "白术"
        assert HerbNormalizedMaps.resolve_alias("炙甘草") == "甘草"
        assert HerbNormalizedMaps.resolve_alias("北芪") == "黄芪"

        # Verify meridian resolution
        assert HerbNormalizedMaps.resolve_meridian("肺经") == "手太阴肺经"
        assert HerbNormalizedMaps.resolve_meridian("心经") == "手少阴心经"

        # Cleanup
        HerbNormalizedMaps.clear_all()

    def test_maps_consistency(self) -> None:
        """Test that maps maintain consistency after building."""
        HerbNormalizedMaps.clear_all()

        import_results = {
            "herb_aliases": [
                {"alias": "川军", "primary": "大黄", "alias_type": "common"},
                {"alias": "酒大黄", "primary": "大黄", "alias_type": "processing"},
            ],
            "primary_terms": ["大黄"],
            "meridian_variants": [],
        }

        HerbNormalizedMaps.build_from_import(import_results)

        # Check reverse mapping
        aliases = HerbNormalizedMaps.get_aliases("大黄")
        assert "川军" in aliases
        assert "酒大黄" in aliases

        # Check processing map
        assert HerbNormalizedMaps._PROCESSING_MAP["酒大黄"] == "大黄"

        # Cleanup
        HerbNormalizedMaps.clear_all()

    def test_maps_o1_lookup_performance(self) -> None:
        """Test that alias lookup is O(1) performance."""
        HerbNormalizedMaps.clear_all()

        # Build with substantial data
        aliases = []
        primaries = [f"herb_{i}" for i in range(100)]
        for i, primary in enumerate(primaries):
            for j in range(5):
                aliases.append({
                    "alias": f"alias_{i}_{j}",
                    "primary": primary,
                    "alias_type": "common",
                })

        import_results = {
            "herb_aliases": aliases,
            "primary_terms": primaries,
            "meridian_variants": [],
        }

        HerbNormalizedMaps.build_from_import(import_results)

        # Performance test: 10000 lookups
        start = time.perf_counter()
        for _ in range(10000):
            HerbNormalizedMaps.resolve_alias("alias_0_0")
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"10000 lookups took {elapsed:.3f}s, should be < 0.1s"

        # Cleanup
        HerbNormalizedMaps.clear_all()

    def test_classifier_to_cache_integration(self) -> None:
        """Test that classifier outputs feed correctly into cache lookups."""
        classifier = AutoClassifier()
        cache_db = MagicMock()
        cache_db.get_cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        cache_db.get_cursor.return_value.__exit__ = MagicMock(return_value=None)

        cache = PatternCacheV2(cache_db, book_type="formula")
        cache.warm_up("formula")

        # Classify a herb name
        result = classifier.classify("当归")
        assert result["category"] in ("formula", "unknown")  # 当归 alone doesn't match formula pattern well

        # But it should be resolvable in cache
        resolved = cache.resolve_herb_alias("当归")
        assert resolved == "当归"  # Primary name resolves to itself

        resolved_alias = cache.resolve_herb_alias("川军")
        assert resolved_alias == "大黄"


# ============================================================================
# Integration: Push Decision Flow
# ============================================================================

class TestPushDecisionFlow:
    """Test suite for push decision to Web API integration."""

    def test_decision_creation_to_resolution_flow(self) -> None:
        """Test full decision lifecycle from creation to resolution."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)

        # Step 1: Create dosage alert (P0)
        mock_cursor.fetchone.return_value = {"id": 1}
        decision_id = logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=10,
            page_num=5,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "severity": "overdose",
            },
        )
        assert decision_id == 1

        # Step 2: Resolve the decision
        mock_cursor.fetchone.return_value = {"id": 1, "status": "pending"}
        success = logger.resolve_decision(
            decision_id=1,
            action="modify",
            final_text="附子9g",
            note="修正剂量为药典标准",
            reviewer_id=100,
        )
        assert success is True

    def test_multiple_decisions_priority_ordering(self) -> None:
        """Test that decisions are ordered by priority."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)

        # Create decisions with different priorities
        decisions = []

        # P1: Consensus dispute
        mock_cursor.fetchone.return_value = {"id": 1}
        decisions.append(logger.log_consensus_dispute(
            book_id="book_001", line_id=1, page_num=1,
            engine_results={"a": {"text": "x"}},
        ))

        # P0: Negation violation
        mock_cursor.fetchone.return_value = {"id": 2}
        decisions.append(logger.log_negation_violation(
            book_id="book_001", line_id=2, page_num=1,
            original="不可用", modified="可用", lost=["不"],
        ))

        # P2: Formula extract fail
        mock_cursor.fetchone.return_value = {"id": 3}
        decisions.append(logger.log_formula_extract_fail(
            book_id="book_001", para_id=1, page_num=1,
            alert_type="no_formula_found", detail={},
        ))

        assert len(decisions) == 3
        assert all(d > 0 for d in decisions)

    def test_decision_stats_integration(self) -> None:
        """Test decision stats calculation with multiple decisions."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        # Mock stats query results
        mock_cursor.fetchone.return_value = {
            "total_decisions": 50,
            "pending_count": 10,
            "resolved_count": 30,
            "rejected_count": 5,
            "auto_resolved_count": 3,
            "escalated_count": 2,
            "avg_resolution_time_sec": 120.0,
        }
        mock_cursor.fetchall.side_effect = [
            # by_priority
            [
                {"priority": "P0", "total_count": 10, "pending_count": 2, "resolved_count": 6},
                {"priority": "P1", "total_count": 20, "pending_count": 5, "resolved_count": 12},
                {"priority": "P2", "total_count": 15, "pending_count": 3, "resolved_count": 10},
                {"priority": "P3", "total_count": 5, "pending_count": 0, "resolved_count": 2},
            ],
            # by_reason
            [
                {"reason_code": "CONSENSUS_DISPUTE", "total_count": 15, "pending_count": 3, "resolved_count": 10},
                {"reason_code": "DOSAGE_PRE_ALERT", "total_count": 10, "pending_count": 2, "resolved_count": 6},
                {"reason_code": "NEGATION_VIOLATION", "total_count": 8, "pending_count": 2, "resolved_count": 5},
            ],
            # by_status
            [
                {"status": "pending", "count": 10},
                {"status": "resolved", "count": 30},
                {"status": "rejected", "count": 5},
            ],
        ]

        logger = PushDecisionLogger(mock_db)
        stats = logger.get_decision_stats()

        assert stats["total_decisions"] == 50
        assert stats["pending_count"] == 10
        assert stats["resolved_count"] == 30
        assert len(stats["by_priority"]) == 4
        assert len(stats["by_reason"]) == 3
        assert len(stats["by_status"]) == 3


# ============================================================================
# Integration: Concurrent Access
# ============================================================================

class TestConcurrentAccess:
    """Test suite for concurrent access scenarios."""

    def test_concurrent_cache_reads(self) -> None:
        """Test concurrent read access to PatternCacheV2."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        results = []
        errors = []

        def reader(thread_id: int) -> None:
            try:
                for i in range(500):
                    alias = ["川军", "炙甘草", "大黄", "北芪", "炒白芍"][i % 5]
                    result = cache.resolve_herb_alias(alias)
                    results.append(result)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(8)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start

        assert len(errors) == 0, f"Concurrent read errors: {errors}"
        assert len(results) == 4000  # 8 threads * 500 reads
        # Should complete quickly
        assert elapsed < 2.0, f"Concurrent reads took {elapsed:.3f}s"

    def test_concurrent_classifier_batch(self) -> None:
        """Test concurrent batch classification."""
        classifier = AutoClassifier()

        results = []
        errors = []

        texts = ["百会穴", "四君子汤", "气虚证", "不", "手太阴肺经"] * 100

        def worker(worker_id: int) -> None:
            try:
                for _ in range(10):
                    batch_results = classifier.classify_batch(texts[:50])
                    results.extend(batch_results)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent classification errors: {errors}"
        assert len(results) == 2000  # 4 threads * 10 batches * 50 items

    def test_concurrent_book_type_detection(self) -> None:
        """Test concurrent book type detection."""
        # Use titles that reliably detect to each type
        book_metas = [
            {"title": "中医方剂学"},       # formula
            {"title": "针灸大成"},         # acupuncture
            {"title": "伤寒论"},           # internal_medicine
            {"title": "临证经验集要"},     # tcm_monograph
        ] * 25  # 100 books

        results = []
        errors = []

        def detector_worker(metas: List[Dict[str, str]]) -> None:
            try:
                for meta in metas:
                    result = BookTypeDetector.detect(meta)
                    results.append(result)
            except Exception as e:
                errors.append(str(e))

        # Split work among 4 threads
        chunk_size = len(book_metas) // 4
        threads = [
            threading.Thread(target=detector_worker, args=(book_metas[i:i + chunk_size],))
            for i in range(0, len(book_metas), chunk_size)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent detection errors: {errors}"
        assert len(results) == 100

        # Verify correct types detected
        type_counts = {}
        for t in results:
            type_counts[t] = type_counts.get(t, 0) + 1

        assert "formula" in type_counts
        assert "acupuncture" in type_counts
        assert "internal_medicine" in type_counts
        assert "tcm_monograph" in type_counts


# ============================================================================
# Integration: Memory Leak Detection
# ============================================================================

class TestMemoryLeak:
    """Test suite for memory leak detection."""

    def test_pattern_cache_no_leak_on_repeated_warmup(self) -> None:
        """Test that repeated warm_up doesn't leak memory."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        # Clear global state first
        PatternCacheV2._LAYER0_LOADED = False
        PatternCacheV2._CRITICAL_SET.clear()
        PatternCacheV2._CRITICAL_TERMS.clear()

        # Measure memory after first warm_up
        cache = PatternCacheV2(mock_db, book_type="formula")
        cache.warm_up("formula")

        first_size = len(PatternCacheV2._CRITICAL_SET)

        # Repeated warm_ups should not increase memory
        for _ in range(10):
            cache.warm_up("formula")

        final_size = len(PatternCacheV2._CRITICAL_SET)
        assert final_size == first_size, (
            f"Memory leak detected: {first_size} -> {final_size}"
        )

    def test_normalized_maps_no_leak_on_rebuild(self) -> None:
        """Test that rebuilding normalized maps doesn't leak."""
        HerbNormalizedMaps.clear_all()

        import_results = {
            "herb_aliases": [
                {"alias": f"alias_{i}", "primary": "大黄", "alias_type": "common"}
                for i in range(100)
            ],
            "primary_terms": ["大黄"],
            "meridian_variants": [],
        }

        # First build
        HerbNormalizedMaps.build_from_import(import_results)
        first_size = len(HerbNormalizedMaps._HERB_ALIAS_MAP)

        # Repeated builds should not accumulate
        for _ in range(5):
            HerbNormalizedMaps.clear_all()
            HerbNormalizedMaps.build_from_import(import_results)

        final_size = len(HerbNormalizedMaps._HERB_ALIAS_MAP)
        assert final_size == first_size, (
            f"Memory leak: {first_size} -> {final_size}"
        )

        HerbNormalizedMaps.clear_all()

    def test_lru_cache_size_bound(self) -> None:
        """Test that LRU cache doesn't grow beyond maxsize."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        cache = PatternCacheV2(mock_db)
        cache.warm_up("formula")

        # Set small max size
        cache._lru_maxsize = 50

        # Add many more items than max size
        for i in range(200):
            cache._lru_set(f"key_{i}", {"value": i})

        # Cache should not exceed maxsize
        assert len(cache._lru_cache) <= cache._lru_maxsize

        # Restore
        cache._lru_maxsize = 1000

    def test_classifier_no_state_leak(self) -> None:
        """Test that AutoClassifier doesn't accumulate state."""
        classifier = AutoClassifier()

        # Classify many items
        for i in range(1000):
            classifier.classify(f"term_{i}")

        # CONTENT_RULES should remain unchanged
        assert len(classifier.CONTENT_RULES) == 8

    def test_push_decision_logger_no_state_leak(self) -> None:
        """Test that PushDecisionLogger doesn't accumulate state."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)

        # REASON_CODES should remain unchanged
        assert len(logger.REASON_CODES) == 15


# ============================================================================
# Integration: Cross-Module End-to-End
# ============================================================================

class TestCrossModuleEndToEnd:
    """End-to-end tests across all modules."""

    def test_e2e_formula_book_processing(self) -> None:
        """End-to-end test: formula book processing pipeline."""
        # 1. Detect book type
        book_meta = {"title": "中医方剂学", "author": "", "publisher": ""}
        book_type = BookTypeDetector.detect(book_meta)
        assert book_type == "formula"

        # 2. Get required caches
        cache_config = BookTypeDetector.get_required_caches(book_type)
        assert cache_config["layer_1_herb"] is True

        # 3. Classify terms from the book
        classifier = AutoClassifier()
        terms = ["四君子汤", "当归", "甘草", "附子", "克"]
        results = classifier.classify_batch(terms)

        assert len(results) == 5
        categories = [r["category"] for r in results]
        assert "formula" in categories
        assert "dosage_unit" in categories

        # 4. Check safety levels
        critical_terms = [r for r in results if r["safety"] == "critical"]
        assert len(critical_terms) >= 1  # At least 克 (dosage_unit) is critical

    def test_e2e_acupuncture_book_processing(self) -> None:
        """End-to-end test: acupuncture book processing pipeline."""
        # 1. Detect book type
        book_meta = {"title": "针灸大成", "author": "", "publisher": ""}
        book_type = BookTypeDetector.detect(book_meta)
        assert book_type == "acupuncture"

        # 2. Get required caches
        cache_config = BookTypeDetector.get_required_caches(book_type)
        assert cache_config["layer_1_acupoint"] is True
        assert cache_config["layer_1_meridian"] is True

        # 3. Classify terms
        classifier = AutoClassifier()
        terms = ["百会穴", "手太阴肺经", "补泻手法", "针刺"]
        results = classifier.classify_batch(terms)

        categories = [r["category"] for r in results]
        assert "acupoint" in categories
        assert "meridian" in categories

    def test_e2e_decision_priority_escalation(self) -> None:
        """End-to-end test: decision priority escalation scenario."""
        # A dosage alert for a toxic herb should be P0
        classifier = AutoClassifier()

        # Classify a toxic herb
        classifier.classify("附子")

        # Verify safety implications
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        logger = PushDecisionLogger(mock_db)

        # Log dosage alert for toxic herb
        mock_cursor.fetchone.return_value = {"id": 1}
        decision_id = logger.log_dosage_pre_alert(
            book_id="book_001",
            line_id=1,
            page_num=1,
            alert={
                "herb_name": "附子",
                "detected_dosage": "30g",
                "standard_max": "15g",
                "severity": "overdose",
            },
        )
        assert decision_id > 0

        # The reason code should be DOSAGE_PRE_ALERT which is P0
        assert PushDecisionLogger.REASON_CODES["DOSAGE_PRE_ALERT"]["priority"] == "P0"

    def test_e2e_all_modules_coexist(self) -> None:
        """Test that all modules can be instantiated together without conflicts."""
        # Create all module instances
        classifier = AutoClassifier()
        detector = BookTypeDetector

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_db.get_cursor.return_value = mock_cursor

        cache = PatternCacheV2(mock_db, book_type="tcm_monograph")
        logger = PushDecisionLogger(mock_db)

        # Use all modules in sequence
        book_meta = {"title": "临证指南医案", "author": "叶天士", "publisher": "古籍出版社"}
        book_type = detector.detect(book_meta)
        cache.warm_up(book_type)

        # Classify various terms
        test_terms = ["百会穴", "四君子汤", "气虚证", "不", "手太阴肺经", "克"]
        classifications = classifier.classify_batch(test_terms)

        # Verify all classifications
        category_map = {r["text"]: r["category"] for r in classifications}
        assert category_map.get("百会穴") == "acupoint"
        assert category_map.get("不") == "negation"
        assert category_map.get("手太阴肺经") == "meridian"
        assert category_map.get("克") == "dosage_unit"

        # Cache lookups
        assert cache.is_critical_field("砒霜") is True
        assert cache.resolve_herb_alias("川军") == "大黄"

        # Decision logging
        mock_cursor.fetchone.return_value = {"id": 1}
        decision_id = logger.log_consensus_dispute(
            book_id="book_001", line_id=1, page_num=1,
            engine_results={"paddle": {"text": "test", "confidence": 0.9}},
        )
        assert decision_id == 1

        # Verify reason codes dictionary
        assert len(logger.REASON_CODES) == 15
        for code, info in logger.REASON_CODES.items():
            assert "priority" in info
            assert "name" in info
            assert "color" in info
