"""版心裁剪左右边界(侧眉裁切)回归测试。

规则(用户需求 + 绝对阈值宽行判定 bw > w*0.5)：
- 奇数页(侧眉在左)：左边界 = 宽行左边界最小值 - 20px，右边不裁。
- 偶数页(侧眉在右)：右边界 = 宽行右边界最大值 + 20px，左边不裁。
宽行 = 行宽超过整页一半(> w*0.5)的正文行；侧眉窄列不达标被排除。

注：构造图时 img[y1:y2, x1:x2]=0 为右开区间，故检测到的右边界为 x2-1。
"""

from __future__ import annotations

import numpy as np

from kzocr.engine.layout_crop import _find_body_boundaries


def _make_page(h: int, w: int, lines: list[tuple[int, int, int, int]]) -> np.ndarray:
    """构造白底 RGB 图，并在 lines 指定的矩形区域涂黑(模拟文字)。"""
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    for x1, y1, x2, y2 in lines:
        img[y1:y2, x1:x2] = 0
    return img


def test_odd_page_left_boundary_from_wide_lines():
    """奇数页：左边界取宽行左边界最小值再左移 20px，右边不裁。"""
    h, w = 1400, 1000
    # 正文宽行：宽 560 > 500(整页一半)，左边界 200、右边界 760
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    side = [(40, 100, 80, 300)]  # 窄侧眉列(宽 40)，应被宽行判定排除
    img = _make_page(h, w, body + side)

    _top, _bottom, left, right = _find_body_boundaries(img, body + side, page_num=1)

    assert left == 200 - 20, f"奇数页左边界应为 180，实际 {left}"
    assert right == w, f"奇数页右边不应裁切，实际 {right}"


def test_even_page_right_boundary_from_wide_lines():
    """偶数页：右边界取宽行右边界最大值再右移 20px，左边不裁。"""
    h, w = 1400, 1000
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    side = [(850, 100, 890, 300)]  # 窄侧眉列(宽 40)，应被宽行判定排除
    img = _make_page(h, w, body + side)

    _top, _bottom, left, right = _find_body_boundaries(img, body + side, page_num=2)

    # 右边界检测为末列 x2-1=759，+20 → 779
    assert right == (760 - 1) + 20, f"偶数页右边界应为 779，实际 {right}"
    assert left == 0, f"偶数页左边不应裁切，实际 {left}"


def test_narrow_side_margin_excluded_from_wide():
    """窄侧眉列不得污染宽行边界：偶数页右边界应取正文 760 而非侧眉 890。"""
    h, w = 1400, 1000
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    side = [(850, 100, 890, 300)]  # 右缘窄侧眉，右边界 889
    img = _make_page(h, w, body + side)

    _top, _bottom, _left, right = _find_body_boundaries(img, body + side, page_num=2)

    # 若侧眉被误纳入宽行，right 会逼近 889+20；正确应为 779
    assert right < 850, f"窄侧眉不应污染右边界，实际 {right}"
    assert right == (760 - 1) + 20


def test_no_wide_lines_fallback_odd_and_even():
    """无宽行(均 < w*0.5)时退化兜底：奇数取最小左边界-20，偶数取最大右边界+20。"""
    h, w = 1400, 1000
    # 全部行宽 400 < 500，无宽行
    lines = [(300, y, 700, y + 30) for y in range(400, 1200, 100)]
    img = _make_page(h, w, lines)

    _t, _b, left_odd, right_odd = _find_body_boundaries(img, lines, page_num=1)
    assert left_odd == 300 - 20, f"兜底(奇)左边界应为 280，实际 {left_odd}"
    assert right_odd == w, f"兜底(奇)右边不裁，实际 {right_odd}"

    _t, _b, left_even, right_even = _find_body_boundaries(img, lines, page_num=2)
    assert right_even == (700 - 1) + 20, f"兜底(偶)右边界应为 719，实际 {right_even}"
    assert left_even == 0, f"兜底(偶)左边不裁，实际 {left_even}"
