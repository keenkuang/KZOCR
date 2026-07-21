"""工作流 B 测试：分歧率 / 质量优化（保守模式 + 共识抽样 env 旋钮）。

覆盖：
- ``_adaptive_quality_params`` 单元：默认关=基线；开启 + 高分歧（且页数足够）= 上调抽样率 +
  收紧（gate 降低至 0.85，使边界置信度页不再进人工队列）；低分歧 / 页数不足 = 基线（干净书
  / 早期样本不足时不翻跳）。
- 串行主循环：保守模式对高分歧书**降低** conf_low 人工队列（gate 0.85 < 0.90 → 边界置信度页
  不再进队）；干净书不变。
- ``KZOCR_CONSENSUS_SAMPLE_RATE`` / ``EngineOverrides.consensus_sample_rate`` 生效（共识页按率
  抽样送视觉仲裁）。
- 并行主循环（KZOCR_PAGE_PARALLEL=1）：合并阶段同样尊重保守模式自适应门控（与串行等价）；
  修正并行路径 tally 不累计导致保守模式失效的潜在问题。

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
        # 按 page_num 取对应文本（支持引擎 runner 多次调用 / 重试用，不耗尽）
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
        # 视觉回看桩：默认判 PASS，使页判定确定性（不依赖真实 VL 端点）。
        return GlyphVerdict(status="PASS", confidence=0.8, details="stub_recheck")


def _text_pages(*texts):
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _text_pages_mixed(pairs):
    # pairs: list of (text, confidence)
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


def _count_conf_low(db_dir, book_code):
    from kzocr.storage.db import BookDB
    db = BookDB(book_code, db_dir=db_dir)
    anomalies = db.get_unresolved_anomalies()
    return sum(1 for a in anomalies if "conf_low" in a.get("details", ""))


def _count_sampled(db_dir, book_code):
    from kzocr.storage.db import BookDB
    db = BookDB(book_code, db_dir=db_dir)
    anomalies = db.get_unresolved_anomalies()
    return sum(1 for a in anomalies if "consensus_sampled" in a.get("details", ""))


# ── 1. _adaptive_quality_params 单元：默认关 = 基线 ──
def test_adaptive_quality_params_off(monkeypatch):
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", False)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    rate, gate = _orc._adaptive_quality_params({"div": 100, "high": 50}, 50, 0.0)
    assert rate == 0.0
    assert gate == 0.90


# ── 2. 开启 + 高分歧（且页数足够）→ 上调抽样率 + 收紧 gate（0.85 < 0.90）──
def test_adaptive_quality_params_on_high_div(monkeypatch):
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # 12 页、div=6 → ratio 0.5 ≥ 0.30 阈值
    rate, gate = _orc._adaptive_quality_params({"div": 6, "high": 0}, 12, 0.0)
    assert rate == 0.20                       # max(0.0, 0.20)
    assert gate == 0.85                        # 收紧（低于默认 0.90）


# ── 3. 开启但分歧率低 → 不触发，回落基线 ──
def test_adaptive_quality_params_on_low_div(monkeypatch):
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # div=2 / 12 ≈ 0.166 < 0.30
    rate, gate = _orc._adaptive_quality_params({"div": 2, "high": 0}, 12, 0.10)
    assert rate == 0.10
    assert gate == 0.90


# ── 4. 开启但样本不足（页数 < _MIN_PAGES_FOR_RATIO）→ 不触发 ──
def test_adaptive_quality_params_before_min_pages(monkeypatch):
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # processed_pages=5 < 10，即便 div 很高也不翻跳
    rate, gate = _orc._adaptive_quality_params({"div": 100, "high": 0}, 5, 0.0)
    assert rate == 0.0
    assert gate == 0.90


# ── 5. KZOCR_CONSENSUS_SAMPLE_RATE 环境变量生效 ──
def test_scheduler_config_env_consensus_sample_rate(monkeypatch):
    from kzocr.config import SchedulerConfig
    monkeypatch.setenv("KZOCR_CONSENSUS_SAMPLE_RATE", "0.35")
    assert SchedulerConfig.from_env().consensus_sample_rate == 0.35
    monkeypatch.setenv("KZOCR_CONSENSUS_SAMPLE_RATE", "0.0")
    assert SchedulerConfig.from_env().consensus_sample_rate == 0.0


# ── 6. 串行：保守模式对高分歧书降低 conf_low 队列 ──
def test_conservative_lowers_conf_low_serial(monkeypatch, tmp_path):
    # 12 页全分歧（Tier1≠Tier2）→ 触发保守模式；前 10 页 conf 0.97，末 2 页 conf 0.88（边界）。
    pairs = [(f"甲{i}", 0.97) for i in range(10)] + [("甲10", 0.88), ("甲11", 0.88)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=["乙"] * 12)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # 关
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", False)
    _run_serial("/fp", "bk_q_off", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    off_count = _count_conf_low(str(tmp_path), "bk_q_off")
    # 开
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    _run_serial("/fp", "bk_q_on", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    on_count = _count_conf_low(str(tmp_path), "bk_q_on")
    assert off_count == 2    # 末 2 页 conf 0.88 ≤ 0.90 进队
    assert on_count == 0     # 保守模式 gate=0.85 → 0.88 不再进队


# ── 7. 串行：干净书（零分歧）保守模式不触发，行为不变 ──
def test_conservative_clean_book_unchanged_serial(monkeypatch, tmp_path):
    # 12 页全共识（Tier1==Tier2）→ div ratio ~0 → 保守模式不触发。
    texts = [f"甲{i}" for i in range(12)]
    pairs = [(t, 0.97) for t in texts[:10]] + [("甲10", 0.88), ("甲11", 0.88)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=texts)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", False)
    _run_serial("/fp", "bk_c_off", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    off_count = _count_conf_low(str(tmp_path), "bk_c_off")
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    _run_serial("/fp", "bk_c_on", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    on_count = _count_conf_low(str(tmp_path), "bk_c_on")
    assert off_count == 2    # 末 2 页边界置信度，默认 gate 0.90 下进队
    assert on_count == 2     # 干净书不触发保守模式，行为不变


# ── 8. 串行：共识抽样率生效（rate=0 不抽样；rate=1 全抽样）──
def test_consensus_sample_rate_effective_serial(monkeypatch, tmp_path):
    # 共识书（Tier1==Tier2）→ is_consensus=True；vision off → 抽样记 no_vision_skip 异常。
    texts = [f"甲{i}" for i in range(6)]
    pages = [PageResult(page_num=i, text=t, confidence=0.99) for i, t in enumerate(texts)]
    reg = _reg(tier1_pages=pages, tier2_texts=texts)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", False)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    # rate=0 → 不抽样
    _run_serial("/fp", "bk_s0", cfg, reg, EngineOverrides(enable_cross_check=True, consensus_sample_rate=0.0), monkeypatch, 6)
    assert _count_sampled(str(tmp_path), "bk_s0") == 0
    # rate=1.0 + 强制抽样
    monkeypatch.setattr(random, "random", lambda: 0.0)
    _run_serial("/fp", "bk_s1", cfg, reg, EngineOverrides(enable_cross_check=True, consensus_sample_rate=1.0), monkeypatch, 6)
    assert _count_sampled(str(tmp_path), "bk_s1") == 6


# ── 9. 并行：合并阶段同样尊重保守模式自适应门控（与串行等价）──
def test_conservative_lowers_conf_low_parallel(monkeypatch, tmp_path):
    pairs = [(f"甲{i}", 0.97) for i in range(10)] + [("甲10", 0.88), ("甲11", 0.88)]
    reg = _reg(tier1_pages=_text_pages_mixed(pairs), tier2_texts=["乙"] * 12)
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    _disable_vision(monkeypatch)
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.90)
    monkeypatch.setattr(_orc, "_CONSERVATIVE_MODE", True)
    _run_parallel("/fp", "bk_qp_on", cfg, reg, EngineOverrides(enable_cross_check=True), monkeypatch, 12)
    # 并行路径必须正确累计 tally 才能触发保守模式（gate 0.85 → 0.88 不进队）
    assert _count_conf_low(str(tmp_path), "bk_qp_on") == 0
