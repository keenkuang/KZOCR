"""run_book 的 max_pages 上限回归测试（orchestrator 全路径卡顿根因修复）。

根因：orchestrate_book 第一步调用 Tier1 适配器 run_book(pdf_path, ...)，
而 run_book 内部 for i in range(doc.page_count) 全本扫描，完全无视 max_pages。
对几百页古籍，即便只请求 5 页，也会先 OCR 完几百页才进入逐页循环 → 长时间卡顿。

修复：run_book 接受 max_pages（0=全本），仅 OCR min(max_pages, 总页数) 页；
orchestrator 传入 budget.max_pages。本测试在 mock fitz/引擎下验证切片逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.types import AdapterPageResult, BookResult


def _fake_doc(page_count: int):
    """构造一个 page_count 页的假 fitz.Document（run_page 已被 mock，无需真实图像）。"""
    fake_pix = MagicMock()
    fake_pix.samples = bytes(2 * 2 * 3)  # np.frombuffer 后 reshape(2,2,3) 需 12 字节
    fake_pix.height = 2
    fake_pix.width = 2
    fake_pix.n = 3
    fake_page = MagicMock()
    fake_page.get_pixmap.return_value = fake_pix
    doc = MagicMock()
    doc.page_count = page_count
    doc.__getitem__ = MagicMock(return_value=fake_page)
    return doc


def _run_book_capped(adapter_cls, page_count: int, max_pages: int):
    adapter = adapter_cls()
    fake_doc = _fake_doc(page_count)
    # run_page 被 mock 后直接返回桩结果，引擎初始化方法（_get_engine/_lazy_init）不会被调用
    with patch.object(adapter_cls, "run_page", return_value=AdapterPageResult(text="x")):
        with patch("fitz.open", return_value=fake_doc):
            return adapter.run_book("fake.pdf", max_pages=max_pages)


def test_paddle_run_book_respects_max_pages():
    res = _run_book_capped(PaddleOCRAdapter, page_count=10, max_pages=3)
    assert isinstance(res, BookResult)
    assert len(res.pages) == 3


def test_paddle_run_book_zero_means_all():
    res = _run_book_capped(PaddleOCRAdapter, page_count=10, max_pages=0)
    assert len(res.pages) == 10


def test_paddle_run_book_over_total_is_clamped():
    res = _run_book_capped(PaddleOCRAdapter, page_count=10, max_pages=999)
    assert len(res.pages) == 10


def test_rapid_run_book_respects_max_pages():
    res = _run_book_capped(RapidOCRAdapter, page_count=10, max_pages=4)
    assert len(res.pages) == 4
    res_all = _run_book_capped(RapidOCRAdapter, page_count=10, max_pages=0)
    assert len(res_all.pages) == 10
