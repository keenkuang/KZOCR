"""orchestrator._merge_tier1_char_boxes 单测（mock，无引擎）。

验证：Tier1 适配器产出的字符级 bbox 能按 page_num 合并回最终页。
"""
from __future__ import annotations

from kzocr.engine.types import BookResult, PageResult
from kzocr.scheduler.orchestrator import _merge_tier1_char_boxes


def test_merge_char_boxes_by_page_num():
    final_pages = [
        PageResult(page_num=0, text="补气"),
        PageResult(page_num=1, text="方用"),
        PageResult(page_num=2, text="海藻"),
    ]
    tier1 = BookResult(
        book_code="B",
        title="测试书",
        pages=[
            PageResult(page_num=0, text="补气", char_boxes=[[[1, 1, 2, 2]]]),
            # 第 1 页 RapidOCR 无字符框 → None
            PageResult(page_num=1, text="方用", char_boxes=None),
            PageResult(page_num=2, text="海藻", char_boxes=[[[3, 3, 4, 4]], [[5, 5, 6, 6]]]),
        ],
    )
    _merge_tier1_char_boxes(final_pages, tier1)

    assert final_pages[0].char_boxes == [[[1, 1, 2, 2]]]
    assert final_pages[1].char_boxes is None
    assert final_pages[2].char_boxes == [[[3, 3, 4, 4]], [[5, 5, 6, 6]]]


def test_merge_no_tier1_is_noop():
    final_pages = [PageResult(page_num=0, text="甲")]
    _merge_tier1_char_boxes(final_pages, None)
    assert final_pages[0].char_boxes is None
