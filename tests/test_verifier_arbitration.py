"""分歧级视觉仲裁（Box-Guided VL）单测（借鉴 ocr_pipeline_v2 cross_arbitrate + 豆包帖）。

覆盖：纯函数（JSON 解析容错、门控规则）、未配置/无图像早退、degraded 与 box_guided
两种模式的裁决映射、box 非法跳过。无真实网络依赖（_post_vl 以 monkeypatch 注入）。
"""
from __future__ import annotations

import numpy as np

from kzocr.scheduler import verifier as _ver
from kzocr.scheduler.cross_align import Divergence
from kzocr.scheduler.verifier import VisionRecheckAdapter
from kzocr.storage.db import BookDB


# ── 纯函数：_parse_arbitration_response ──
def test_parse_response_valid_json():
    obj = _ver._parse_arbitration_response('{"is_match": true, "confidence": 0.9, "real_char": "二"}')
    assert obj == {"is_match": True, "confidence": 0.9, "real_char": "二"}


def test_parse_response_code_fence():
    raw = '```json\n{"is_match": false, "confidence": 0.3, "real_char": ""}\n```'
    obj = _ver._parse_arbitration_response(raw)
    assert obj == {"is_match": False, "confidence": 0.3, "real_char": ""}


def test_parse_response_invalid_returns_none():
    assert _ver._parse_arbitration_response("no json here") is None
    assert _ver._parse_arbitration_response("") is None
    assert _ver._parse_arbitration_response("{not json") is None


# ── 纯函数：_gate_arbitration ──
def test_gate_accepted_a():
    d, real = _ver._gate_arbitration(
        {"is_match": True, "confidence": 0.9, "real_char": "三"}, "三", "二",
    )
    assert d == "accepted_a" and real == "三"


def test_gate_accepted_b():
    d, real = _ver._gate_arbitration(
        {"is_match": True, "confidence": 0.9, "real_char": "二"}, "三", "二",
    )
    assert d == "accepted_b"


def test_gate_both_wrong():
    d, real = _ver._gate_arbitration(
        {"is_match": True, "confidence": 0.9, "real_char": "王"}, "三", "二",
    )
    assert d == "both_wrong" and real == "王"


def test_gate_uncertain_when_real_empty():
    d, _ = _ver._gate_arbitration(
        {"is_match": True, "confidence": 0.9, "real_char": ""}, "三", "二",
    )
    assert d == "uncertain"


def test_gate_manual_on_low_conf():
    d, _ = _ver._gate_arbitration(
        {"is_match": True, "confidence": 0.4, "real_char": "二"}, "三", "二",
    )
    assert d == "manual"


def test_gate_manual_on_is_match_false():
    d, _ = _ver._gate_arbitration(
        {"is_match": False, "confidence": 0.95, "real_char": "二"}, "三", "二",
    )
    assert d == "manual"


# ── arbitrate_divergence：未配置 / 无图像早退（不调网络）──
def test_arbitrate_not_configured():
    va = VisionRecheckAdapter()  # 无 key/base/model
    div = Divergence(page_no=1, a_seg="三", b_seg="二")
    arb = va.arbitrate_divergence(div, None)
    assert arb.decision == "manual"
    assert "not_configured" in arb.raw


def test_arbitrate_no_image():
    va = VisionRecheckAdapter(api_key="k", base_url="http://x", model="m")
    div = Divergence(page_no=1, a_seg="三", b_seg="二")
    arb = va.arbitrate_divergence(div, None)
    assert arb.decision == "manual"
    assert arb.raw == "vision_recheck_no_image"


# ── arbitrate_divergence：degraded 模式（boxes 为空）──
def _va_with_mock_response(response: str) -> VisionRecheckAdapter:
    va = VisionRecheckAdapter(api_key="k", base_url="http://x", model="m")
    va._post_vl = lambda prompt, b64: response  # 注入假 VL 响应，避免网络
    return va


def test_arbitrate_degraded_accepted_a():
    va = _va_with_mock_response('{"is_match": true, "confidence": 0.9, "real_char": "三"}')
    div = Divergence(page_no=1, a_seg="三", b_seg="二", a_context="黄【三】两")
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    arb = va.arbitrate_divergence(div, img)
    assert arb.mode == "degraded"
    assert arb.decision == "accepted_a"
    assert arb.engine == "m"


def test_arbitrate_degraded_manual_on_parse_fail():
    va = _va_with_mock_response("模型胡言乱语不是json")
    div = Divergence(page_no=1, a_seg="三", b_seg="二")
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    arb = va.arbitrate_divergence(div, img)
    assert arb.decision == "manual"


# ── arbitrate_divergence：box_guided 模式（boxes 非空精确裁框）──
def test_arbitrate_box_guided_accepted_b():
    va = _va_with_mock_response('{"is_match": true, "confidence": 0.9, "real_char": "二"}')
    div = Divergence(page_no=2, a_seg="三", b_seg="二", boxes=[[10, 10, 40, 40]])
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    arb = va.arbitrate_divergence(div, img)
    assert arb.mode == "box_guided"
    assert arb.decision == "accepted_b"


def test_arbitrate_box_multi_char_skip():
    va = _va_with_mock_response("{}")
    div = Divergence(page_no=2, a_seg="三", b_seg="二", boxes=[[10, 10, 40, 40], [50, 50, 60, 60]])
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    arb = va.arbitrate_divergence(div, img)
    assert arb.decision == "manual"
    assert arb.raw == "box_multi_char_skip"


def test_arbitrate_box_too_small_skip():
    va = _va_with_mock_response("{}")
    div = Divergence(page_no=2, a_seg="三", b_seg="二", boxes=[[10, 10, 12, 12]])
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    arb = va.arbitrate_divergence(div, img)
    assert arb.decision == "manual"
    assert arb.raw == "box_too_small_skip"


# ── db：update_cross_divergence_status roundtrip ──
def test_update_cross_divergence_status(tmp_path):
    db = BookDB("bkdb", db_dir=str(tmp_path))
    divs = [Divergence(page_no=3, div_type="replace", a_seg="三", b_seg="二", priority="high")]
    db.write_cross_divergences(3, divs, engine_a="t1", engine_b="t3")
    n = db.update_cross_divergence_status(3, "replace", "三", "二", "accepted_a")
    assert n == 1
    rows = db.get_cross_divergences(page_no=3)
    assert rows[0]["status"] == "accepted_a"
    db.close()
