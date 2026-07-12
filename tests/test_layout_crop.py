"""版心裁剪左右边界(侧眉裁切)回归测试。

新契约(用户差值公式 + 奇偶对称，doclayout 全量 0 过裁验证)：
- 奇数页(侧眉在左)：left = 用户差值公式(_body_left_user, calib 奇=105)，right 保留整宽。
- 偶数页(侧眉在右)：left = 用户差值公式(calib 偶=75)，right = _body_right_even
  （排除右侧边栏取中点 - pad；无边栏回退整宽，不过裁正文）。
- top/bottom：_body_top_bottom 取首尾块上下缘 ± padding（宁欠裁包含页眉/页脚，也不过裁）。

用户公式: left = (mean(x1|x1>15) - mean(x1 全部)) / 2 - 15 + calib
典型单左竖眉页：侧眉 x1≈40、正文 x1≈200，二者均 >15 → diff=0 → left = calib-15
(奇 105-15=90；偶 75-15=60)。构造图时 img[y1:y2, x1:x2]=0 为右开区间，
检测右边界为 x2-1。
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


def test_odd_page_left_boundary_from_user_formula():
    """奇数页：左边界取用户差值公式(单左竖眉 → left=calib-15=90)，右边不裁。"""
    h, w = 1400, 1000
    # 正文行：左边界 200、右边界 760
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    # 左侧竖眉窄列(左边界 40，y 置于正文之后保持块按 y 序)：应被用户公式折半吸收
    side = [(40, 1300, 80, 1340)]
    img = _make_page(h, w, body + side)

    _top, _bottom, left, right = _find_body_boundaries(img, body + side, page_num=1)

    assert left == 105 - 15, f"奇数页左边界应为 90，实际 {left}"
    assert right == w, f"奇数页右边不应裁切，实际 {right}"


def test_even_page_right_boundary_excludes_sidebar():
    """偶数页：右边界取 _body_right_even(排除右侧边栏中点-28)，左边取用户公式。"""
    h, w = 1400, 1000
    # 正文行：左边界 200、右边界 760
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    # 右侧边栏窄列(右边界 889，置于正文之后保持块按 y 序)
    side = [(850, 1300, 890, 1340)]
    img = _make_page(h, w, body + side)

    _top, _bottom, left, right = _find_body_boundaries(img, body + side, page_num=2)

    # M=889, X=759, right = 889 - (889-759)/2 - 28 = 796
    assert right == 796, f"偶数页右边界应为 796，实际 {right}"
    assert left == 75 - 15, f"偶数页左边界应为 60，实际 {left}"


def test_narrow_sidebar_excluded_from_right():
    """窄右侧边栏不得污染右边界：偶数页右边界应取正文中点 796 而非逼近边栏 889。"""
    h, w = 1400, 1000
    body = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    side = [(850, 1300, 890, 1340)]  # 右缘窄边栏，右边界 889
    img = _make_page(h, w, body + side)

    _top, _bottom, _left, right = _find_body_boundaries(img, body + side, page_num=2)

    # 若边栏被误纳入正文，right 会逼近 889-28；正确应为 796
    assert right == 796, f"窄边栏不应污染右边界，实际 {right}"
    assert right < 850, f"右边界应远小于边栏右缘，实际 {right}"


def test_no_side_boundary_fallback_full_width():
    """无边栏时退化兜底：偶数页 right 回退整宽(不过裁正文)，左右仍取用户公式。"""
    h, w = 1400, 1000
    # 全部行宽 560，无侧眉列
    lines = [(200, y, 760, y + 30) for y in range(400, 1200, 100)]
    img = _make_page(h, w, lines)

    _t, _b, left_odd, right_odd = _find_body_boundaries(img, lines, page_num=1)
    assert left_odd == 105 - 15, f"兜底(奇)左边界应为 90，实际 {left_odd}"
    assert right_odd == w, f"兜底(奇)右边不裁，实际 {right_odd}"

    _t, _b, left_even, right_even = _find_body_boundaries(img, lines, page_num=2)
    # 无右侧边栏(gap 内无正文) → 回退整宽，避免切掉正文右缘 28px
    assert right_even == w, f"兜底(偶)无栏应回退整宽，实际 {right_even}"
    assert left_even == 75 - 15, f"兜底(偶)左边界应为 60，实际 {left_even}"
