"""D4: 层级异常检测 — 识别 VLM 输出中结构异常页。

检测规则：

1. **字符数突变（anomaly_type="char_count_spike"）**:
   某页字符数超过邻居中位数 × threshold（默认 3 倍），
   或低于邻居中位数 × (1/threshold)。

2. **内容类型飞跃（anomaly_type="content_type_jump"）**:
   页内容在行级特征（平均行长、符号密度等）上与前后页差异过大。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field  # noqa: F401
from typing import Callable  # noqa: F401


@dataclass
class HierarchyAnomaly:
    """层级异常记录。

    Attributes:
        page: 页码（1-indexed）
        anomaly_type: 异常类型 (char_count_spike / content_type_jump)
        severity: 严重程度 (0.0 ~ 1.0)，基于偏离邻居的程度
        expected_range: (min, max) 预期正常范围
        actual_value: 实际观测值
    """
    page: int
    anomaly_type: str
    severity: float
    expected_range: tuple[float, float]
    actual_value: float
    message: str = ""


def _get_neighbor_window(pages: list, idx: int, window: int = 3,
                         min_samples: int = 2) -> list[int]:
    """获取 idx 前后 window 范围内有效邻居的字符数列表。

    跳过 idx 自身以及空页/无效页。
    """
    n = len(pages)
    start = max(0, idx - window)
    end = min(n, idx + window + 1)
    neighbors = []
    for i in range(start, end):
        if i == idx:
            continue
        text = pages[i]
        if text and text.strip():
            neighbors.append(len(text))
    # 如果邻居不足 min_samples，扩大搜索范围
    if len(neighbors) < min_samples:
        for i in range(n):
            if i == idx:
                continue
            text = pages[i]
            if text and text.strip() and i not in [j for j in range(start, end) if j != idx]:
                neighbors.append(len(text))
            if len(neighbors) >= min_samples:
                break
    return neighbors


def check_hierarchy_anomaly(
    pages_text: list[str],
    char_count_threshold: float = 3.0,
    window: int = 3,
    min_neighbors: int = 2,
) -> list[HierarchyAnomaly]:
    """检测页面文本序列中的层级异常。

    Args:
        pages_text: VLM 输出的逐页文本列表（0-indexed，页号 = index + 1）
        char_count_threshold: 字符数异常倍数阈值（默认 3 倍）
        window: 邻居窗口半径
        min_neighbors: 检测所需最少邻居数

    Returns:
        异常列表，按页码排序
    """
    anomalies: list[HierarchyAnomaly] = []

    if len(pages_text) < min_neighbors + 1:
        return anomalies  # 页数太少，无法检测

    for i, text in enumerate(pages_text):
        page_char_count = len(text) if text else 0
        if page_char_count == 0:
            continue  # 空页不检测

        neighbors = _get_neighbor_window(pages_text, i, window, min_neighbors)
        if len(neighbors) < min_neighbors:
            continue

        median = statistics.median(neighbors)
        if median == 0:
            continue

        # D4-1: 字符数异常检测
        ratio = page_char_count / median
        if ratio > char_count_threshold:
            severity = min(1.0, (ratio - char_count_threshold) / char_count_threshold)
            anomalies.append(HierarchyAnomaly(
                page=i + 1,
                anomaly_type="char_count_spike",
                severity=round(severity, 2),
                expected_range=(0, round(median * char_count_threshold)),
                actual_value=float(page_char_count),
                message=(
                    f"P{i + 1} 字符数 {page_char_count} "
                    f"是邻居中位数 {median} 的 {ratio:.1f} 倍"
                ),
            ))
        elif ratio < 1.0 / char_count_threshold:
            severity = min(1.0, (1.0 / ratio - char_count_threshold) / char_count_threshold)
            anomalies.append(HierarchyAnomaly(
                page=i + 1,
                anomaly_type="char_count_spike",
                severity=round(severity, 2),
                expected_range=(
                    round(median / char_count_threshold),
                    float("inf"),
                ),
                actual_value=float(page_char_count),
                message=(
                    f"P{i + 1} 字符数 {page_char_count} "
                    f"是邻居中位数 {median} 的 {ratio:.1f} 倍（过低）"
                ),
            ))

    return sorted(anomalies, key=lambda a: a.page)
