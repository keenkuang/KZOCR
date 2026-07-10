"""章节合并测试：TOC 驱动合并、三级编号校验、Markdown 导出。"""

from __future__ import annotations

from kzocr.engine.section_merger import (
    _validate_numbers,
    merge_by_toc,
    to_markdown,
)
from kzocr.engine.types import (
    BookResult,
    ChapterResult,
    PageResult,
    TocEntry,
    TocTree,
)


def _book(texts: list[str], toc: TocTree | None = None) -> BookResult:
    pages = [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]
    b = BookResult(book_code="test", title="Test Book", pages=pages)
    b.toc = toc
    return b


# ── 无 TOC ──

def test_no_toc_returns_single_chapter():
    book = _book(["第一页", "第二页"])
    chs = merge_by_toc(book)
    assert len(chs) == 1
    assert chs[0].title == "Test Book"
    assert chs[0].level == 1


def test_no_pages_returns_empty():
    book = _book([])
    assert merge_by_toc(book) == []


# ── 有 TOC ──

def test_simple_toc():
    book = _book(
        ["内科第1页", "内科第2页", "妇科第1页"],
        TocTree(max_depth=2, entries=[
            TocEntry(level=1, title="内科秘验方", page=1),
            TocEntry(level=1, title="妇科秘验方", page=3),
        ]),
    )
    chs = merge_by_toc(book)
    assert len(chs) == 2
    assert chs[0].title == "内科秘验方"
    assert chs[1].title == "妇科秘验方"


def test_toc_with_subchapters():
    book = _book(
        ["内科总论", "感冒方1", "感冒方2", "头痛方", "妇科总论"],
        TocTree(max_depth=3, entries=[
            TocEntry(level=1, title="内科秘验方", page=1, sub_entries=[
                TocEntry(level=3, title="§1 治感冒秘方", page=1),
                TocEntry(level=3, title="§2 治头痛秘方", page=3),
            ]),
            TocEntry(level=1, title="妇科秘验方", page=5),
        ]),
    )
    chs = merge_by_toc(book)
    assert len(chs) == 2
    assert len(chs[0].sub_chapters) == 2
    assert chs[0].sub_chapters[0].title == "§1 治感冒秘方"


# ── 三级编号校验 ──

def test_validate_numbers_continuous():
    text = "1.1 方一 1.2 方二 1.3 方三"
    assert _validate_numbers(text, 3) == []


def test_validate_numbers_gap():
    text = "1.1 方一 1.3 方三"  # 缺 1.2
    anomalies = _validate_numbers(text, 3)
    assert len(anomalies) >= 1
    assert "期望" in anomalies[0]


def test_validate_numbers_section_change():
    text = "1.5 方五 2.1 方一"  # 节号改变
    anomalies = _validate_numbers(text, 3)
    assert len(anomalies) >= 1


# ── Markdown 导出 ──

def test_to_markdown_simple():
    chs = [ChapterResult(title="内科", level=1, page_start=0, page_end=1, text="内科正文")]
    md = to_markdown(chs)
    assert "# 内科" in md
    assert "内科正文" in md


def test_to_markdown_with_anomaly():
    chs = [ChapterResult(
        title="内科", level=1, page_start=0, page_end=0,
        text="1.1 方一", anomalies=["节号偏差: 期望1, 实际2"],
    )]
    md = to_markdown(chs)
    assert "⚠" in md


def test_to_markdown_nested():
    chs = [ChapterResult(
        title="内科", level=1, page_start=0, page_end=1, text="",
        sub_chapters=[ChapterResult(title="§1 感冒", level=3, page_start=0, page_end=1, text="感冒方")],
    )]
    md = to_markdown(chs)
    assert "## §1 感冒" in md


# ── 完整集成 ──

def test_merge_and_validate_integration():
    text1 = "内科总论"
    text2 = "1.1 感冒宁 1.2 止咳散 1.4 头痛方"  # 缺 1.3
    book = _book(
        [text1, text2],
        TocTree(max_depth=2, entries=[
            TocEntry(level=1, title="内科秘验方", page=1),
        ]),
    )
    chs = merge_by_toc(book)
    assert len(chs) == 1
    assert len(chs[0].anomalies) >= 1
    assert chs[0].recipe_count >= 1
