"""E3 GlyphVerifier 测试（v0.7 §11.4）。

覆盖：各检测器命中/未命中、资源缺失 disable、短路（PASS / critical FAIL
立即返回）、聚合（RARE/UNKNOWN/FAIL 组合），以及 B6/B7 资源归一化与
"正确"条目跳过。
"""

from __future__ import annotations

from kzocr.engine.types import GlyphVerdict
from kzocr.scheduler.verifier import (
    CharCountSpikeDetector,
    ConfusionKeyPresenceDetector,
    ConfusionSetDetector,
    DetectorContext,
    GlyphVerifier,
    HerbEntry,
    LeakageDetector,
    TermKBMatcher,
    ToxinDoseDetector,
)


def _ctx(**kw) -> DetectorContext:
    return DetectorContext(page_num=1, resources=kw)


# ── ToxinDoseDetector ──
def test_toxin_dose_hit_over_limit():
    det = ToxinDoseDetector({"附子": HerbEntry("附子", max_dosage_g=15.0)})
    v = det.check("附子 20g 先煎", _ctx())
    assert v is not None and v.status == "FAIL"
    assert "severity=critical" in v.details


def test_toxin_dose_within_limit():
    det = ToxinDoseDetector({"附子": HerbEntry("附子", max_dosage_g=15.0)})
    assert det.check("附子 10g", _ctx()) is None


def test_toxin_dose_unit_conversion():
    # 1两 ≈ 30g > 15g 上限
    det = ToxinDoseDetector({"附子": HerbEntry("附子", max_dosage_g=15.0)})
    v = det.check("附子 1两", _ctx())
    assert v is not None and v.status == "FAIL"
    # 5钱 = 15g，不超上限（边界）
    assert det.check("附子 5钱", _ctx()) is None


def test_toxin_dose_no_false_match_on_compound():
    # "附子汤" 不应误匹配 "附子"（药名后须紧跟剂量）
    det = ToxinDoseDetector({"附子": HerbEntry("附子", max_dosage_g=15.0)})
    assert det.check("附子汤主治", _ctx()) is None


def test_toxin_dose_disabled_when_no_db():
    assert ToxinDoseDetector(None).enabled is False


# ── LeakageDetector ──
def test_leakage_detected():
    det = LeakageDetector()
    # 下一页文本（>=50 字符）泄漏到当前页尾部，且泄漏起点 > 30% 页长
    next_text = "L" * 60
    cur_text = "N" * 200 + next_text
    v = det.check(cur_text, _ctx(next_page_text=next_text))
    assert v is not None and v.status == "FAIL"


def test_leakage_no_next_page():
    det = LeakageDetector()
    assert det.check("当前页文本", _ctx()) is None


# ── CharCountSpikeDetector ──
def test_char_count_spike():
    det = CharCountSpikeDetector(multiplier=3.0)
    ctx = _ctx(neighbor_texts=["x" * 100, "y" * 100])
    assert det.check("z" * 400, ctx).status == "UNCERTAIN"
    assert det.check("z" * 150, ctx) is None


def test_char_count_spike_needs_neighbors():
    det = CharCountSpikeDetector()
    assert det.check("z" * 1000, _ctx(neighbor_texts=["x"])) is None


# ── ConfusionSetDetector ──
def test_confusion_hit():
    det = ConfusionSetDetector({"我术": "莪术"})
    v = det.check("误作我术", _ctx())
    assert v is not None and v.status == "RARE" and v.force_review is True


def test_confusion_loader_skips_correct_entries():
    # B7：_init_detectors 加载时应跳过 wrong==correct / category=='正确' 的条目
    ver = GlyphVerifier()
    det = next(d for d in ver.detectors if d.name == "ConfusionSetDetector")
    # 加载后的混淆表不应含"自身等于自身"的条目
    assert all(wrong != correct for wrong, correct in det.confusion_set.items())


def test_confusion_resource_filters_correct_entries():
    # 真实资源含 category=='正确' 条目，加载后不应导致正常文本判 UNKNOWN（阻断）
    ver = GlyphVerifier()
    det = next(d for d in ver.detectors if d.name == "ConfusionSetDetector")
    assert det.enabled  # 资源存在应启用
    # 构造一条"正确"条目词（如"黄芪"在资源中 category 正确）不应被阻断（UNKNOWN）
    # 注：双向拦截改由 ConfusionKeyPresence（非阻断 force_review）承担，
    # 此处仅保证 ConfusionSetDetector 不对正确文本判 UNKNOWN。
    ctx = _ctx()
    v = det.check("黄芪常用于补益剂", ctx)
    assert v is None or v.status != "UNKNOWN"


# ── ConfusionKeyPresenceDetector 分侧强弱标 ──
def test_confusion_key_presence_wrong_side_strong():
    """wrong 侧一级高危字命中 → 强标 (confidence=0.55)。"""
    det = ConfusionKeyPresenceDetector(
        {"补": "一级高危", "炙": "一级高危"},
        correct_keys={},
    )
    v = det.check("补气", _ctx())
    assert v is not None and v.status == "RARE"
    assert v.force_review is True
    assert v.confidence == 0.55
    assert "confusion_key_wrong" in v.details


def test_confusion_key_presence_correct_only_side_weak():
    """correct-only 一级高危字（不在 wrong 侧）命中 → 弱标 (confidence=0.35)。"""
    det = ConfusionKeyPresenceDetector(
        {"补": "一级高危"},  # wrong 侧
        correct_keys={"朴": "一级高危"},  # correct-only：朴不在 wrong 侧
    )
    v = det.check("朴硝", _ctx())
    assert v is not None and v.status == "RARE"
    assert v.force_review is True
    assert v.confidence == 0.35
    assert "confusion_key_correct" in v.details


def test_confusion_key_presence_bidirectional_gets_strong():
    """双向字符（同时在 wrong 和 correct 侧）→ wrong 侧优先，强标。"""
    det = ConfusionKeyPresenceDetector(
        {"补": "一级高危"},  # wrong 侧
        correct_keys={"补": "一级高危"},  # correct 侧也有"补"
    )
    v = det.check("补气", _ctx())
    assert v is not None and v.confidence == 0.55
    assert "confusion_key_wrong" in v.details


def test_confusion_key_presence_no_hit():
    """无命中 → None。"""
    det = ConfusionKeyPresenceDetector(
        {"日": "三级通用"},
        correct_keys={},
    )
    assert det.check("补气", _ctx()) is None


def test_confusion_key_presence_empty_text():
    """空文本 → None。"""
    det = ConfusionKeyPresenceDetector({"补": "一级高危"})
    assert det.check("", _ctx()) is None


# ── TermKBMatcher ──
def test_termkb_rare_allowlist_pass():
    det = TermKBMatcher(rare_allowlist={"萆薢"})
    v = det.check("方用萆薢分清饮", _ctx())
    assert v is not None and v.status == "PASS"


def test_termkb_variant_rare():
    det = TermKBMatcher(variant_map={"黄": ["黃"]})
    v = det.check("古本作黃芪", _ctx())
    assert v is not None and v.status == "RARE"


def test_termkb_disabled_when_empty():
    assert TermKBMatcher(None, None).enabled is False


# ── GlyphVerifier 短路与聚合 ──
class _FixedDet:
    """测试用固定裁决检测器。"""

    def __init__(self, name: str, priority: int, verdict: GlyphVerdict | None, enabled: bool = True):
        self.name = name
        self.priority = priority
        self._verdict = verdict
        self.enabled = enabled

    def check(self, text, context):
        return self._verdict


def test_verifier_all_pass():
    v = GlyphVerdict(status="PASS", confidence=1.0)
    ver = GlyphVerifier([_FixedDet("A", 10, v), _FixedDet("B", 20, v)])
    assert ver.verify("t", _ctx()).status == "PASS"


def test_verifier_rare_only():
    ver = GlyphVerifier(
        [_FixedDet("A", 10, GlyphVerdict(status="RARE", confidence=0.7))]
    )
    assert ver.verify("t", _ctx()).status == "RARE"


def test_verifier_unknown_only():
    ver = GlyphVerifier(
        [_FixedDet("A", 10, GlyphVerdict(status="UNKNOWN", confidence=0.6))]
    )
    assert ver.verify("t", _ctx()).status == "UNKNOWN"


def test_verifier_noncritical_fail_aggregates_unknown():
    # 非 critical FAIL 不短路，聚合为 UNKNOWN（留给编排循环降级）
    ver = GlyphVerifier(
        [_FixedDet("A", 10, GlyphVerdict(status="FAIL", confidence=0.0, details="x"))]
    )
    out = ver.verify("t", _ctx())
    assert out.status == "UNKNOWN" and "has_fail=True" in out.details


def test_verifier_pass_short_circuits():
    pass_v = GlyphVerdict(status="PASS", confidence=0.9)
    fail_v = GlyphVerdict(status="FAIL", confidence=0.0, details="x")
    # PASS 优先级低（50），但应短路立即返回，不走到 FAIL
    ver = GlyphVerifier(
        [
            _FixedDet("high_prio", 10, fail_v),
            _FixedDet("low_prio_pass", 50, pass_v),
        ]
    )
    out = ver.verify("t", _ctx())
    assert out.status == "PASS"
    assert ver.last_detector_chain == ["high_prio", "low_prio_pass"]


def test_verifier_critical_fail_short_circuits():
    crit = GlyphVerdict(status="FAIL", confidence=1.0, details="severity=critical")
    ver = GlyphVerifier([_FixedDet("A", 10, crit)])
    out = ver.verify("t", _ctx())
    assert out.status == "FAIL" and "severity=critical" in out.details


def test_verifier_disabled_detectors_filtered():
    # 注入一个 enabled=False 的检测器 + 一个 PASS，应只用 PASS
    pass_v = GlyphVerdict(status="PASS", confidence=0.9)
    ver = GlyphVerifier(
        [_FixedDet("off", 10, None, enabled=False), _FixedDet("on", 20, pass_v)]
    )
    assert [d.name for d in ver.detectors] == ["on"]


def test_verifier_real_resource_verify_runs():
    # 真实资源加载的 verify 可正常执行（不含危险性文本时应 PASS/RARE）
    ver = GlyphVerifier()
    # 检测器链：毒剂/泄漏/字符尖峰/形近集/基准字前置/词组/术语库
    expected = {
        "ToxinDoseDetector", "LeakageDetector", "CharCountSpikeDetector",
        "ConfusionSetDetector", "ConfusionKeyPresence", "PhraseErrorDetector",
        "TermKBMatcher",
    }
    assert expected.issubset({d.name for d in ver.detectors})
    out = ver.verify("黄芪补气，方用萆薢分清饮", _ctx())
    assert out.status in ("PASS", "RARE", "UNKNOWN")
