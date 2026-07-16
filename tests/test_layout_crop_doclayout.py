"""PP-DocLayoutV3 版心裁剪后端测试（mock paddlex，不依赖真实模型）。

覆盖：
- crop_by_doclayout 取 text/vertical_text/标题 并集 + padding(15/15/10)，排除侧眉/页眉/页码
- 无正文框 / 模型不可用 → 返回 None
- crop_by_layout 优先 doclayout；模型不可用时降级 cv2
外部依赖（paddlex/cv2）均 mock 或 importorskip，CI 可跑。
"""

from __future__ import annotations

from unittest import mock

import numpy as np

from kzocr.engine import layout_crop
from kzocr.engine.layout_crop import crop_by_doclayout, crop_by_layout


class _FakeResult:
    def __init__(self, boxes):
        self._boxes = boxes

    @property
    def json(self):
        return {"res": {"boxes": self._boxes}}


class _FakeModel:
    def __init__(self, boxes):
        self._boxes = boxes

    def predict(self, img, batch_size=1):
        return [_FakeResult(self._boxes)]


def _box(label, x1, y1, x2, y2):
    return {"label": label, "coordinate": [x1, y1, x2, y2], "score": 0.9}


def _make_img(h, w):
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_crop_by_doclayout_union_with_padding():
    img = _make_img(1000, 800)
    boxes = [
        _box("text", 100, 200, 600, 300),
        _box("vertical_text", 120, 700, 620, 850),
        _box("aside_text", 10, 200, 60, 850),   # 侧眉，排除
        _box("header", 100, 50, 600, 90),        # 页眉，排除
        _box("number", 700, 950, 750, 980),      # 页码，排除
    ]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)):
        out = crop_by_doclayout(img)
    assert out is not None
    # 左边界 = 最左候选(正文 min x1=100 -15=85)；侧眉(aside_text)不在 _BODY_LABELS 已排除，
    # 不再用 margin_x_min-20，下限 0 → left=85（侧眉 x[10,60] 被裁掉）
    # body 并集 y∈[200,850]; padding 左右上 15 / 下 10
    left, top = 85, 200 - 15
    right, bottom = 620 + 15, 850 + 10
    assert out.shape[1] == right - left
    assert out.shape[0] == bottom - top


def test_crop_by_doclayout_right_margin_does_not_push_left():
    """回归测试：右侧边栏(aside_text x 很大)不应把 left 推到右侧导致偶数页过裁。

    修复前 left = max(0, max(left_candidates))，右侧边栏 x≈1300 会使 left≈1280，
    偶数页左界被推到 ~1100px（实测 crop 宽度仅 461 远低于奇数页 1157）。
    """
    img = _make_img(1000, 1500)
    boxes = [
        _box("text", 100, 200, 620, 850),
        _box("aside_text", 1300, 200, 1410, 850),  # 右侧边栏，x 很大
    ]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)):
        out = crop_by_doclayout(img)
    assert out is not None
    # 最左候选 = min(正文 x1 -15=85, 窄正文 x1 -20=80) → left=80（不是 1280）
    assert out.shape[1] == (620 + 15) - 80


def test_crop_by_doclayout_trims_left_side_eyebrow():
    """dl 左界应排除左侧竖眉(aside_text)，且不再被 120 下限兜死。"""
    img = _make_img(2055, 1430)
    boxes = [
        _box("text", 186, 300, 1280, 400),     # 正文（最左 x1=186）
        _box("aside_text", 50, 307, 92, 885),  # 左侧竖眉 x[50,92]
        _box("text", 188, 900, 1280, 1000),
    ]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)):
        out = crop_by_doclayout(img)
    assert out is not None
    # min(正文 x1)=186 → left=186-15=171；侧眉(aside_text)已排除，右缘 92 < 171 → 被裁
    left = 171
    assert out.shape[1] == (1280 + 15) - left
    assert left > 92, "左侧竖眉(50-92)应被裁掉"
    assert left != 120, "不应再被 120 下限兜死"


def test_crop_by_doclayout_no_body_returns_none():
    img = _make_img(500, 500)
    boxes = [_box("aside_text", 10, 10, 60, 60), _box("number", 400, 450, 450, 470)]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)):
        assert crop_by_doclayout(img) is None


def test_crop_by_doclayout_model_unavailable_returns_none():
    img = _make_img(500, 500)
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=None):
        assert crop_by_doclayout(img) is None


def test_crop_by_layout_prefers_doclayout():
    img = _make_img(1000, 800)
    boxes = [_box("text", 100, 200, 600, 300), _box("vertical_text", 120, 700, 620, 850)]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)):
        out = crop_by_layout(img, padding=10, page_num=1)
    assert out is not None
    # 左边界 = 最左候选(正文min-15=85)，下限 0 → left=85
    assert out.shape[1] == (620 + 15) - 85


def test_crop_by_layout_falls_back_to_cv2_when_model_none():
    import pytest

    pytest.importorskip("cv2")
    img = _make_img(1400, 1000)
    # 三条宽行（>整页一半），模拟正文
    for y in (400, 500, 600):
        img[y : y + 30, 200:760] = 0
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=None):
        out = crop_by_layout(img, padding=10, page_num=1)
    assert out is not None


def test_crop_by_layout_doclayout_then_no_cv2_fallback_none():
    """doclayout 无正文框返回 None，且 cv2 也无文字行时整体返回 None（纯白图，不依赖 cv2）。"""
    img = _make_img(400, 400)
    boxes = [_box("aside_text", 10, 10, 30, 30)]
    with mock.patch.object(layout_crop, "_get_doclayout_model", return_value=_FakeModel(boxes)), \
         mock.patch.object(layout_crop, "_detect_text_lines", return_value=[]):
        assert crop_by_layout(img, padding=10, page_num=1) is None
