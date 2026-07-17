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
    assert r.text == "" and r.boxes == [] and r.confidence == 0.0

    r = _parse_ppocr_result([])
    assert r.text == "" and r.boxes == []


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


def test_parse_ppocr_dict_format():
    """PaddleX 新格式 dict。"""
    data = [
        {"rec_text": "补气", "rec_score": 0.95, "poly": [[10, 20], [30, 20], [30, 40], [10, 40]]},
        {"rec_text": "方用", "rec_score": 0.90},
    ]
    r = _parse_ppocr_result(data)
    assert r.text == "补气方用"
    assert r.boxes == [[10, 20, 30, 40]]
    assert abs(r.confidence - 0.925) < 0.01


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
