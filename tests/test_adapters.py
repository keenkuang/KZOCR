"""kzocr.engine.adapters 单元测试（mock 数据，无真实引擎/图像依赖）。"""
from __future__ import annotations

from kzocr.engine.adapters import (
    _parse_ppocr_result,
    _parse_rapidocr_result,
    _quad_to_rect,
)


def test_quad_to_rect():
    quad = [[10, 20], [30, 20], [30, 40], [10, 40]]
    assert _quad_to_rect(quad) == [10, 20, 30, 40]


def test_parse_ppocr_empty():
    r = _parse_ppocr_result(None)
    assert r.text == "" and r.boxes is None and r.char_boxes is None and r.confidence == 0.0

    r = _parse_ppocr_result([])
    assert r.text == "" and r.boxes is None and r.char_boxes is None


def test_parse_ppocr_tuple_format():
    """旧格式：res = [(quad, (text, score)), ...]。"""
    data = [
        ([[10, 20], [30, 20], [30, 40], [10, 40]], ("补气", 0.95)),
        ([[50, 20], [70, 20], [70, 40], [50, 40]], ("方用", 0.90)),
    ]
    r = _parse_ppocr_result(data)
    assert r.text == "补气方用"
    assert r.boxes == [[10, 20, 30, 40], [50, 20, 70, 40]]
    assert abs(r.confidence - 0.925) < 0.01


def test_parse_ppocr_list_format():
    """部分版本返回 list 而非 tuple。"""
    data = [
        [[[10, 20], [30, 20], [30, 40], [10, 40]], ("补气", 0.95)],
    ]
    r = _parse_ppocr_result(data)
    assert r.text == "补气"
    assert r.boxes == [[10, 20, 30, 40]]


def test_parse_ppocr_paddlex_page_format():
    """PaddleX 页面级 OCRResult（dict 子类）：rec_texts / rec_polys / text_word_boxes。

    对应真实引擎 eng.ocr(img, return_word_box=True) 的输出结构。
    """
    data = [{
        "rec_texts": ["补气", "方用"],
        "rec_scores": [0.95, 0.90],
        "rec_polys": [
            [[10, 20], [30, 20], [30, 40], [10, 40]],
            [[50, 20], [70, 20], [70, 40], [50, 40]],
        ],
        "text_word": [["补", "气"], ["方", "用"]],
        "text_word_boxes": [
            [[10, 20, 30, 40], [12, 20, 28, 40]],
            [[50, 20, 70, 40], [52, 20, 68, 40]],
        ],
    }]
    r = _parse_ppocr_result(data)
    assert r.text == "补气方用"
    assert r.boxes == [[10, 20, 30, 40], [50, 20, 70, 40]]
    assert abs(r.confidence - 0.925) < 0.01
    # 字符级 bbox：每行逐字 [x1,y1,x2,y2]
    assert r.char_boxes is not None
    assert r.char_boxes[0] == [[10, 20, 30, 40], [12, 20, 28, 40]]
    assert r.char_boxes[1] == [[50, 20, 70, 40], [52, 20, 68, 40]]


def test_parse_rapidocr_empty():
    r = _parse_rapidocr_result(None)
    assert r.text == "" and r.boxes == []


def test_parse_rapidocr_normal():
    data = [
        ([[10, 20], [30, 20], [30, 40], [10, 40]], "补气"),
        ([[50, 20], [70, 20], [70, 40], [50, 40]], "方用"),
    ]
    r = _parse_rapidocr_result(data)
    assert r.text == "补气方用"
    assert r.boxes == [[10, 20, 30, 40], [50, 20, 70, 40]]


def test_parse_rapidocr_skips_empty_text():
    data = [
        ([[10, 20], [30, 20], [30, 40], [10, 40]], ""),
        ([[50, 20], [70, 20], [70, 40], [50, 40]], "方用"),
    ]
    r = _parse_rapidocr_result(data)
    assert r.text == "方用"
    assert len(r.boxes) == 1
