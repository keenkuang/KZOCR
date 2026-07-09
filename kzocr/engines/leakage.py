"""C1: VLM 跨页内容泄漏 4 层防御系统。

继承 TOC 项目在 970 页真实中医书上的验证结果：
- 泄漏模式从页面 ~50% 位置开始（非仅页末）
- 原 max_tokens=4096 过于宽松 → 下调到 2048
- 动态字符基线 + 增量探针重叠检测实现零漏报+零误报

使用顺序：
1. 初始化 CharCountBaseline(window=50)
2. 逐页 feed page_text → 自动计算基线
3. 在跨页合并前调用 apply_leakage_defense() 应用 4 层防御
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 探针排除词：短字段名/常见词，避免误报
_EXCLUDE_WORDS = {"组成", "用法", "功用", "方解", "主治", "加减", "疗效", "附记", "来源", "秘方"}


def _normalize(text: str) -> str:
    """标准化文本用于重叠检测：去空白、全角数字→半角。"""
    text = text.strip()
    # 全角数字→半角
    text = text.replace("０", "0").replace("１", "1").replace("２", "2").replace("３", "3")
    text = text.replace("４", "4").replace("５", "5").replace("６", "6").replace("７", "7")
    text = text.replace("８", "8").replace("９", "9")
    # 去空白字符
    text = re.sub(r"\s+", "", text)
    return text


class CharCountBaseline:
    """L1: 动态字符数基线。

    取前 `window` 页的中位数作为 baseline，乘以 1.5 作为阈值。
    """

    def __init__(self, window: int = 50):
        self.window = window
        self.baseline: Optional[float] = None
        self._counts: list[int] = []

    def feed(self, page_text: str) -> None:
        """输入一页文本，更新基线统计。只在达到 window 数后计算基线。"""
        if self.baseline is not None:
            return  # 基线已锁定，不再更新
        self._counts.append(len(page_text))
        if len(self._counts) >= self.window:
            sorted_counts = sorted(self._counts)
            self.baseline = float(sorted_counts[len(sorted_counts) // 2])
            logger.info(
                "[leakage] 基线已建立: baseline=%.0f, threshold=%.0f (窗口=%d)",
                self.baseline, self.threshold, self.window,
            )

    @property
    def threshold(self) -> Optional[float]:
        """返回 baseline * 1.5 或 None（基线未就绪）。"""
        if self.baseline is None:
            return None
        return self.baseline * 1.5

    @property
    def ready(self) -> bool:
        """基线是否已就绪（收集了足够页数）。"""
        return self.baseline is not None


class LeakageDetector:
    """L4: 增量探针重叠检测。

    检测 page_b 的文本是否出现在 page_a 中（表明模型输出了下一页内容）。
    """

    @staticmethod
    def _is_excluded(probe: str) -> bool:
        """探针是否由排除词组成（防误报）。"""
        for word in _EXCLUDE_WORDS:
            if word in probe and len(probe) < len(word) * 3:
                return True
        return False

    @classmethod
    def detect(
        cls,
        text_a: str,
        text_b: str,
        min_probe: int = 50,
        max_probe: int = 300,
        step: int = 10,
        min_overlap_pct: float = 0.30,
    ) -> Optional[int]:
        """检测 page_b 内容是否泄漏到 page_a。

        Args:
            text_a: 当前页文本（可能含泄漏内容）。
            text_b: 下页文本（泄漏源）。
            min_probe: 最小探针长度（字符）。
            max_probe: 最大探针长度。
            step: 探针长度步进。
            min_overlap_pct: 重叠位置阈值（0.0-1.0）。
                仅当重叠起始位置 > 此比例时才判为泄漏。

        Returns:
            text_a 中泄漏起始位置，或 None。
        """
        if not text_a or not text_b or len(text_b) < min_probe:
            return None

        norm_a = _normalize(text_a)
        norm_b = _normalize(text_b)
        if not norm_a or not norm_b:
            return None

        n_a, n_b = len(norm_a), len(norm_b)
        min_plen = min(min_probe, n_b)
        max_plen = min(max_probe, n_b)

        for plen in range(min_plen, max_plen + 1, step):
            probe = norm_b[:plen]
            if cls._is_excluded(probe):
                continue
            pos = norm_a.find(probe)
            if pos >= 0:
                leak_start_pct = pos / n_a
                if leak_start_pct > min_overlap_pct:
                    logger.info(
                        "[leakage] L4 检出泄漏: 探针=%d, 位置=%.1f%%, 截断@%d",
                        plen, leak_start_pct * 100, pos,
                    )
                    return pos
        return None

    @staticmethod
    def trim(text_a: str, leak_start: int) -> str:
        """在泄漏起始位置截断 text_a。"""
        if leak_start <= 0 or leak_start >= len(text_a):
            return text_a
        # 在截断位置向前找完整的句子边界
        cutoff = max(leak_start, 0)
        return text_a[:cutoff].rstrip()


def apply_leakage_defense(
    pages_text: list[str],
    baseline: CharCountBaseline,
    max_tokens: int = 2048,
) -> list[str]:
    """应用完整 4 层泄漏防御。

    流程:
    L1: 动态基线检测（char_count > threshold）
    L2: max_tokens 物理上限
    L3: 超阈自动重 OCR（此处仅日志记录，实际重 OCR 由调用侧负责）
    L4: 相邻页重叠探测 + 截断

    Args:
        pages_text: 逐页文本列表。
        baseline: 已建立的字符数基线。
        max_tokens: L2 物理上限（默认 2048）。

    Returns:
        防御处理后的文本列表。
    """
    if not pages_text:
        return pages_text

    result = list(pages_text)

    # L1/L2: 逐页检查字符数
    threshold = baseline.threshold if baseline.ready else None
    for i in range(len(result)):
        char_count = len(result[i])
        # L1: 检查是否超过基线阈值
        if threshold is not None and char_count > threshold:
            logger.warning(
                "[leakage] L1 触发: P%d 字符数 %d > 阈值 %.0f",
                i + 1, char_count, threshold,
            )
        # L2: max_tokens 物理上限（约等于字符数上限）
        if char_count > max_tokens * 2:  # 保守估计: 1 token ≈ 2 中文字
            logger.warning(
                "[leakage] L2 触发: P%d 字符数 %d > max_tokens*2=%d",
                i + 1, char_count, max_tokens * 2,
            )
            # L3: 标记需要重 OCR（日志提示）
            logger.info("[leakage] L3: P%d 建议重 OCR（max_tokens=%.0f）", i + 1, char_count * 0.5)

    # L4: 相邻页重叠探测（从后往前避免索引偏移）
    for i in range(len(result) - 2, -1, -1):
        cur = result[i]
        nxt = result[i + 1]
        if not cur or not nxt:
            continue
        leak_start = LeakageDetector.detect(cur, nxt)
        if leak_start is not None:
            result[i] = LeakageDetector.trim(cur, leak_start)

    return result
