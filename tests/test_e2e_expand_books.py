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
            "x.pdf", pages=10, dpi=150, paddle=adapters[0], ovis=adapters[1],
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
            "x.pdf", pages=10, dpi=150, paddle=adapters[0], ovis=adapters[1],
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
            "x.pdf", pages=5, dpi=150, paddle=a, ovis=b,
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


def test_persist_e2e_record_writes_db(tmp_path):
    """_persist_e2e 把一条 e2e 扩面记录写入该书按书分库的 BookDB。"""
    import json as _json
    rec = {
        "book": "胡天宝标本逆从法治疗Ⅱ型糖尿病  _笔记.pdf",
        "pdf": "/x/胡天宝标本逆从法治疗Ⅱ型糖尿病  _笔记.pdf",
        "pages_processed": 40,
        "pages_requested": 40,
        "total_divergences": 100,
        "high_divergences": 20,
        "render_warnings": [3, 7],
    }
    rid = m._persist_e2e(rec, db_dir=str(tmp_path))
    assert rid >= 1
    bc = m._safe_book_code(rec["book"])
    db = m.BookDB(bc, db_dir=str(tmp_path))
    try:
        rows = db.get_e2e_expansions(bc)
    finally:
        db.close()
    assert len(rows) == 1
    r = rows[0]
    assert r["pdf"] == rec["pdf"]
    assert r["book_title"] == rec["book"]
    assert r["pages_processed"] == 40
    assert r["total_divergences"] == 100
    assert r["high_divergences"] == 20
    assert _json.loads(r["render_warnings_json"]) == [3, 7]


def test_safe_book_code_alignment_with_run_py():
    """_safe_book_code 与 kzocr.engine.run 中 VLM 链路用的 book_code 派生规则一致。

    run.py 的 _run_vlm 用 re.sub(r"[^A-Za-z0-9_\\-]", "_", os.path.splitext(title)[0])；
    此处用同一正则独立复算 expected 做对齐校验。
    """
    import re as _re
    name = "胡天宝标本逆从法治疗Ⅱ型糖尿病  _笔记.pdf"
    expected = _re.sub(r"[^A-Za-z0-9_\-]", "_", os.path.splitext(name)[0])
    assert m._safe_book_code(name) == expected
    # 同一文件名多次派生稳定且非空
    assert m._safe_book_code(name) and m._safe_book_code(name) == m._safe_book_code(name)


def test_count_book_attaches_divergences():
    """count_book 的 per_page 每页附带 divergences（asdict 后的分歧对象，含引擎来源）。"""
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    a = _FakeAdapter("甲乙丙丁戊")
    b = _FakeAdapter("甲乙己庚辛")  # 与 a 不同 → 产生分歧
    with mock.patch.object(m, "render_page", _fake_render_factory(img, True)):
        rec = m.count_book(
            "x.pdf", pages=3, dpi=150, paddle=a, ovis=b,
            confusion_set={}, body_start=0,
        )
    for p in rec["per_page"]:
        assert "divergences" in p
        for d in p["divergences"]:
            # asdict 产物为纯 dict，含 Divergence 全部字段
            assert set(d.keys()) >= {
                "page_no", "div_type", "a_seg", "b_seg", "a_context",
                "boxes", "priority", "status", "engine_a", "engine_b",
            }
            # run_cross_align 已注明引擎来源（Module H 落库需正确 provenance）
            assert d["engine_a"] == "PaddleOCR"
            assert d["engine_b"] == "OvisOCR2-Q4_KM"


def test_persist_e2e_writes_divergences(tmp_path):
    """_persist_e2e 把每页逐条分歧明细落 cross_divergence 表，且重跑幂等不重复。"""
    import dataclasses as _dc

    from kzocr.scheduler.cross_align import Divergence

    d1 = Divergence(page_no=0, div_type="replace", a_seg="丙", b_seg="己",
                    a_context="甲乙【丙】丁", priority="P1",
                    engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM")
    d2 = Divergence(page_no=1, div_type="replace", a_seg="戊", b_seg="辛",
                    a_context="庚辛【戊】壬", priority="normal",
                    engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM")
    rec = {
        "book": "测试书.pdf",
        "pdf": "/x/测试书.pdf",
        "pages_processed": 2,
        "pages_requested": 2,
        "total_divergences": 2,
        "high_divergences": 1,
        "render_warnings": [],
        "per_page": [
            {"page": 0, "div": 1, "high": 1,
             "divergences": [_dc.asdict(d1)]},
            {"page": 1, "div": 1, "high": 0,
             "divergences": [_dc.asdict(d2)]},
        ],
    }
    rid = m._persist_e2e(rec, db_dir=str(tmp_path))
    assert rid >= 1
    bc = m._safe_book_code(rec["book"])
    db = m.BookDB(bc, db_dir=str(tmp_path))
    try:
        rows = db.get_cross_divergences()
    finally:
        db.close()
    assert len(rows) == 2
    seg_pairs = {(r["a_seg"], r["b_seg"]) for r in rows}
    assert ("丙", "己") in seg_pairs
    assert ("戊", "辛") in seg_pairs

    # 幂等：同 rec 重跑，按页号清除后重写，行数不变（不产生重复行）
    m._persist_e2e(rec, db_dir=str(tmp_path))
    db = m.BookDB(bc, db_dir=str(tmp_path))
    try:
        assert len(db.get_cross_divergences()) == 2
    finally:
        db.close()


def test_persist_e2e_keeps_divergences_for_pages_without_detail(tmp_path):
    """增量合并 rec 中：带明细的新页重写，无明细的旧页保持原表不动（不误删）。"""
    import dataclasses as _dc

    from kzocr.scheduler.cross_align import Divergence

    bc = m._safe_book_code("测试书.pdf")
    # 先用一条「全部带明细」的 rec 落库 baseline
    base = Divergence(page_no=0, div_type="replace", a_seg="丙", b_seg="己",
                      a_context="甲乙【丙】丁", priority="P1",
                      engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM")
    rec_all = {
        "book": "测试书.pdf", "pdf": "/x/测试书.pdf",
        "pages_processed": 1, "pages_requested": 1,
        "total_divergences": 1, "high_divergences": 1, "render_warnings": [],
        "per_page": [{"page": 0, "div": 1, "high": 1,
                      "divergences": [_dc.asdict(base)]}],
    }
    m._persist_e2e(rec_all, db_dir=str(tmp_path))

    # 增量合并 rec：旧页 p0 无明细（来自旧 summary），新页 p1 带明细
    new_div = Divergence(page_no=1, div_type="replace", a_seg="戊", b_seg="辛",
                         a_context="庚辛【戊】壬", priority="normal",
                         engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM")
    rec_merge = {
        "book": "测试书.pdf", "pdf": "/x/测试书.pdf",
        "pages_processed": 2, "pages_requested": 2,
        "total_divergences": 2, "high_divergences": 1, "render_warnings": [],
        "per_page": [
            {"page": 0, "div": 1, "high": 1},  # 无 divergences
            {"page": 1, "div": 1, "high": 0,
             "divergences": [_dc.asdict(new_div)]},
        ],
    }
    m._persist_e2e(rec_merge, db_dir=str(tmp_path))

    db = m.BookDB(bc, db_dir=str(tmp_path))
    try:
        rows = db.get_cross_divergences()
    finally:
        db.close()
    # p0 的明细未被清除（保持 baseline 落库），p1 新写入 → 共 2 行
    assert len(rows) == 2
    seg_pairs = {(r["a_seg"], r["b_seg"]) for r in rows}
    assert ("丙", "己") in seg_pairs
    assert ("戊", "辛") in seg_pairs


class _FlakyAdapter:
    """Adapter whose run_page raises on demand (simulates OvisOCR2 timeout)."""

    def __init__(self, text: str = "", fail_pages: set[int] | None = None) -> None:
        self._text = text
        self._fail_pages = fail_pages or set()

    def run_page(self, page_input):  # noqa: ANN001
        if page_input.page_num in self._fail_pages:
            raise RuntimeError("OvisOCR2 request failed: timed out")
        return types.SimpleNamespace(text=self._text)


def test_count_book_isolates_single_page_timeout():
    """A single-page engine timeout must skip that page, not crash the batch.

    Regression: OvisOCR2 on CPU can exceed the adapter timeout; an unhandled
    TimeoutError previously aborted e2e_expand_books.py and caused the nightly
    driver to mark the entire 2876-book batch as permanently failed.
    """
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    paddle = _FakeAdapter("aaaaa")
    ovis = _FlakyAdapter(text="aaaaa", fail_pages={2})
    with mock.patch.object(m, "render_page", _fake_render_factory(img, True)):
        rec = m.count_book(
            "x.pdf", pages=5, dpi=150, paddle=paddle, ovis=ovis,
            confusion_set={}, body_start=0,
        )
    assert rec["pages_processed"] == 4
    assert [d["page"] for d in rec["per_page"]] == [0, 1, 3, 4]


def test_count_book_skips_all_pages_when_engine_always_fails():
    """If one engine never responds, every page is skipped but no exception escapes."""
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    paddle = _FakeAdapter("aaaaa")
    ovis = _FlakyAdapter(fail_pages=set(range(10)))
    with mock.patch.object(m, "render_page", _fake_render_factory(img, True)):
        rec = m.count_book(
            "x.pdf", pages=6, dpi=150, paddle=paddle, ovis=ovis,
            confusion_set={}, body_start=0,
        )
    assert rec["pages_processed"] == 0
    assert rec["per_page"] == []
