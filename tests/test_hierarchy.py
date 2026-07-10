"""D4: 层级异常检测测试。"""
from __future__ import annotations


from kzocr.engines.hierarchy import (
    HierarchyAnomaly,
    _get_neighbor_window,
    check_hierarchy_anomaly,
)


class TestNeighborWindow:
    def test_basic_neighbors(self):
        pages = ["a" * 10, "b" * 20, "c" * 30, "d" * 40, "e" * 50]
        neighbors = _get_neighbor_window(pages, 2, window=1, min_samples=2)
        assert len(neighbors) >= 2

    def test_edge_case_first_page(self):
        pages = ["a" * 10, "b" * 20, "c" * 30]
        neighbors = _get_neighbor_window(pages, 0, window=1, min_samples=2)
        assert len(neighbors) >= 2

    def test_edge_case_last_page(self):
        pages = ["a" * 10, "b" * 20, "c" * 30]
        neighbors = _get_neighbor_window(pages, 2, window=1, min_samples=2)
        assert len(neighbors) >= 2

    def test_skips_empty_pages(self):
        pages = ["a" * 10, "", "", "c" * 30, "d" * 40]
        neighbors = _get_neighbor_window(pages, 3, window=1, min_samples=2)
        assert len(neighbors) >= 2


class TestCheckHierarchyAnomaly:
    def test_no_anomaly_normal_pages(self):
        pages = ["正常页面内容。" * 20] * 10  # ~140 chars each
        result = check_hierarchy_anomaly(pages)
        assert len(result) == 0

    def test_detects_oversize_page(self):
        pages = ["正常页面。" * 20] * 5
        pages[2] = "超长内容。" * 500  # ~2500 chars, much larger than ~140
        result = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(result) == 1
        assert result[0].page == 3
        assert result[0].anomaly_type == "char_count_spike"

    def test_detects_undersize_page(self):
        pages = ["正常页面。" * 20] * 5
        pages[3] = "短"
        result = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(result) == 1
        assert result[0].page == 4
        assert result[0].actual_value == 1

    def test_empty_input(self):
        assert check_hierarchy_anomaly([]) == []

    def test_single_page(self):
        assert check_hierarchy_anomaly(["一些内容"]) == []

    def test_few_pages_below_min_neighbors(self):
        assert check_hierarchy_anomaly(["a", "b"], min_neighbors=3) == []

    def test_multiple_anomalies_sorted(self):
        pages = ["正常。" * 20] * 6
        pages[1] = "超大。" * 300
        pages[4] = "超大。" * 300
        result = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(result) == 2
        assert result[0].page == 2
        assert result[1].page == 5

    def test_severity_scaling(self):
        pages = ["正常。" * 20] * 7
        pages[3] = "超大。" * 1000  # massive spike, only 1 of 7 pages
        result = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(result) == 1
        assert result[0].severity > 0.5  # high severity for extreme deviation

    def test_anomaly_dataclass_fields(self):
        a = HierarchyAnomaly(
            page=1,
            anomaly_type="char_count_spike",
            severity=0.5,
            expected_range=(0, 100),
            actual_value=300.0,
            message="test",
        )
        assert a.page == 1
        assert a.anomaly_type == "char_count_spike"
        assert a.severity == 0.5

    def test_neighbor_expansion(self):
        """When within-window neighbors are insufficient, should expand search."""
        pages = ["a" * 10, "", "", "", "b" * 20, "", "", "", "c" * 30]
        result = check_hierarchy_anomaly(pages, min_neighbors=2)
        assert isinstance(result, list)


class TestIntegrationWithPagesText:
    def test_zero_length_page_skipped(self):
        """空页不触发异常检测（避免除零）。"""
        pages = ["", "正常页。" * 30, "正常页。" * 30, "正常页。" * 30]
        result = check_hierarchy_anomaly(pages)
        assert len(result) == 0

    def test_threshold_tolerance(self):
        """刚好在阈值内的页不算异常。"""
        pages = ["正常。" * 20] * 6
        median = len(pages[0])
        pages[3] = "正" * int(median * 2.9)  # 低于 3 倍阈值
        result = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(result) == 0

    def test_custom_threshold(self):
        """自定义阈值。"""
        pages = ["正常。" * 20] * 4
        pages[1] = "正" * int(len(pages[0]) * 2)  # 2x
        # threshold=1.5 应检出，threshold=3.0 不应
        strict = check_hierarchy_anomaly(pages, char_count_threshold=1.5)
        loose = check_hierarchy_anomaly(pages, char_count_threshold=3.0)
        assert len(strict) == 1
        assert len(loose) == 0
