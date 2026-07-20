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
