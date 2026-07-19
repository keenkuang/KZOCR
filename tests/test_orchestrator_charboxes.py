"""orchestrator._merge_tier1_char_boxes 单测（mock，无引擎）。

验证：Tier1 适配器产出的字符级 bbox 能按 page_num 合并回最终页。
"""
from __future__ import annotations

from kzocr.engine.types import BookResult, PageResult
from kzocr.scheduler.orchestrator import _build_pages_result, _merge_tier1_char_boxes


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


def test_build_pages_result_preserves_real_page_num_with_gap():
    """失败页缺口下，page_num 必须保留真实页号而非位置索引，
    否则 _merge_tier1_char_boxes 会把失败页的字符框错配到成功页。

    模拟：真实页 0/2/3 成功、页 1 失败未入 pages_text。
    """
    pages_text = ["补气", "海藻", "方用"]  # 对应真实页 0, 2, 3
    pages_order = [0, 2, 3]
    final_pages = _build_pages_result(pages_text, pages_order)
    assert [p.page_num for p in final_pages] == [0, 2, 3]

    # Tier1 适配器产出全部页的字符框（真实页号 0-based，含失败页 1）
    tier1 = BookResult(
        book_code="B",
        title="测试书",
        pages=[
            PageResult(page_num=0, text="补气", char_boxes=[[[1, 1, 2, 2]]]),
            PageResult(page_num=1, text="(失败页)", char_boxes=[[[9, 9, 9, 9]]]),  # 不应泄漏
            PageResult(page_num=2, text="海藻", char_boxes=[[[3, 3, 4, 4]]]),
            PageResult(page_num=3, text="方用", char_boxes=[[[5, 5, 6, 6]]]),
        ],
    )
    _merge_tier1_char_boxes(final_pages, tier1)

    # 失败页（page_num=1）的字符框不得错配到任何成功页
    assert final_pages[0].char_boxes == [[[1, 1, 2, 2]]]   # 真实页 0
    assert final_pages[1].char_boxes == [[[3, 3, 4, 4]]]   # 真实页 2（非失败页框）
    assert final_pages[2].char_boxes == [[[5, 5, 6, 6]]]   # 真实页 3
