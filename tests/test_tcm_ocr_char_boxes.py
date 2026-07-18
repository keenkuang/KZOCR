"""page_pipeline._multi_engine_recognition 字符框接线测试（mock 引擎，无模型）。

验证 §3.3：对 paddleocr 行 ROI 调 recognize_char_level + extract_char_bboxes，
det_box = 页绝对行框（不偏移），orig_line_h/w = roi.shape[:2]（行 ROI 自身尺寸），
写入 line["char_bboxes"]（页绝对坐标）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from kzocr.tcm_ocr.pipeline.page_pipeline import PagePipeline


def test_multi_engine_recognition_populates_char_bboxes():
    page_img = np.zeros((500, 400, 3), dtype=np.uint8)  # h=500, w=400
    lines = [
        {"bbox": [10, 100, 200, 130], "type": "text", "text": "x", "block_id": "b1"},
    ]

    captured = {}
    mock_engine = MagicMock()
    mock_engine.recognize.return_value = ("当归", 0.95)
    mock_engine.recognize_char_level.return_value = [
        {"char": "当", "conf": 0.9, "start_step": 1, "end_step": 3},
        {"char": "归", "conf": 0.9, "start_step": 4, "end_step": 6},
    ]

    def fake_extract(det_box, char_details, h, w):
        captured["det_box"] = list(det_box)
        captured["h"] = h
        captured["w"] = w
        # extract_char_bboxes 返回页绝对坐标（与 det_box 同域，无额外偏移）
        return [
            {"char": "当", "bbox": [det_box[0] + 1, det_box[1], det_box[0] + 20, det_box[3]]},
            {"char": "归", "bbox": [det_box[0] + 21, det_box[1], det_box[0] + 40, det_box[3]]},
        ]

    mock_engine.extract_char_bboxes.side_effect = fake_extract

    pipe = PagePipeline(config={}, engines={"paddleocr": mock_engine}, term_kb=None)
    out = pipe._multi_engine_recognition(page_img, lines)

    # char_bboxes 已写入行
    assert "char_bboxes" in out[0]
    assert len(out[0]["char_bboxes"]) == 2

    # det_box = 页绝对行框（未偏移）
    assert captured["det_box"] == [10, 100, 200, 130]
    # orig_line_h/w = roi.shape[:2] = (30, 190)，非整页 (500, 400)
    assert captured["h"] == 30
    assert captured["w"] == 190

    # 返回页绝对坐标：每行 char bbox 的 y 落在 [100, 130]（与 det_box 同域）
    for det in out[0]["char_bboxes"]:
        bbox = det["bbox"]
        assert bbox[1] == 100 and bbox[3] == 130
