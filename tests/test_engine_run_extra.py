"""engine/run.py 补充纯逻辑单测（零 OCR 引擎 / 无真实 PDF 渲染依赖）。

针对原覆盖率 82% 的未覆盖纯逻辑分支：
- ``_read_deliverable``：从 BookPipeline 返回字典/对象/交付物目录取 Markdown（239-262）
- ``_markdown_to_pages``：空分段跳过（276）
- ``_merge_cross_page_breaks``：空页/空末行/空续行/装饰行分支（567, 573-574, 594-595, 599-600）
- ``_process_vlm_page``：单页路径 + 超长 OverSizeError（528, 532）
- ``_init_v07_registry``：kimi / sensenova 注册分支（62-66, 72-73）
- ``run_engine``：persist_db 落库分支（含失败兜底）（134-141）
- ``_run_vlm``：页数上限截断（665-667）
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kzocr.config import Config
from kzocr.engine import run as run_mod
from kzocr.engine.run import (
    _init_v07_registry,
    _markdown_to_pages,
    _merge_cross_page_breaks,
    _process_vlm_page,
    _read_deliverable,
    _run_vlm,
    run_engine,
)
from kzocr.engines.errors import OverSizeError


# ── _read_deliverable ──

def test_read_deliverable_dict_final_markdown() -> None:
    assert _read_deliverable({"final_markdown": "X"}, "/tmp", "B") == "X"


def test_read_deliverable_dict_markdown_attr() -> None:
    assert _read_deliverable({"markdown": "M"}, "/tmp", "B") == "M"


def test_read_deliverable_dict_final_text_attr() -> None:
    assert _read_deliverable({"final_text": "T"}, "/tmp", "B") == "T"


def test_read_deliverable_dict_outputs_md(tmp_path) -> None:
    md = tmp_path / "out.md"
    md.write_text("交付物正文", encoding="utf-8")
    out = _read_deliverable({"outputs": {"p": str(md)}}, "/tmp", "B")
    assert out == "交付物正文"


def test_read_deliverable_object_outputs_attr() -> None:
    class R:
        outputs = None
    # 对象分支（245-246）：outputs=None → 跳过，最终返回 ""
    assert _read_deliverable(R(), "/nonexistent", "B") == ""


def test_read_deliverable_lib_dir_body_md(tmp_path) -> None:
    base = tmp_path / "BID"
    base.mkdir()
    (base / "body.md").write_text("版心正文", encoding="utf-8")
    assert _read_deliverable({}, str(tmp_path), "BID") == "版心正文"


def test_read_deliverable_lib_dir_other_md(tmp_path) -> None:
    base = tmp_path / "BID"
    base.mkdir()
    (base / "random.md").write_text("其他正文", encoding="utf-8")
    # 无 body/full/final_document → 回退到 rglob 最新 md（259-261）
    assert _read_deliverable({}, str(tmp_path), "BID") == "其他正文"


def test_read_deliverable_empty() -> None:
    assert _read_deliverable({}, "/nonexistent_dir", "B") == ""


# ── _markdown_to_pages ──

def test_markdown_to_pages_skips_empty_segment() -> None:
    # 第二个分段为空白 → 276 跳过
    md = "## 第 1 页\n正文A\n\n## 第 2 页\n\n"
    pages = _markdown_to_pages(md, "B")
    assert len(pages) == 1
    assert pages[0].page_num == 1


# ── _merge_cross_page_breaks ──

def test_merge_empty_page_continues() -> None:
    # 567：当前页为空 → 不合并
    assert _merge_cross_page_breaks(["", "有内容"]) == ["", "有内容"]


def test_merge_blank_continuation_line() -> None:
    # 594-595：续接段中的空行 → 归入 remaining，不并入
    out = _merge_cross_page_breaks(["不成句，", "续行\n\n又续"])
    assert out[0] == "不成句，\n续行\n又续"


def test_merge_decorative_line() -> None:
    # 599-600：续接段以装饰符【开头 → 归入 remaining，不并入
    out = _merge_cross_page_breaks(["不成句，", "【注】x\n续"])
    assert out[0] == "不成句，\n续"


# ── _process_vlm_page ──

class _FakeVlmSingle:
    def recognize_page(self, img: np.ndarray) -> str:
        return "单页文本"


class _FakeVlmLong:
    def recognize_page(self, img: np.ndarray) -> str:
        return "x" * 9000


def test_process_vlm_page_single_page() -> None:
    # 528：非双页 → 走 recognize_page
    text = _process_vlm_page(
        _FakeVlmSingle(), np.zeros((10, 10, 3), np.uint8),
        supports_two_page=False,
    )
    assert text == "单页文本"


def test_process_vlm_page_oversize_raises() -> None:
    # 532：输出超 8000 字 → OverSizeError
    with pytest.raises(OverSizeError):
        _process_vlm_page(
            _FakeVlmLong(), np.zeros((10, 10, 3), np.uint8),
            supports_two_page=False,
        )


# ── _init_v07_registry ──

def test_init_v07_registry_registers_real_engines(monkeypatch) -> None:
    # 用轻量桩替换 BookPipelineAdapter，避免真实 tcm_ocr 初始化（覆盖 62-66）
    class FakeBookPipe:
        def __init__(self, name: str, pipeline_config=None, temperature: float = 0.0) -> None:
            self.name = name

    monkeypatch.setattr("kzocr.engine.run.BookPipelineAdapter", FakeBookPipe)
    cfg = Config(
        use_mock=False,
        kimi_engine_dir="/tmp/eng",
        sensenova_api_key="sk-x",
        sensenova_base_url="http://x",
    )
    reg = _init_v07_registry(cfg)
    names = {r.meta.name for r in reg.list()}
    assert "kimi" in names
    assert "sensenova" in names


# ── run_engine persist_db 分支 ──

def test_run_engine_persist_db(monkeypatch) -> None:
    cfg = Config(use_mock=True)
    cfg.scheduler.persist_db = True
    fake_book = MagicMock()
    monkeypatch.setattr(run_mod, "orchestrate_book", lambda **kw: fake_book)
    monkeypatch.setattr(run_mod, "enrich_book_result", lambda b: None)
    captured: dict = {}

    class FakeBookDB:
        @staticmethod
        def persist_book_result(book, db_dir=None) -> None:
            captured["book"] = book

    monkeypatch.setattr("kzocr.storage.db.BookDB", FakeBookDB)
    result = run_engine("/fake.pdf", "BC", config=cfg)
    assert result is fake_book
    assert captured["book"] is fake_book


def test_run_engine_persist_db_failure_logged(monkeypatch) -> None:
    # 140-141：落库异常不阻断主流程
    cfg = Config(use_mock=True)
    cfg.scheduler.persist_db = True
    fake_book = MagicMock()
    monkeypatch.setattr(run_mod, "orchestrate_book", lambda **kw: fake_book)
    monkeypatch.setattr(run_mod, "enrich_book_result", lambda b: None)

    class FakeBookDBFail:
        @staticmethod
        def persist_book_result(book, db_dir=None) -> None:
            raise RuntimeError("db down")

    monkeypatch.setattr("kzocr.storage.db.BookDB", FakeBookDBFail)
    result = run_engine("/fake.pdf", "BC", config=cfg)
    assert result is fake_book


# ── _run_vlm 页数上限截断 ──

@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_run_vlm_max_pages_truncation(mock_init_vlm, mock_fitz_open) -> None:
    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = MagicMock(
        samples=b"\xff" * (100 * 200 * 3), n=3, height=100, width=200,
    )
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 8
    mock_doc.__iter__.return_value = iter([mock_page] * 8)
    mock_fitz_open.return_value = mock_doc

    # 用真实类而非 MagicMock：避免 MagicMock 自带 recognize_pages 误触发双页路径
    class FakeVlm:
        engine_label = "PaddleOCR-VL-1.6"

        def recognize_page(self, img: np.ndarray) -> str:
            return "截断测试正文。"

    mock_init_vlm.return_value = FakeVlm()

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
    cfg.scheduler.max_pages = 3

    with patch(
        "kzocr.engine.run._crop_to_body",
        return_value=np.zeros((100, 200, 3), np.uint8),
    ):
        result = _run_vlm("/fake/multi.pdf", cfg, "MAXP")

    assert len(result.pages) == 3
