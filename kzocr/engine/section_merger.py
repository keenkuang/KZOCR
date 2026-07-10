"""章节合并器：按 TOC 树合并逐页文本为章节，含三级编号校验。

功能：
1. merge_by_toc(book) — 返回 list[ChapterResult]
2. validate_recipe_numbers(text) — 三级计数器校验（防 traedocu 33% 事故）
3. to_markdown(chapters) — 多级 Markdown 导出
"""

from __future__ import annotations

import logging
import re

from kzocr.engine.types import BookResult, ChapterResult, TocEntry

_logger = logging.getLogger(__name__)

RECIPE_NO_RE = re.compile(r"(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)")


def merge_by_toc(book: BookResult) -> list[ChapterResult]:
    """按 BookResult.toc 将 pages 合并为章节列表。

    无 TOC 时返回单章（书名作为章标题）。
    """
    if not book.pages:
        return []
    if not book.toc or not book.toc.entries:
        return [ChapterResult(
            title=book.title or book.book_code,
            level=1,
            page_start=0,
            page_end=len(book.pages) - 1,
            text="\n".join(p.text for p in book.pages if p.text),
        )]

    pages = book.pages
    chapters: list[ChapterResult] = []

    def _build_chapter(entry: TocEntry, start_idx: int) -> tuple[ChapterResult, int]:
        """递归构建章节。

        Args:
            entry: TocEntry 节点。
            start_idx: 当前条目在 pages 中的起始索引。

        Returns:
            (ChapterResult, next_start_idx)
        """
        # 找到该章的结束页：下一条同级的 page_start - 1，或书尾
        end_idx = len(pages) - 1
        # 给每个子条目分配页范围
        sub_results: list[ChapterResult] = []
        next_start = start_idx
        for sub in entry.sub_entries:
            sub_ch, next_start = _build_chapter(sub, next_start)
            sub_results.append(sub_ch)
        if sub_results:
            end_idx = max(ch.page_end for ch in sub_results)

        # 收集该章文本
        page_texts: list[str] = []
        for i in range(start_idx, min(end_idx + 1, len(pages))):
            page_texts.append(pages[i].text or "")
        combined = "\n".join(page_texts)

        # 方剂计数
        recipes = RECIPE_NO_RE.findall(combined)

        # 编号校验
        anomalies = _validate_numbers(combined, entry.level)

        ch = ChapterResult(
            title=entry.title,
            level=entry.level,
            page_start=start_idx,
            page_end=end_idx,
            text=combined,
            recipe_count=len(set(recipes)),
            sub_chapters=sub_results,
            anomalies=anomalies,
        )
        return ch, end_idx + 1

    next_start = 0
    for entry in book.toc.entries:
        # 用 TOC 的 page 字段找到 pages 中的索引
        if entry.page > 0 and entry.page <= len(pages):
            start = entry.page - 1  # 0-indexed
        else:
            # 从上一章的结束处或书首开始
            start = next_start
        ch, next_start = _build_chapter(entry, start)
        chapters.append(ch)

    return chapters


def _validate_numbers(text: str, level: int) -> list[str]:
    """三级编号校验：检查章节/方剂编号是否连续递增。

    traedocu OCR-BUG-001：章/节号误判致识别率仅 33%。
    本函数扫描文本命中 RECIPE_NO_RE 的编号，检查连续性。

    Returns:
        偏差记录列表，空 = 无异常。
    """
    matches = RECIPE_NO_RE.findall(text)
    if len(matches) < 2:
        return []
    anomalies: list[str] = []
    expected_first = 1
    expected_second = 1
    for m in matches:
        parts = m.split(".")
        if len(parts) >= 2:
            try:
                main_no = int(parts[0])
                sub_no = int(parts[1])
                if main_no != expected_first:
                    anomalies.append(
                        f"节号偏差: 期望{expected_first}, 实际{main_no} (编号{m})"
                    )
                    expected_first = main_no
                if sub_no != expected_second and main_no == expected_first:
                    anomalies.append(
                        f"方序号偏差: 期望{expected_first}.{expected_second}, 实际{m}"
                    )
                    expected_second = sub_no + 1
                else:
                    expected_second = sub_no + 1
                expected_first = main_no
            except ValueError:
                continue
    return anomalies


def to_markdown(chapters: list[ChapterResult], level: int = 1) -> str:
    """将章节列表导出为多级 Markdown。"""
    lines: list[str] = []
    for ch in chapters:
        prefix = "#" * min(level, 6)
        lines.append(f"{prefix} {ch.title}")
        lines.append("")
        if ch.anomalies:
            for a in ch.anomalies:
                lines.append(f"> ⚠ {a}")
            lines.append("")
        if ch.sub_chapters:
            lines.append(to_markdown(ch.sub_chapters, level + 1))
        else:
            lines.append(ch.text)
            lines.append("")
    return "\n".join(lines)
