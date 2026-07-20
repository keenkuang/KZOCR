"""C1: 跨页泄漏检测测试。"""
from __future__ import annotations

from kzocr.engines.leakage import (
    CharCountBaseline,
    LeakageDetector,
    apply_leakage_defense,
)


class TestCharCountBaseline:
    def test_feed_baseline_computed(self):
        bl = CharCountBaseline(window=5)
        for i in range(5):
            bl.feed("x" * (100 + i * 10))
        assert bl.ready is True
        # 中位数: 已排序 [100, 110, 120, 130, 140] → 120
        assert bl.baseline == 120.0

    def test_threshold(self):
        bl = CharCountBaseline(window=3)
        for i in range(3):
            bl.feed("x" * 100)
        assert bl.threshold == 150.0  # 100 * 1.5

    def test_not_ready(self):
        bl = CharCountBaseline(window=5)
        bl.feed("x" * 100)
        assert bl.ready is False
        assert bl.threshold is None

    def test_feed_after_ready_no_change(self):
        bl = CharCountBaseline(window=3)
        for i in range(3):
            bl.feed("x" * 100)
        bl.feed("x" * 9999)  # 不应改变已锁定的基线
        assert bl.baseline == 100.0


class TestLeakageDetector:
    def test_no_leak(self):
        """不相关页面 → 无泄漏。"""
        a = "这是第一页的内容。主要介绍了感冒的辨证论治方法。"
        b = "第二页讲的是咳嗽的方剂。杏苏散适用于风寒咳嗽。"
        assert LeakageDetector.detect(a, b, min_probe=5, max_probe=20, step=5) is None

    def test_detect_leak(self):
        """page_b 的开头文字完整出现在 page_a 的中后部 → 检出。"""
        prefix = "第一页正文。方用白术三钱。方用甘草二钱。方用茯苓四钱。"
        leak_content = "鳖甲消瘤方用药物。牡蛎30克、玄参15克。"
        tail = "其他内容。" * 3
        page_a = prefix + leak_content + tail
        page_b = leak_content + "贝母12克。"
        result = LeakageDetector.detect(page_a, page_b, min_probe=8, max_probe=20, step=4, min_overlap_pct=0.30)
        assert result is not None
        assert result > 0

    def test_skip_early_overlap(self):
        """重叠位置 <30% → 不判为泄漏（自然跨页接续）。"""
        a = "第一页内容。" + "重复文本。" * 3 + "第二页方药内容。"
        b = "重复文本。这是一种常见的描述。"
        result = LeakageDetector.detect(a, b, min_probe=5, max_probe=20, step=5, min_overlap_pct=0.30)
        # 重叠在开头 → 不应判为泄漏
        assert result is None

    def test_trim_at_leak(self):
        text = "正常内容。这是正常的第一页正文。第二页泄漏的内容。第三页更多泄漏。"
        trimmed = LeakageDetector.trim(text, 15)
        assert "正常内容" in trimmed
        assert "第二页泄漏" not in trimmed

    def test_trim_at_zero(self):
        text = "测试文本"
        assert LeakageDetector.trim(text, 0) == text

    def test_empty_pages(self):
        assert LeakageDetector.detect("", "content") is None
        assert LeakageDetector.detect("content", "") is None


class TestApplyDefense:
    def test_under_threshold_unchanged(self):
        bl = CharCountBaseline(window=3)
        pages = ["page1", "page2", "page3"]
        for p in pages:
            bl.feed(p)
        result = apply_leakage_defense(pages, bl)
        assert result == pages

    def test_l4_leak_detected(self):
        """泄漏内容被截断（泄漏文本 ≥50 字满足默认 min_probe）。"""
        bl = CharCountBaseline(window=3)
        prefix = "第一页正文。方用白术三钱。方用甘草二钱。" * 3
        leak = "第二页泄漏内容。" * 8  # 64 chars, >= min_probe=50
        a = prefix + leak
        b = leak + "后续正文的其他内容。"
        pages = [a, b, "第三页内容。"]
        for p in ["x" * 100, "y" * 100, "z" * 100]:
            bl.feed(p)
        result = apply_leakage_defense(pages, bl, max_tokens=2048)
        assert len(result[0]) < len(a)

    def test_empty_input(self):
        assert apply_leakage_defense([], CharCountBaseline()) == []

    def test_single_page_no_change(self):
        bl = CharCountBaseline(window=3)
        bl.feed("x" * 100)
        bl.feed("y" * 100)
        bl.feed("z" * 100)
        result = apply_leakage_defense(["单独一页"], bl)
        assert result == ["单独一页"]


class TestLeakageEdgeBranches:
    """覆盖探针/规范化/四层判定中的边界分支（覆盖率查漏）。"""

    def test_is_excluded_true(self):
        """探针由排除词组成且较短 → 判为排除（line 85）。"""
        assert LeakageDetector._is_excluded("组成") is True

    def test_is_excluded_false(self):
        """长文本即使含排除词也不排除。"""
        assert LeakageDetector._is_excluded("组成方用白术三钱茯苓") is False

    def test_detect_whitespace_only_b(self):
        """text_b 全空白 → 规范化后为空 → 直接返回 None（line 117-118）。"""
        assert LeakageDetector.detect("正文内容。", "   ", min_probe=2) is None

    def test_detect_excluded_probe_skipped(self):
        """page_b 前缀为排除词 → 探针被排除不误报（line 127）。"""
        a = "第一页正文内容。" * 5
        b = "组成方后续描述。"  # 前缀「组成」属排除词
        assert LeakageDetector.detect(
            a, b, min_probe=2, max_probe=10, step=1, min_overlap_pct=0.1
        ) is None

    def test_apply_l1_over_threshold_logged(self):
        """L1：页面超基线阈值 → 触发告警分支（line 179-183），不崩溃。"""
        bl = CharCountBaseline(window=3)
        for p in ["x" * 100, "y" * 100, "z" * 100]:
            bl.feed(p)
        pages = ["a" * 200, "b" * 100, "c" * 100]  # 200 > threshold 150
        result = apply_leakage_defense(pages, bl, max_tokens=2048)
        assert len(result) == 3

    def test_apply_l2_over_maxtokens_logged(self):
        """L2：页面超 max_tokens*2 → 触发告警分支（line 185-189）。"""
        bl = CharCountBaseline(window=3)
        for p in ["x" * 100, "y" * 100, "z" * 100]:
            bl.feed(p)
        pages = ["a" * 5000]  # 5000 > 2048*2=4096
        result = apply_leakage_defense(pages, bl, max_tokens=2048)
        assert result == pages

    def test_apply_skips_empty_adjacent(self):
        """L4 相邻空页 → 跳过该对（line 196 分支）。"""
        bl = CharCountBaseline(window=3)
        for p in ["x" * 100, "y" * 100, "z" * 100]:
            bl.feed(p)
        result = apply_leakage_defense(["a" * 100, "", "c" * 100], bl)
        assert result[1] == ""
