"""cv2 降级路径版心裁剪函数的单元测试（不依赖真实引擎/模型）。

覆盖 _compute_blocks（左侧竖边框线跳过 + 无墨行守卫）、
_body_left_user（用户差值公式）、_body_right_even（偶数页右界）、
_body_top_bottom（页眉页脚检测）。
"""
from __future__ import annotations

import numpy as np

from kzocr.engine import layout_crop as lc


def _white(h: int, w: int) -> np.ndarray:
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_body_left_user_empty():
    assert lc._body_left_user([], calib=0) == 0


def test_body_left_user_formula():
    # 两个正文块 x1=100/105，一个左侧窄侧眉 x1=5(<=15 排除)
    blocks = [(100, 0, 500, 10, 400), (105, 20, 500, 30, 395), (5, 40, 40, 50, 35)]
    # m_all=(100+105+5)/3=70; m_body=(100+105)/2=102.5;
    # (102.5-70)/2-15+calib = 1.25+calib
    assert lc._body_left_user(blocks, calib=0) == 1
    assert lc._body_left_user(blocks, calib=105) == 106


def test_body_left_user_all_excluded_falls_back():
    # 所有 x1<=15 → body 空，退化为 m_body=m_all，差值 0
    blocks = [(5, 0, 40, 10, 35), (10, 20, 40, 30, 30)]
    # (0)/2-15+calib = -15+calib
    assert lc._body_left_user(blocks, calib=75) == 60


def test_body_right_even_empty_returns_w():
    assert lc._body_right_even([], w=1000) == 1000


def test_body_right_even_midpoint():
    # M=1410(含右侧边栏)，正文最右 X=1350(在 M-40 内)，pad=28
    blocks = [(100, 0, 1350, 10, 1250), (100, 20, 1410, 30, 1310)]
    # right = 1410 - (1410-1350)/2 - 28 = 1410 - 30 - 28 = 1352
    assert lc._body_right_even(blocks, w=2000) == 1352


def test_body_right_even_no_gap_falls_back_to_m():
    # 所有块右缘都接近 M → body 空 → X=M → right=M-pad
    blocks = [(100, 0, 1410, 10, 1310), (100, 20, 1410, 30, 1310)]
    assert lc._body_right_even(blocks, w=2000) == 1410 - 28


def test_body_top_bottom_no_header_footer():
    h = 1000
    # 所有块均不在顶部 15%(y<150) 或底部 15%(y>850) 区域，且高度均<=30
    blocks = [(50, 100, 500, 120, 450), (50, 200, 500, 220, 450), (50, 800, 500, 820, 450)]
    top, bottom = lc._body_top_bottom(blocks, h)
    # 无页眉页脚 → top=首块 y1-15, bottom=末块 y2+15
    assert top == 100 - 15
    assert bottom == 820 + 15


def test_body_top_bottom_includes_header_undercrop():
    h = 1000
    # 首块在顶部 15%(y<150) 且高度>30，形似页眉；但为「宁欠裁不过裁」，
    # 不把它排除，top 直接取首块上缘上移 padding（含页眉，安全）
    blocks = [(50, 50, 500, 90, 450), (50, 100, 500, 120, 450), (50, 300, 500, 320, 450)]
    top, bottom = lc._body_top_bottom(blocks, h)
    assert top == 50 - 15  # 首块 y1-15，不向下推
    assert bottom == 320 + 15  # 末块 y2+15，不向上提


def test_compute_blocks_skips_left_frame_line():
    h, w = 60, 80
    img = _white(h, w)
    img[:, 0:4] = 0            # 左侧贯穿竖边框线 x=0..3
    img[20:30, 30:70] = 0      # 正文行
    blocks = lc._compute_blocks(img, [(0, 20, 80, 30)], w)
    assert blocks, "应检出正文块"
    lx = blocks[0][0]
    assert lx >= 28, f"trim_left 应跳过竖框线, 实际 lx={lx}"


def test_compute_blocks_skips_inkless_line():
    h, w = 60, 80
    img = _white(h, w)         # 全白，无墨
    blocks = lc._compute_blocks(img, [(0, 20, 80, 30)], w)
    assert blocks == [], "无墨行不应产出伪全宽块"
