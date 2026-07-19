"""engine/run.py 纯逻辑单测（无 OCR 引擎 / 无真实 PDF 依赖）。

覆盖编排管线核心的零依赖逻辑：
- ``_vlm_postprocess``：VLM 输出文本噪声清理（正则 + 空行压缩）
- ``_merge_cross_page_breaks``：跨页断字/方剂合并（影响正文连续性）
- ``_crop_to_body_fallback``：纯投影法版心裁切（降级路径，纯 numpy）
"""
from __future__ import annotations

import numpy as np

from kzocr.engine.run import (
    _crop_to_body_fallback,
    _merge_cross_page_breaks,
    _vlm_postprocess,
)


# ── _vlm_postprocess ──

def test_postprocess_collapses_excess_blank_lines():
    assert _vlm_postprocess("黄芪\n\n\n\n茯苓") == "黄芪\n\n茯苓"


def test_postprocess_strips_running_header():
    out = _vlm_postprocess("秘方求真 R（卷三）\n黄芪三钱")
    assert "秘方求真" not in out
    assert "黄芪三钱" in out


def test_postprocess_normalizes_parens():
    assert _vlm_postprocess("组成：\\(黄芪\\)") == "组成：(黄芪)"


def test_postprocess_normalizes_field_separator():
    assert _vlm_postprocess("功用♡主治") == "功用：主治"


# ── _merge_cross_page_breaks ──

def test_single_page_unchanged():
    assert _merge_cross_page_breaks(["仅一页"]) == ["仅一页"]


def test_merge_dangling_comma_continuation():
    pages = ["首段。\n黄芪三钱，", "茯苓四两。\n组成：..."]
    out = _merge_cross_page_breaks(pages)
    assert out[0] == "首段。\n黄芪三钱，\n茯苓四两。"
    assert out[1] == "组成：..."


def test_no_merge_when_page_ends_with_period():
    pages = ["黄芪三钱。", "茯苓四两。"]
    assert _merge_cross_page_breaks(pages) == pages


def test_no_merge_when_next_page_has_header():
    pages = ["黄芪三钱，", "治肝痈方\n茯苓四两。"]
    assert _merge_cross_page_breaks(pages) == pages


def test_no_merge_chapter_title():
    pages = ["29 治食管瘤秘方", "茯苓四两。"]
    assert _merge_cross_page_breaks(pages) == pages


# ── _crop_to_body_fallback ──

def test_crop_removes_white_margins():
    h, w = 100, 80
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    img[20:60, 15:50] = 0  # 中央黑块（模拟版心文字）
    out = _crop_to_body_fallback(img, padding=2)
    assert out.shape[0] < h and out.shape[1] < w  # 白边被裁掉
    assert out[out.shape[0] // 2, out.shape[1] // 2].sum() == 0  # 内容保留
