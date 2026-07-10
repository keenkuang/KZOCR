"""types.py 契约冻结测试 — round4 B1/B2/B7 裁决验证。"""
from __future__ import annotations

import json

import pytest

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    GlyphStatus,
    LineResult,
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
