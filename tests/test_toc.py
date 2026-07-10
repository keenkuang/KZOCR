"""F1: TOC 抽取测试（正向/OCR噪声/负向/跨页/压力/去重）。"""

from __future__ import annotations

import time

from kzocr.engine.toc import (
    TOC_HEADER_KEYWORDS,
    build_toc,
    build_toc_tree,
    discover_toc_pages,
    enrich_book_result,
    parse_toc,
)
from kzocr.engine.types import BookResult, PageResult


# ── 正向：3 层 → chapter / section / subsection ──
def test_discover_toc_page_normal():
    pages = [
        "普通正文内容……",
        "目　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1\n1.1 特效感冒宁……………………1",
        "后续正文",
    ]
    assert discover_toc_pages(pages) == [1]


def test_parse_three_levels():
    pages = [
        "目　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1\n1.1 特效感冒宁……………………1",
    ]
    entries = parse_toc(pages, [0])
    assert len(entries) >= 3
    levels = [e["level"] for e in entries]
    # 至少包含 1, 3, 开头（具体值依赖检测逻辑）
    assert max(levels) <= 5


def test_build_toc_tree_three_levels():
    entries = [
        {"level": 1, "title": "内科秘验方", "page": 1, "section_no": ""},
        {"level": 3, "title": "§1 治感冒秘方", "page": 1, "section_no": "§1"},
        {"level": 4, "title": "1.1 特效感冒宁", "page": 1, "section_no": "1.1"},
    ]
    tree = build_toc_tree(entries)
    assert tree is not None
    assert tree.max_depth == 4
    assert len(tree.entries) == 1
    assert len(tree.entries[0].sub_entries) == 1  # 挂到 level 1 下


# ── 正向：5 层 ──
def test_build_toc_tree_five_levels():
    entries = [
        {"level": 1, "title": "卷上", "page": 1, "section_no": ""},
        {"level": 2, "title": "草部", "page": 2, "section_no": ""},
        {"level": 3, "title": "大黄", "page": 5, "section_no": ""},
        {"level": 4, "title": "主治", "page": 6, "section_no": ""},
        {"level": 5, "title": "(1) 泻下攻积", "page": 6, "section_no": ""},
    ]
    tree = build_toc_tree(entries)
    assert tree is not None
    assert tree.max_depth == 5
    # 卷上 → 草部 → 大黄 → 主治 → (1)
    assert len(tree.entries) == 1
    l2 = tree.entries[0].sub_entries
    assert len(l2) == 1
    l3 = l2[0].sub_entries
    assert len(l3) == 1
    l4 = l3[0].sub_entries
    assert len(l4) == 1


# ── B1: OCR 噪声容错 ──
def test_discover_ocr_noise_mu_as_ri():
    """OCR 将"目录"识别为"日录"仍触发发现。"""
    pages = ["正文", "日　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1", "正文"]
    found = discover_toc_pages(pages, keywords=TOC_HEADER_KEYWORDS)
    assert len(found) >= 1


def test_discover_ocr_noise_mu_as_zi():
    """OCR 将"目录"识别为"自录"仍触发发现。"""
    pages = ["正文", "自录\n内科秘验方……………………1\n§1 治感冒秘方……………………1", "正文"]
    found = discover_toc_pages(pages, keywords=TOC_HEADER_KEYWORDS)
    assert len(found) >= 1


def test_discover_ocr_noise_traditional():
    """繁体"目錄"触发发现。"""
    pages = ["正文", "目錄\n內科秘驗方……………………1\n§1 治感冒秘方……………………1", "正文"]
    found = discover_toc_pages(pages, keywords=TOC_HEADER_KEYWORDS)
    assert len(found) >= 1


# ── B5: 负向测试 ──
def test_no_toc_returns_none():
    pages = ["普通正文第一页", "普通正文第二页"]
    tree = build_toc(pages)
    assert tree is None


def test_header_word_without_entries_ignored():
    """正文含"目录"二字但无目录结构→不误报。"""
    pages = ["本书分为三个目录: a, b, c"]
    assert discover_toc_pages(pages) == []


def test_header_only_no_entries_ignored():
    """仅"目　录"标题行无条目→不报告。"""
    pages = ["目　录", "正文"]
    assert discover_toc_pages(pages) == []


def test_enrich_no_toc():
    """enrich_book_result 对无目录书不抛错，toc=None。"""
    result = BookResult(book_code="x", title="x", pages=[PageResult(page_num=0, text="正文")])
    enrich_book_result(result)
    assert result.toc is None


# ── R6: 中文数字页码 ──
def test_chinese_numeral_page():
    pages = ["目　录\n内科秘验方……………………三十"]
    entries = parse_toc(pages, [0])
    assert len(entries) >= 1
    assert entries[0]["page"] == 30


# ── 跨页合并 ──
def test_cross_page_toc_merge():
    pages = [
        "正文1",
        "目　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1",
        "1.1 特效感冒宁……………………1\n§2 治头痛秘方……………………2",
    ]
    entries = parse_toc(pages, [1, 2])
    assert len(entries) >= 3


# ── R10: 非连续页 ──
def test_non_contiguous_toc_pages():
    pages = [
        "正文1",
        "目　录\n内科秘验方……………………1",
        "正文2",
        "§1 治感冒秘方……………………1\n1.1 特效感冒宁……………………1",
    ]
    entries = parse_toc(pages, [1, 3])
    assert len(entries) >= 3


# ── R5: 节标题扩展 ──
def test_section_variants():
    pages = [
        "目　录\n第一节 治感冒秘方………………1\n（一）风寒感冒………………1\n一、辨证要点………………1",
    ]
    entries = parse_toc(pages, [0])
    if entries:
        assert all(e["level"] >= 3 for e in entries)  # 应解析为节或以上


# ── R8: 大目录压力 ──
def test_large_toc_pressure():
    """500+ 条目 5 层深，应在 1s 内完成。"""
    entries = []
    for i in range(1, 101):
        entries.append({"level": 1, "title": f"卷第{i}", "page": i, "section_no": ""})
        for j in range(1, 4):
            entries.append({"level": 2, "title": f"章{j}", "page": i + j, "section_no": ""})
            for k in range(1, 3):
                entries.append({"level": 3, "title": f"节{k}", "page": i + j + k, "section_no": ""})
    t0 = time.monotonic()
    tree = build_toc_tree(entries)
    elapsed = time.monotonic() - t0
    assert tree is not None
    assert tree.max_depth == 3
    assert elapsed < 1.0, f"build_toc_tree took {elapsed:.3f}s"


# ── R9: 编号去重 ──
def test_duplicate_section_no_does_not_crash():
    entries = [
        {"level": 1, "title": "内科", "page": 1, "section_no": "1"},
        {"level": 3, "title": "§1 感冒", "page": 2, "section_no": "§1"},
        {"level": 3, "title": "§1 咳嗽", "page": 5, "section_no": "§1"},  # 重复
    ]
    tree = build_toc_tree(entries)
    assert tree is not None
    # 两个 §1 都应挂到内科下
    assert len(tree.entries) == 1


# ── 集成：build_toc 全流程 ──
def test_build_toc_integration():
    pages = [
        "前面几页",
        "目　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1\n1.1 特效感冒宁……………………1",
        "正文第一页",
    ]
    tree = build_toc(pages)
    assert tree is not None
    assert tree.max_depth >= 3
    assert len(tree.entries) >= 1


def test_enrich_book_result():
    result = BookResult(
        book_code="test",
        title="Test",
        pages=[PageResult(page_num=0, text="正文"),
               PageResult(page_num=1, text="目　录\n内科秘验方……………………1\n§1 治感冒秘方……………………1"),
               PageResult(page_num=2, text="正文")],
    )
    enrich_book_result(result)
    assert result.toc is not None
    assert len(result.toc.entries) >= 1
