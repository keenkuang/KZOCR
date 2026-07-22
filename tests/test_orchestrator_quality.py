"""工作流 B 测试：分歧全进人工 + 共识抽样兜底（已移除「脏书/干净书」保守模式）。

覆盖：
- 所有 high 分歧（含 VL 已明确裁决者）一律进人工复核队列（目标一字不差，程序修正的
  字仍须人工核对）；不再因「干净书」放松、也不再对「脏书」额外加严。
- ``KZOCR_CONSENSUS_SAMPLE_RATE`` / ``EngineOverrides.consensus_sample_rate`` 生效（共识页
  按率抽样送视觉仲裁，仅作 VL 质量抽检兜底）。
- 共识抽样率固定为配置值，conf 门控固定为 _CONF_GATE（已移除保守模式自适应门限）。

全程 mock 引擎与渲染，无真实 PDF / 网络依赖。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

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
from kzocr.scheduler import verifier as _verifier

import numpy as np


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
    page_parallel: bool = False
    page_workers: int = 0


class FakeBookAdapter:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.calls = 0

    def run_book(self, pdf_path, **kwargs):
        self.calls += 1
        return BookResult(book_code="test", title="Test Book", pages=self.pages)

    def run_page(self, pi):
        raise NotImplementedError


class FakePageAdapter:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = 0

    def run_book(self, pdf_path, **kwargs):
        raise NotImplementedError

    def run_page(self, pi):
        self.calls += 1
        if not self.responses:
            raise RuntimeError("FakePageAdapter exhausted")
        return self.responses[pi.page_num % len(self.responses)]


class StubVisionAdapter:
    api_key = "test-key"
    base_url = "https://stub"
    model = "stub"

    def __init__(self, decisions=None):
        self._decisions = list(decisions or [])
        self.arbitrated = []

    def arbitrate_divergence(self, divergence, page_img, confusion_set=None, bucket=None):
        self.arbitrated.append(divergence)
        decision = self._decisions.pop(0) if self._decisions else "manual"
        from kzocr.scheduler.cross_align import DivergenceArbitration
        return DivergenceArbitration(page_no=divergence.page_no, decision=decision)

    def recheck(self, text, page_img=None, engine_label=""):
        from kzocr.engine.types import GlyphVerdict
        return GlyphVerdict(status="PASS", confidence=0.8, details="stub_recheck")


def _text_pages(*texts):
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _text_pages_mixed(pairs):
    return [PageResult(page_num=i, text=t, confidence=c) for i, (t, c) in enumerate(pairs)]


def _reg(tier1_pages=None, tier2_texts=None, tier3_texts=None):
    reg = EngineRegistry()
    if tier1_pages is not None:
        reg.register_adapter(
            AdapterMeta(name="t1", label="T1", tier=1, batch_capable=True),
            EngineConfig(),
            adapter=FakeBookAdapter(tier1_pages),
        )
    if tier2_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t2", label="T2", tier=2, requires_network=True),
            EngineConfig(base_url="https://api.deepseek.com/v1"),
            adapter=FakePageAdapter([AdapterPageResult(text=t) for t in tier2_texts]),
        )
    if tier3_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t3", label="T3", tier=3),
            EngineConfig(),
            adapter=FakePageAdapter([AdapterPageResult(text=t) for t in tier3_texts]),
        )
    return reg


def _render_gen(n):
    for i in range(n):
        yield PageInput(page_num=i, img=None)


def _render_gen_with_img(n):
    for i in range(n):
        yield PageInput(page_num=i, img=np.zeros((8, 8, 3), dtype=np.uint8))


def _patch_glm_stub(monkeypatch, adapter):
    monkeypatch.setattr(
        _verifier.VisionRecheckAdapter, "glm_default",
        staticmethod(lambda: adapter),
    )


def _disable_vision(monkeypatch):
    """禁用全部视觉回看适配器（返回 None），使页判定确定性（文本校验 PASS），
    同时保留 allow_cloud_vision=True 以启用 Tier2 跨引擎比对。"""
    monkeypatch.setattr(_verifier.VisionRecheckAdapter, "glm_default", staticmethod(lambda: None))
    monkeypatch.setattr(_verifier.VisionRecheckAdapter, "modelscope_default", staticmethod(lambda: None))
    monkeypatch.setattr(_verifier.VisionRecheckAdapter, "sensenova_default", staticmethod(lambda: None))


def _run_serial(pdf, book_code, cfg, reg, overrides, monkeypatch, n_pages):
    monkeypatch.setattr(_orc, "render_pages", lambda p, c, dpi=150: _render_gen_with_img(n_pages))
    monkeypatch.delenv("KZOCR_PAGE_PARALLEL", raising=False)
    return orchestrate_book(pdf, book_code, cfg, reg, overrides=overrides)


def _run_parallel(pdf, book_code, cfg, reg, overrides, monkeypatch, n_pages, workers=0):
    monkeypatch.setattr(_orc, "render_pages", lambda p, c, dpi=150: _render_gen(n_pages))
    monkeypatch.setattr(
        _orc, "_render_one_page",
        lambda p, pn, c=None: np.zeros((8, 8, 3), dtype=np.uint8),
    )
    monkeypatch.setenv("KZOCR_PAGE_PARALLEL", "1")
    if workers:
        monkeypatch.setenv("KZOCR_PAGE_WORKERS", str(workers))
    return orchestrate_book(pdf, book_code, cfg, reg, overrides=overrides)


def _count_cross_anomalies(db_dir, book_code):
    from kzocr.storage.db import BookDB
    db = BookDB(book_code, db_dir=db_dir)
    anomalies = db.get_unresolved_anomalies()
    return [a for a in anomalies if "cross_divergence" in a.get("details", "")]


def _count_sampled(db_dir, book_code):
    from kzocr.storage.db import BookDB
    db = BookDB(book_code, db_dir=db_dir)
    anomalies = db.get_unresolved_anomalies()
    return sum(1 for a in anomalies if "consensus_sampled" in a.get("details", ""))


# ── 1. KZOCR_CONSENSUS_SAMPLE_RATE 环境变量生效 ──
def test_scheduler_config_env_consensus_sample_rate(monkeypatch):
    from kzocr.config import SchedulerConfig
    monkeypatch.setenv("KZOCR_CONSENSUS_SAMPLE_RATE", "0.35")
    assert SchedulerConfig.from_env().consensus_sample_rate == 0.35
    monkeypatch.setenv("KZOCR_CONSENSUS_SAMPLE_RATE", "0.0")
    assert SchedulerConfig.from_env().consensus_sample_rate == 0.0


# ── 2. 串行：所有 high 分歧（无论 VL 是否裁决）一律进人工队列 ──
def test_all_high_divergences_enter_human_queue_serial(monkeypatch, tmp_path):
    # 12 页全分歧（Tier1≠Tier2）→ 每页均产生 high 分歧。
    pairs = [(f"甲{i}", 0.97) for i in range(12)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=["乙"] * 12)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    _run_serial("/fp", "bk_all", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    # 每页 high 分歧都进了人工队列（目标一字不差，不再因干净/脏书区分而跳过）。
    cross = _count_cross_anomalies(str(tmp_path), "bk_all")
    assert len(cross) == 12


# ── 3. 串行：VL 明确接受（accepted_a）仍须进人工队列（程序修正字仍须人工核对）──
def test_vl_accepted_still_enters_human_queue_serial(monkeypatch, tmp_path):
    _patch_glm_stub(monkeypatch, StubVisionAdapter(["accepted_a", "accepted_a"]))
    pairs = [(f"甲{i}", 0.97) for i in range(6)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=["乙"] * 6)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _run_serial("/fp", "bk_vl", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 6)
    from kzocr.storage.db import BookDB
    db = BookDB("bk_vl", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    # VL 裁决已回填状态（黄底标注依据）
    assert all(d["status"] == "accepted_a" for d in divs if d["priority"] in ("P0", "P1"))
    # 即便 VL 已接受，high 分歧仍进人工队列（不再自动接受跳过）
    cross = _count_cross_anomalies(str(tmp_path), "bk_vl")
    assert any(a["page_num"] == 0 for a in cross)


# ── 4. 并行：所有 high 分歧一律进人工队列（与串行等价）──
def test_all_high_divergences_enter_human_queue_parallel(monkeypatch, tmp_path):
    pairs = [(f"甲{i}", 0.97) for i in range(12)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=["乙"] * 12)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    _run_parallel("/fp", "bk_all_p", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    cross = _count_cross_anomalies(str(tmp_path), "bk_all_p")
    assert len(cross) == 12


# ── 5. 共识抽样率生效（rate=0 不抽样；rate=1 全抽样）──
def test_consensus_sample_rate_effective_serial(monkeypatch, tmp_path):
    # 共识书（Tier1==Tier2）→ is_consensus=True；vision off → 抽样记 no_vision_skip 异常。
    texts = [f"甲{i}" for i in range(6)]
    pages = [PageResult(page_num=i, text=t, confidence=0.99) for i, t in enumerate(texts)]
    reg = _reg(tier1_pages=pages, tier2_texts=texts)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # rate=0 → 不抽样
    _run_serial("/fp", "bk_s0", cfg, reg, EngineOverrides(enable_cross_check=True, consensus_sample_rate=0.0), monkeypatch, 6)
    assert _count_sampled(str(tmp_path), "bk_s0") == 0
    # rate=1.0 + 强制抽样
    monkeypatch.setattr(random, "random", lambda: 0.0)
    _run_serial("/fp", "bk_s1", cfg, reg, EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0), monkeypatch, 6)
    assert _count_sampled(str(tmp_path), "bk_s1") == 6
