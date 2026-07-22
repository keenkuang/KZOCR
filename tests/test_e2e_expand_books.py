"""scripts/e2e_expand_books.py 的纯逻辑回归测试。

覆盖 v4 扩面发现的两类数据质量修复：
- body_start：采样跳过封面/目录区，从正文起始页起算；
- render_warnings：渲染健康度异常（疑似 xref 损坏丢字）被记录。

不依赖真实 OCR 引擎 / PDF：render_page 与适配器均被 mock。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from unittest import mock

import numpy as np

# scripts/ 不在默认 sys.path，显式加入以导入待测模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import e2e_expand_books as m  # noqa: E402


class _FakeAdapter:
    """替代 PaddleOCRAdapter / RapidOCRAdapter，仅暴露 run_page().text。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def run_page(self, page_input):  # noqa: ANN001
        return types.SimpleNamespace(text=self._text)


def _fake_render_factory(img, healthy_map=None):
    """构造 render_page 替身：healthy_map 为 {page_num: healthy} 或常量。"""

    def _fake(pdf, page_num, dpi=150, max_pixels=2048):  # noqa: ANN001
        if isinstance(healthy_map, dict):
            healthy = healthy_map.get(page_num, True)
        else:
            healthy = healthy_map if healthy_map is not None else True
        return img, healthy

    return _fake


def test_count_book_body_start_skips_front_matter():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    adapters = (_FakeAdapter("甲乙丙丁戊"), _FakeAdapter("甲乙丙丁戊"))
    with mock.patch.object(
        m, "render_page", _fake_render_factory(img, True)
    ):
        rec = m.count_book(
            "x.pdf", pages=10, dpi=150, paddle=adapters[0], rapid=adapters[1],
            confusion_set={}, body_start=3,
        )
    assert rec["pages_processed"] == 7
    assert [d["page"] for d in rec["per_page"]] == list(range(3, 10))
    assert rec["render_warnings"] == []


def test_count_book_records_render_warnings():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    adapters = (_FakeAdapter("甲乙"), _FakeAdapter("甲乙"))
    fake = _fake_render_factory(img, {4: False, 7: False})
    with mock.patch.object(m, "render_page", fake):
        rec = m.count_book(
            "x.pdf", pages=10, dpi=150, paddle=adapters[0], rapid=adapters[1],
            confusion_set={}, body_start=0,
        )
    assert rec["render_warnings"] == [4, 7]


def test_count_book_counts_divergences():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    a = _FakeAdapter("甲乙丙丁戊")
    b = _FakeAdapter("甲乙己庚辛")  # 与 a 不同 → 产生分歧
    with mock.patch.object(
        m, "render_page", _fake_render_factory(img, True)
    ):
        rec = m.count_book(
            "x.pdf", pages=5, dpi=150, paddle=a, rapid=b,
            confusion_set={}, body_start=0,
        )
    assert rec["pages_processed"] == 5
    assert rec["total_divergences"] > 0


def test_render_page_real_pdf_healthy():
    """render_page 对含文本层的真实 PDF 返回 (img, healthy=True)。"""
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc[0].insert_text((10, 50), "中医古籍OCR")
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(path)
    doc.close()
    try:
        img, healthy = m.render_page(path, 0, dpi=72)
        assert img.ndim == 3
        assert healthy is True
    finally:
        os.unlink(path)


def test_parse_target_line_preserves_internal_spaces():
    """文件名含 2/3/4/5… 个连续空格时，路径必须完整保留，不吞并为单空格。

    回归：e2e nightly 中「胡天宝标本逆从法治疗Ⅱ型糖尿病  _笔记.pdf」因文件名
    含 2 个连续空格，被 split()+join() 吞成 1 个，导致 os.path.isfile 误判
    「文件不存在」并引发失败书无限重试。
    """
    for n in (2, 3, 4, 5, 10):
        name = "胡天宝标本逆从法治疗Ⅱ型糖尿病" + " " * n + "_笔记.pdf"
        path, pgs = m.parse_target_line(f"{name} 40", 20)
        assert pgs == 40
        assert path == name, f"含 {n} 个连续空格时应完整保留，实际={path!r}"
    # 单文件模式（无页码）→ 整行作为路径，用默认页数
    assert m.parse_target_line("普通书名.pdf", 20) == ("普通书名.pdf", 20)
    # 行末非整数（文件名内部有空格，如 foo 123.pdf）→ 整体当路径，不误拆
    assert m.parse_target_line("foo 123.pdf", 20) == ("foo 123.pdf", 20)
