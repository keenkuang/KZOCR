"""types.py 契约冻结测试 — round4 B1/B2/B7 裁决验证。"""
from __future__ import annotations

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    BookResult,
    EngineCallRecord,
    GlyphVerdict,
    LineResult,
    PageResult,
    ProbeResult,
)


class TestGlyphStatus:
    def test_enum_values(self):
        """glyph_status 取值必须在 GlyphStatus 枚举中。"""
        valid = {"PASS", "RARE", "UNKNOWN", "FAIL", "UNCERTAIN"}
        for v in valid:
            # 构造验证：Literal 类型注解不生成运行时枚举，仅验证取值可用
            line = LineResult(glyph_status=v)  # type: ignore[arg-type]
            assert line.glyph_status == v

    def test_default_none(self):
        """glyph_status 默认值为 None。"""
        line = LineResult()
        assert line.glyph_status is None

    def test_glyph_verified_preserved(self):
        """glyph_verified 仍存文本，不受 glyph_status 影响（B1 双字段共存）。"""
        line = LineResult(glyph_verified="黄芪", glyph_status="PASS")  # type: ignore[arg-type]
        assert line.glyph_verified == "黄芪"
        assert line.glyph_status == "PASS"
        assert line.glyph_verified != line.glyph_status  # 文本列 ≠ 枚举列

    def test_glyph_verified_default_none(self):
        """glyph_verified 默认 None（向后兼容）。"""
        line = LineResult()
        assert line.glyph_verified is None

    def test_crop_img_path_default_none(self):
        """crop_img_path 默认 None。"""
        line = LineResult()
        assert line.crop_img_path is None

    def test_crop_img_path_set(self):
        """crop_img_path 可设路径字符串。"""
        line = LineResult(crop_img_path="/tmp/page_001_line_003.png")
        assert line.crop_img_path == "/tmp/page_001_line_003.png"


class TestProbeResult:
    def test_defaults(self):
        """ProbeResult 默认构造。"""
        pr = ProbeResult()
        assert pr.gpu is False
        assert pr.vram_gb == 0.0
        assert pr.cpu_cores == 1
        assert pr.ports == {}
        assert pr.keys == {}
        assert pr.allow_cloud_vision is False

    def test_filled(self):
        """ProbeResult 全字段填充。"""
        pr = ProbeResult(
            gpu=True,
            vram_gb=24.0,
            cpu_cores=16,
            ports={"18080": True, "18083": False},
            keys={"sensenova": True},
            allow_cloud_vision=True,
        )
        assert pr.gpu is True
        assert pr.vram_gb == 24.0
        assert pr.cpu_cores == 16
        assert pr.ports["18080"] is True
        assert pr.keys["sensenova"] is True
        assert pr.allow_cloud_vision is True


class TestAdapterMeta:
    def test_defaults(self):
        """AdapterMeta 默认构造。"""
        m = AdapterMeta(name="paddleocr", label="PaddleOCR")
        assert m.name == "paddleocr"
        assert m.label == "PaddleOCR"
        assert m.kind == "page"
        assert m.supports_confidence is True
        assert m.default_enabled is True
        assert m.requires_gpu is False
        assert m.requires_network is False

    def test_book_kind(self):
        """书级适配器 (BookPipeline shim)。"""
        m = AdapterMeta(
            name="kimi_book",
            label="kimi BookPipeline",
            kind="book",
            requires_gpu=False,
            requires_network=False,
        )
        assert m.kind == "book"

    def test_vlm_adapter(self):
        """VLM 适配器（无字级置信）。"""
        m = AdapterMeta(
            name="paddleocr_vl16",
            label="PaddleOCR-VL-1.6",
            supports_confidence=False,
            supports_context=True,
            default_enabled=False,
        )
        assert m.supports_confidence is False
        assert m.supports_context is True
        assert m.default_enabled is False


class TestAdapterPageResult:
    def test_basic(self):
        """AdapterPageResult 基础构造。"""
        apr = AdapterPageResult(text="白术三钱")
        assert apr.text == "白术三钱"
        assert apr.confidence == 0.9
        assert apr.char_confidences is None
        assert apr.crop_img_path is None
        assert apr.meta is None

    def test_with_char_confidences(self):
        """带字级置信度的构造。"""
        apr = AdapterPageResult(
            text="白术三钱",
            confidence=0.95,
            char_confidences=[0.9, 0.8, 0.95, 0.7],
            crop_img_path="/tmp/crop.png",
            meta=AdapterMeta(name="paddleocr", label="PaddleOCR"),
        )
        assert apr.text == "白术三钱"
        assert apr.confidence == 0.95
        assert apr.char_confidences == [0.9, 0.8, 0.95, 0.7]
        assert apr.crop_img_path == "/tmp/crop.png"
        assert apr.meta is not None
        assert apr.meta.name == "paddleocr"


class TestPageResult:
    def test_char_boxes_none_sentinel(self):
        """char_boxes=None 表示引擎不支持字符框（如 RapidOCR）。"""
        p = PageResult(page_num=1)
        assert p.char_boxes is None

    def test_char_boxes_list_supported(self):
        """支持字符框的引擎给出 list[list[list[int]]]（每行 → 逐字 [x1,y1,x2,y2]）。"""
        p = PageResult(page_num=1, char_boxes=[[[1, 2, 3, 4]], []])
        assert p.char_boxes == [[[1, 2, 3, 4]], []]

    def test_mutable_defaults_independent(self):
        """paragraphs 等 list 默认工厂必须每实例独立（防可变默认陷阱）。"""
        a = PageResult(page_num=1)
        b = PageResult(page_num=2)
        assert a.paragraphs is not b.paragraphs
        assert a.paragraphs == []


class TestBookResult:
    def test_mutable_list_defaults_independent(self):
        """聚合体的 list 默认工厂必须每实例独立——否则一书的 pages 会泄漏到另一书。"""
        b1 = BookResult(book_code="A", title="书A")
        b2 = BookResult(book_code="B", title="书B")
        b1.pages.append(PageResult(page_num=1))
        b1.engine_trace.append(EngineCallRecord(page=1, tier=1, engine="x", latency_ms=1.0))
        assert b2.pages == []
        assert b2.engine_trace == []

    def test_mutable_dict_defaults_independent(self):
        """failed_pages / uncertain_pages 等 dict 默认工厂必须每实例独立。"""
        b1 = BookResult(book_code="A", title="书A")
        b2 = BookResult(book_code="B", title="书B")
        b1.failed_pages[5] = "ocr failed"
        b1.uncertain_pages[7] = GlyphVerdict(status="UNCERTAIN", confidence=0.5)
        assert b2.failed_pages == {}
        assert b2.uncertain_pages == {}

    def test_char_boxes_only_on_page_not_line(self):
        """字符框仅挂在 PageResult，LineResult 无此字段（orchestrator 合并的契约前提）。"""
        page = PageResult(page_num=1, char_boxes=[[[1, 2, 3, 4]]])
        line = LineResult(final="黄芪")
        assert page.char_boxes == [[[1, 2, 3, 4]]]
        assert not hasattr(line, "char_boxes")
