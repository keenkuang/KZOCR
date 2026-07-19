"""cross_align._is_priority 纯逻辑单测（无引擎/网络依赖）。

``_is_priority`` 决定跨引擎分歧是否标 high 优先级（数字/剂量分歧 + 形近字黑名单命中），
直接驱动哪些分歧送视觉仲裁 / 人工复核。逻辑敏感且无回归保护，补此测试防止回归。
"""
from __future__ import annotations

from kzocr.scheduler.cross_align import _is_priority


def test_arabic_digit_is_priority():
    assert _is_priority("6", "5", None) is True
    assert _is_priority("黄芪3", "黄芪5", None) is True


def test_cn_digit_is_priority():
    assert _is_priority("二", "三", None) is True
    assert _is_priority("十五", "五十", None) is True


def test_confusion_hit_is_priority():
    cs = {"芩": "苓", "炙": "灸"}
    assert _is_priority("芩", "苓", cs) is True


def test_confusion_miss_not_priority():
    cs = {"芩": "苓"}
    # 黄→皇 不在黑名单，且非数字 → 普通分歧
    assert _is_priority("黄", "皇", cs) is False


def test_plain_multi_char_not_priority():
    assert _is_priority("黄芪", "黄耆", None) is False
    assert _is_priority("甲", "乙", {"芩": "苓"}) is False


def test_empty_segments_not_priority():
    assert _is_priority("", "", None) is False
    assert _is_priority("黄芪", "黄耆", {}) is False
