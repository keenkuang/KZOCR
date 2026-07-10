"""验证器性能基准：GlyphVerifier.verify 耗时断言。"""

from __future__ import annotations

import time

from kzocr.scheduler.verifier import (
    DetectorContext,
    GlyphVerifier,
)


def test_verify_plain_text_performance():
    """verify 纯文本应在 50ms 内完成。"""
    ver = GlyphVerifier()
    ctx = DetectorContext(page_num=1)
    t0 = time.monotonic()
    for _ in range(50):
        ver.verify("黄芪补气，方用甘草、大枣调和诸药。", ctx)
    elapsed = (time.monotonic() - t0) / 50
    assert elapsed < 0.05, f"verify avg {elapsed*1000:.1f}ms > 50ms"


def test_verify_toxic_text_performance():
    """含毒性药材的文本应在 50ms 内完成。"""
    ver = GlyphVerifier()
    ctx = DetectorContext(page_num=1)
    t0 = time.monotonic()
    for _ in range(50):
        ver.verify("附子 30g 先煎，乌头 15g，半夏 12g。", ctx)
    elapsed = (time.monotonic() - t0) / 50
    assert elapsed < 0.05, f"verify toxic avg {elapsed*1000:.1f}ms > 50ms"


def test_verify_long_text_performance():
    """长文本（1000 字）应在 200ms 内完成。"""
    ver = GlyphVerifier()
    ctx = DetectorContext(page_num=1)
    long_text = "甘草、大枣、生姜、桂枝、芍药。" * 100
    t0 = time.monotonic()
    for _ in range(10):
        ver.verify(long_text, ctx)
    elapsed = (time.monotonic() - t0) / 10
    assert elapsed < 0.2, f"verify long text avg {elapsed*1000:.1f}ms > 200ms"
