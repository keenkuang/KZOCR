"""跨引擎分歧：BookDB 落库单测 + orchestrator 集成测试（借鉴 ocr_pipeline_v2）。

沿用 tests/test_orchestrator.py 的桩模式（FakeBookAdapter/FakePageAdapter/_reg），
无真实 PDF / 网络依赖。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    BookResult,
    EngineConfig,
    PageInput,
    PageResult,
)
from kzocr.scheduler import orchestrator as _orc
from kzocr.scheduler.orchestrator import orchestrate_book
from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import EngineOverrides
from kzocr.scheduler.cross_align import Divergence
from kzocr.engine.types import GlyphVerdict
from kzocr.storage.db import BookDB


# ── BookDB 落库单测 ──
def test_write_and_get_cross_divergences(tmp_path):
    db = BookDB("bkdb", db_dir=str(tmp_path))
    divs = [
        Divergence(
            page_no=0, div_type="replace", a_seg="三", b_seg="二",
            a_context="【三】", priority="high", engine_a="t1", engine_b="t3",
        ),
        Divergence(page_no=0, div_type="delete", a_seg="", b_seg="", priority="normal"),
    ]
    n = db.write_cross_divergences(0, divs, engine_a="t1", engine_b="t3")
    assert n == 2
    rows = db.get_cross_divergences()
    assert len(rows) == 2
    assert rows[0]["priority"] == "high"
    assert rows[0]["a_seg"] == "三"
    assert rows[0]["engine_a"] == "t1"
    assert rows[0]["engine_b"] == "t3"
    # 按页过滤
    paged = db.get_cross_divergences(page_no=0)
    assert len(paged) == 2
    db.close()


# ── orchestrator 集成测试（复用 test_orchestrator 桩）──
@dataclass
class StubConfig:
    max_pages: int = 50
    total_timeout_s: int = 7200
    max_time_per_page_ms: int = 120000
    allow_cloud_vision: bool = False
    book_type: str = ""
    pub_era: str = ""
    output_dir: str = ""
    trace_dir: str = ""
    db_dir: str = ""


class FakeBookAdapter:
    def __init__(self, pages=None):
        self.pages = pages or []

    def run_book(self, pdf_path):
        return BookResult(book_code="test", title="Test", pages=self.pages)

    def run_page(self, pi):
        raise NotImplementedError


class FakePageAdapter:
    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def run_book(self, pdf_path):
        raise NotImplementedError

    def run_page(self, pi):
        if not self.responses:
            raise RuntimeError("exhausted")
        return self.responses.pop(0)


def _text_pages(*texts):
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _page_result(text):
    return AdapterPageResult(text=text)


def _reg(tier1_pages=None, tier2_texts=None, tier3_texts=None):
    reg = EngineRegistry()
    if tier1_pages is not None:
        reg.register_adapter(
            AdapterMeta(name="t1", label="T1", tier=1, batch_capable=True),
            EngineConfig(), adapter=FakeBookAdapter(tier1_pages),
        )
    if tier2_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t2", label="T2", tier=2, requires_network=True),
            EngineConfig(), adapter=FakePageAdapter([_page_result(t) for t in tier2_texts]),
        )
    if tier3_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t3", label="T3", tier=3),
            EngineConfig(), adapter=FakePageAdapter([_page_result(t) for t in tier3_texts]),
        )
    return reg


def _render_gen(n):
    for i in range(n):
        yield PageInput(page_num=i, img=None)


@pytest.fixture(autouse=True)
def _patch_render(monkeypatch):
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(1))


def _read_db(book_code, db_dir):
    db = BookDB(book_code, db_dir=db_dir)
    rows = db.get_cross_divergences()
    db.close()
    return rows


def test_cross_align_writes_on_tier1_fail_tier3_success(tmp_path):
    """Tier1 触发毒性剂量 FAIL → Tier3 不同文本（剂量数字分歧）→ cross_divergence 落库。

    Tier3 文本含一级高危基准字「附」（附子→附，SPEC#1 强制 M4）：按 Option B 设计，
    文本**照常采纳**，同时打标送 M4 复核队列（force_review，不阻断主流程）。
    """
    reg = _reg(
        tier1_pages=_text_pages("附子20g"),   # ToxinDose FAIL(critical)
        tier3_texts=["附子二钱"],             # 数字/剂量分歧：20g ↔ 二钱
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    result = orchestrate_book("/fp", "bkc1", cfg, reg)
    # Option B：含一级高危字仍采纳文本，仅送 M4 复核
    assert len(result.pages) == 1

    rows = _read_db("bkc1", str(tmp_path))
    assert len(rows) >= 1
    # 剂量数字分歧应标 high
    assert any(r["priority"] == "high" for r in rows)
    # 引擎标签正确
    assert any(r["engine_a"] == "t1" and r["engine_b"] == "t3" for r in rows)

    # 一级高危字 → 强制 M4（force_review 进复核队列，detector=ConfusionKeyPresence）
    db = BookDB("bkc1", db_dir=str(tmp_path))
    anomalies = db.get_anomalies()
    db.close()
    assert any("ConfusionKeyPresence" in (a["detector_chain"] or "") for a in anomalies)


def test_high_priority_divergence_routed_to_review_queue(tmp_path):
    """M4 复核队列规则：high 优先级分歧（数字/剂量）100% 进人工复核（record_anomaly）。"""
    reg = _reg(
        tier1_pages=_text_pages("附子20g"),   # FAIL
        tier3_texts=["附子二钱"],             # 数字分歧 → high
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    orchestrate_book("/fp", "bkc3", cfg, reg)

    db = BookDB("bkc3", db_dir=str(tmp_path))
    anomalies = db.get_anomalies()  # resolution='pending'
    db.close()
    assert len(anomalies) >= 1
    # 复核队列项应来自 CrossAlign 且标记 cross_divergence
    assert any(
        "CrossAlign" in (a["detector_chain"] or "") and "cross_divergence" in (a["details"] or "")
        for a in anomalies
    )


def test_cross_align_skipped_on_tier1_success(tmp_path):
    """Tier1 直接通过 → 不进入 Tier3，无分歧落库。"""
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    cfg = StubConfig(db_dir=str(tmp_path))
    orchestrate_book("/fp", "bkc2", cfg, reg)
    rows = _read_db("bkc2", str(tmp_path))
    assert rows == []


def test_cross_divergence_arbitrated_by_vision(tmp_path, monkeypatch):
    """4.3 分歧级视觉仲裁：allow_cloud_vision=True 时，high 分歧经 VL 仲裁后状态被更新。

    注入假 VL 响应（real_char 匹配 b_seg）→ accepted_b；recheck 同时 stub 避免真实网络。
    """
    monkeypatch.setenv("KZOCR_MODELSCOPE_API_KEY", "testkey")
    monkeypatch.setattr(
        _orc.VisionRecheckAdapter, "recheck",
        lambda self, *a, **k: GlyphVerdict(status="PASS", confidence=0.7,
                                           details="stub", detector_name="VisionRecheckAdapter"),
    )
    monkeypatch.setattr(
        _orc.VisionRecheckAdapter, "_post_vl",
        lambda self, prompt, b64: '{"is_match": true, "confidence": 0.9, "real_char": "二钱"}',
    )

    reg = _reg(
        tier1_pages=_text_pages("附子20g"),   # ToxinDose FAIL(critical) → 进 Tier3 比对
        tier3_texts=["附子二钱"],             # 剂量数字分歧 20g↔二钱 → high
    )
    # 渲染桩需带图像，否则仲裁因 img=None 被跳过
    def _render_gen_img(n):
        for i in range(n):
            yield PageInput(page_num=i, img=np.zeros((100, 100, 3), dtype=np.uint8))

    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen_img(1))
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    orchestrate_book("/fp", "bkc4", cfg, reg)

    rows = _read_db("bkc4", str(tmp_path))
    assert len(rows) >= 1
    # high 剂量分歧 20g↔二钱 经 VL 仲裁：mock 返回 real_char='二钱'==b_seg → accepted_b
    high = [r for r in rows if r["priority"] == "high"]
    assert high, "应有一条 high 分歧"
    assert high[0]["status"] == "accepted_b"
    assert high[0]["engine_b"] == "t3"


# ── C：成功页 cross-check ──
def test_cross_check_on_success_page(tmp_path):
    """enable_cross_check=True + Tier2 可用 → 成功页触发 cross-check，分歧落库。"""
    reg = _reg(
        tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"),  # PASS
        # Tier2 不设 requires_network（cross-check 引擎可以是本地 CPU 引擎，不需要 allow_cloud_vision）
    )
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补气，方用萆薮分清饮")]),
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True)
    result = orchestrate_book("/fp", "bkc5", cfg, reg, overrides=overrides)
    assert len(result.pages) == 1

    rows = _read_db("bkc5", str(tmp_path))
    assert len(rows) >= 1, "应有分歧落库"
    assert any(r["engine_a"] == "t1" and r["engine_b"] == "t2" for r in rows)


def test_cross_check_no_tier2(tmp_path):
    """enable_cross_check=True 但无 Tier2 → 静默跳过，无分歧落库。"""
    reg = _reg(
        tier1_pages=_text_pages("黄芪补气"),  # PASS
        # 未注册 Tier2
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True)
    orchestrate_book("/fp", "bkc6", cfg, reg, overrides=overrides)

    rows = _read_db("bkc6", str(tmp_path))
    assert rows == [], "无 Tier2 时应无分歧"


def test_cross_check_disabled_by_default(tmp_path):
    """enable_cross_check=False（默认）→ 即使有 Tier2 可见也不触发 cross-check。"""
    reg = _reg(
        tier1_pages=_text_pages("黄芪补气"),  # PASS
    )
    # 显式注册 requires_network=False 的 Tier2（即使 allow_cloud_vision=False 也可选）
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补")]),
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    orchestrate_book("/fp", "bkc7", cfg, reg)  # 默认 overrides=None，enable_cross_check 为 False
    rows = _read_db("bkc7", str(tmp_path))
    assert rows == [], "默认不启用 cross-check，即使 Tier2 可见也应无分歧"


def _read_anomalies(book_code, db_dir):
    """从 BookDB 读取所有异常记录。"""
    db = BookDB(book_code, db_dir=db_dir)
    rows = db.get_anomalies()
    db.close()
    return rows


# ── 共识错误抽样 ──
def test_consensus_sampling_triggers_on_consensus_page(tmp_path, monkeypatch):
    """共识一致页 + sample_rate=1.0 → 抽样触发，anomaly 含 ConsensusErrorArbitration。"""
    # 强制 random.random 返回 0（永远中签）
    monkeypatch.setattr("random.random", lambda: 0.0)
    # 共识页：Tier1 PASS + Tier2 返回完全相同文本
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补气，方用萆薢分清饮")]),
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0)
    orchestrate_book("/fp", "bkc8", cfg, reg, overrides=overrides)

    anomalies = _read_anomalies("bkc8", str(tmp_path))
    assert len(anomalies) >= 1
    assert any("ConsensusErrorArbitration" in (a["detector_chain"] or "") for a in anomalies)


def test_consensus_sampling_skipped_when_rate_zero(tmp_path):
    """consensus_sample_rate=0.0（默认）→ 不触发抽样。"""
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补气，方用萆薢分清饮")]),
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True, consensus_sample_rate=0.0)
    orchestrate_book("/fp", "bkc9", cfg, reg, overrides=overrides)

    anomalies = _read_anomalies("bkc9", str(tmp_path))
    consensus = [a for a in anomalies if "ConsensusErrorArbitration" in (a["detector_chain"] or "")]
    assert len(consensus) == 0, "consensus_sample_rate=0 不应触发抽样"


def test_consensus_sampling_skipped_when_divergence(tmp_path, monkeypatch):
    """分歧页（Tier2 返回不同文本）→ 不触发抽样。"""
    monkeypatch.setattr("random.random", lambda: 0.0)  # 100% 中签
    reg = _reg(tier1_pages=_text_pages("黄芪补气"))
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补肾")]),  # 分歧：气↔肾
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0)
    orchestrate_book("/fp", "bkc10", cfg, reg, overrides=overrides)

    anomalies = _read_anomalies("bkc10", str(tmp_path))
    consensus = [a for a in anomalies if "ConsensusErrorArbitration" in (a["detector_chain"] or "")]
    assert len(consensus) == 0, "分歧页不应触发共识抽样"


def test_consensus_sampling_vision_pass_skips_anomaly(tmp_path, monkeypatch):
    """VL recheck 返回 PASS → 抽样已执行但不触发 anomaly（VL 确认文本正确）。"""
    monkeypatch.setattr("random.random", lambda: 0.0)  # 100% 中签
    monkeypatch.setenv("KZOCR_MODELSCOPE_API_KEY", "testkey")
    # mock recheck 返回 PASS（VL 确认文本与图片一致）
    monkeypatch.setattr(
        _orc.VisionRecheckAdapter, "recheck",
        lambda self, text, page_img, bbox=None, engine_label="":
            GlyphVerdict(status="PASS", confidence=0.9,
                         details="stub_pass", detector_name="VisionRecheckAdapter"),
    )

    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补气，方用萆薢分清饮")]),
    )

    def _render_gen_img(n):
        for i in range(n):
            yield PageInput(page_num=i, img=np.zeros((100, 100, 3), dtype=np.uint8))

    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen_img(1))
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0)
    orchestrate_book("/fp", "bkc11", cfg, reg, overrides=overrides)

    anomalies = _read_anomalies("bkc11", str(tmp_path))
    consensus = [a for a in anomalies if "ConsensusErrorArbitration" in (a["detector_chain"] or "")]
    assert len(consensus) == 0, "VL 确认 PASS 不应产生仲裁 anomaly"


def test_consensus_sampling_vision_fail_triggers_anomaly(tmp_path, monkeypatch):
    """VL recheck 返回 FAIL → anomaly 产生，含 ConsensusErrorArbitration。"""
    monkeypatch.setattr("random.random", lambda: 0.0)  # 100% 中签
    monkeypatch.setenv("KZOCR_MODELSCOPE_API_KEY", "testkey")
    monkeypatch.setattr(
        _orc.VisionRecheckAdapter, "recheck",
        lambda self, text, page_img, bbox=None, engine_label="":
            GlyphVerdict(
                status="FAIL" if engine_label == "consensus-check" else "PASS",
                confidence=0.3 if engine_label == "consensus-check" else 0.9,
                details="stub_fail;mismatch_detected",
                detector_name="VisionRecheckAdapter",
            ),
    )

    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    reg.register_adapter(
        AdapterMeta(name="t2", label="T2", tier=2, requires_network=False),
        EngineConfig(), adapter=FakePageAdapter([_page_result("黄芪补气，方用萆薢分清饮")]),
    )

    def _render_gen_img(n):
        for i in range(n):
            yield PageInput(page_num=i, img=np.zeros((100, 100, 3), dtype=np.uint8))

    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen_img(1))
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    overrides = EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0)
    orchestrate_book("/fp", "bkc12", cfg, reg, overrides=overrides)

    anomalies = _read_anomalies("bkc12", str(tmp_path))
    assert len(anomalies) >= 1, "VL 判定 FAIL 应触发 anomaly"
    assert any("ConsensusErrorArbitration" in (a["detector_chain"] or "") for a in anomalies)
