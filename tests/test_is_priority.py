"""cross_align._is_priority 纯逻辑单测（无引擎/网络依赖）。

``_is_priority`` 决定跨引擎分歧优先级：P0（数字/剂量分歧）、P1（形近字黑名单命中）、
normal（其他）。直接影响哪些分歧送视觉仲裁 / 人工复核。
"""
from __future__ import annotations

from kzocr.scheduler.cross_align import _is_priority


def test_arabic_digit_is_priority():
    assert _is_priority("6", "5", None) == "P0"
    assert _is_priority("黄芪3", "黄芪5", None) == "P0"


def test_cn_digit_is_priority():
    assert _is_priority("二", "三", None) == "P0"
    assert _is_priority("十五", "五十", None) == "P0"


def test_confusion_hit_is_priority():
    cs = {"芩": "苓", "炙": "灸"}
    assert _is_priority("芩", "苓", cs) == "P1"


def test_confusion_miss_not_priority():
    cs = {"芩": "苓"}
    # 黄↔皇 不在黑名单，且非数字 → normal
    assert _is_priority("黄", "皇", cs) == "normal"


def test_plain_multi_char_not_priority():
    assert _is_priority("黄芪", "黄耆", None) == "normal"
    assert _is_priority("甲", "乙", {"芩": "苓"}) == "normal"


def test_empty_segments_not_priority():
    assert _is_priority("", "", None) == "normal"
    assert _is_priority("黄芪", "黄耆", {}) == "normal"
