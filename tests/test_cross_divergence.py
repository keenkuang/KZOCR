"""跨引擎分歧：BookDB 落库单测 + orchestrator 集成测试（借鉴 ocr_pipeline_v2）。

沿用 tests/test_orchestrator.py 的桩模式（FakeBookAdapter/FakePageAdapter/_reg），
无真实 PDF / 网络依赖。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    BookResult,
    EngineConfig,
    PageInput,
    PageResult,
)
from kzocr.scheduler import orchestrator as _orc
from kzocr.scheduler.orchestrator import orchestrate_book
from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.cross_align import Divergence
from kzocr.storage.db import BookDB


# ── BookDB 落库单测 ──
def test_write_and_get_cross_divergences(tmp_path):
    db = BookDB("bkdb", db_dir=str(tmp_path))
    divs = [
        Divergence(
            page_no=0, div_type="replace", a_seg="三", b_seg="二",
            a_context="【三】", priority="high", engine_a="t1", engine_b="t3",
        ),
        Divergence(page_no=0, div_type="delete", a_seg="", b_seg="", priority="normal"),
    ]
    n = db.write_cross_divergences(0, divs, engine_a="t1", engine_b="t3")
    assert n == 2
    rows = db.get_cross_divergences()
    assert len(rows) == 2
    assert rows[0]["priority"] == "high"
    assert rows[0]["a_seg"] == "三"
    assert rows[0]["engine_a"] == "t1"
    assert rows[0]["engine_b"] == "t3"
    # 按页过滤
    paged = db.get_cross_divergences(page_no=0)
    assert len(paged) == 2
    db.close()


# ── orchestrator 集成测试（复用 test_orchestrator 桩）──
@dataclass
class StubConfig:
    max_pages: int = 50
    total_timeout_s: int = 7200
    max_time_per_page_ms: int = 120000
    allow_cloud_vision: bool = False
    book_type: str = ""
    pub_era: str = ""
    output_dir: str = ""
    trace_dir: str = ""
    db_dir: str = ""


class FakeBookAdapter:
    def __init__(self, pages=None):
        self.pages = pages or []

    def run_book(self, pdf_path):
        return BookResult(book_code="test", title="Test", pages=self.pages)

    def run_page(self, pi):
        raise NotImplementedError


class FakePageAdapter:
    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def run_book(self, pdf_path):
        raise NotImplementedError

    def run_page(self, pi):
        if not self.responses:
            raise RuntimeError("exhausted")
        return self.responses.pop(0)


def _text_pages(*texts):
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _page_result(text):
    return AdapterPageResult(text=text)


def _reg(tier1_pages=None, tier2_texts=None, tier3_texts=None):
    reg = EngineRegistry()
    if tier1_pages is not None:
        reg.register_adapter(
            AdapterMeta(name="t1", label="T1", tier=1, batch_capable=True),
            EngineConfig(), adapter=FakeBookAdapter(tier1_pages),
        )
    if tier2_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t2", label="T2", tier=2, requires_network=True),
            EngineConfig(), adapter=FakePageAdapter([_page_result(t) for t in tier2_texts]),
        )
    if tier3_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t3", label="T3", tier=3),
            EngineConfig(), adapter=FakePageAdapter([_page_result(t) for t in tier3_texts]),
        )
    return reg


def _render_gen(n):
    for i in range(n):
        yield PageInput(page_num=i, img=None)


@pytest.fixture(autouse=True)
def _patch_render(monkeypatch):
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(1))


def _read_db(book_code, db_dir):
    db = BookDB(book_code, db_dir=db_dir)
    rows = db.get_cross_divergences()
    db.close()
    return rows


def test_cross_align_writes_on_tier1_fail_tier3_success(tmp_path):
    """Tier1 触发毒性剂量 FAIL → Tier3 不同文本（剂量数字分歧）→ cross_divergence 落库。"""
    reg = _reg(
        tier1_pages=_text_pages("附子20g"),   # ToxinDose FAIL(critical)
        tier3_texts=["附子二钱"],             # 数字/剂量分歧：20g ↔ 二钱
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    result = orchestrate_book("/fp", "bkc1", cfg, reg)
    assert len(result.pages) == 1  # Tier3 文本被采纳

    rows = _read_db("bkc1", str(tmp_path))
    assert len(rows) >= 1
    # 剂量数字分歧应标 high
    assert any(r["priority"] == "high" for r in rows)
    # 引擎标签正确
    assert any(r["engine_a"] == "t1" and r["engine_b"] == "t3" for r in rows)


def test_cross_align_skipped_on_tier1_success(tmp_path):
    """Tier1 直接通过 → 不进入 Tier3，无分歧落库。"""
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    cfg = StubConfig(db_dir=str(tmp_path))
    orchestrate_book("/fp", "bkc2", cfg, reg)
    rows = _read_db("bkc2", str(tmp_path))
    assert rows == []
