"""_common.py 转换函数测试 — B2 裁决验证。

adapter_to_line_result() 是唯一的 AdapterPageResult → LineResult 转换入口，
任何适配器不得自行折算 LineResult。
"""
from __future__ import annotations

import json

from kzocr.engine.types import AdapterMeta, AdapterPageResult
from kzocr.engines._common import adapter_to_line_result


_META = AdapterMeta(name="paddleocr", label="PaddleOCR")


class TestAdapterToLineResult:
    def test_basic(self):
        """普通文本 → LineResult 字段正确映射。"""
        apr = AdapterPageResult(text="白术三钱", confidence=0.95)
        line = adapter_to_line_result(apr, "paddleocr")
        assert line.final == "白术三钱"
        assert line.consensus == "白术三钱"
        assert line.confidence == 0.95
        assert line.engine_texts == {"paddleocr": "白术三钱"}
        assert line.sequence_in_paragraph == 1

    def test_char_confidences_to_json(self):
        """char_confidences 序列化到 char_level_json。"""
        apr = AdapterPageResult(
            text="白术三钱",
            char_confidences=[0.9, 0.8, 0.95, 0.7],
        )
        line = adapter_to_line_result(apr, "paddleocr")
        assert line.char_level_json is not None
        parsed = json.loads(line.char_level_json)
        assert parsed["conf"] == [0.9, 0.8, 0.95, 0.7]

    def test_crop_img_path_passthrough(self):
        """crop_img_path 透传到 LineResult。"""
        apr = AdapterPageResult(
            text="白术三钱",
            crop_img_path="/tmp/page_001.png",
        )
        line = adapter_to_line_result(apr, "paddleocr")
        assert line.crop_img_path == "/tmp/page_001.png"

    def test_char_confidences_mismatch_truncated(self):
        """char_confidences 长度与 text 不一致时截断到 min。"""
        apr = AdapterPageResult(
            text="白术三钱",  # 4 个字
            char_confidences=[0.9, 0.8],  # 只有 2 个
        )
        line = adapter_to_line_result(apr, "paddleocr")
        parsed = json.loads(line.char_level_json)
        assert len(parsed["conf"]) == 2  # 截断到 text 长度

    def test_char_confidences_none(self):
        """char_confidences=None 时 char_level_json 为 None。"""
        apr = AdapterPageResult(text="白术三钱", char_confidences=None)
        line = adapter_to_line_result(apr, "paddleocr")
        assert line.char_level_json is None

    def test_engine_result_populated(self):
        """engine_results 包含 EngineResult 条目。"""
        apr = AdapterPageResult(text="白术三钱", confidence=0.95)
        line = adapter_to_line_result(apr, "paddleocr")
        assert len(line.engine_results) == 1
        er = line.engine_results[0]
        assert er.engine == "paddleocr"
        assert er.text == "白术三钱"
        assert er.confidence == 0.95

    def test_engine_name_in_texts(self):
        """engine_name 正确填入 engine_texts。"""
        apr = AdapterPageResult(text="RapidOCR 结果")
        line = adapter_to_line_result(apr, "rapidocr")
        assert "rapidocr" in line.engine_texts
        assert line.engine_texts["rapidocr"] == "RapidOCR 结果"
