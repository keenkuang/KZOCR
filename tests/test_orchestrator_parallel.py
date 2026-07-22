"""工作流 A 测试：页级并发编排 + 渲染隔离（KZOCR_PAGE_PARALLEL）。

覆盖：默认关=串行路径不变；开启=多页并发、合并结果等价、VLM 锁安全、渲染隔离、
大书可控并发。全程 mock 引擎与渲染，无真实 PDF / 网络依赖。

设计要点（见 kzocr/scheduler/orchestrator.py）：
- 页级并发默认关闭，冻结栈行为不变；开启时每 worker 独立渲染、合并阶段串行写共享状态。
- 串行与并行两条路径经 ``_finalize_book`` 统一收口，最终 ``BookResult`` 应等价。
"""

from __future__ import annotations

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


# ── 桩类型（与 test_orchestrator 对齐的最小子集）──
@dataclass
class StubConfig:
    max_pages: int = 50
    total_timeout_s: int = 7200
    max_time_per_page_ms: int = 120000
    allow_cloud_vision: bool = False
    book_type: str = ""
    pub_era: str = ""
    output_dir: str = ""
    trace_dir: str = ""  # 空 = 禁用 trace 写出
    db_dir: str = ""
    # 页级并发旋钮（仅并行路径读取；默认关）
    page_parallel: bool = False
    page_workers: int = 0


class FakeBookAdapter:
    def __init__(self, pages: list[PageResult] | None = None):
        self.pages = pages or []
        self.calls = 0

    def run_book(self, pdf_path: str, **kwargs) -> BookResult:
        self.calls += 1
        return BookResult(book_code="test", title="Test Book", pages=self.pages)

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        raise NotImplementedError


class FakePageAdapter:
    def __init__(self, responses: list[AdapterPageResult] | None = None):
        self.responses = list(responses or [])
        self.calls = 0

    def run_book(self, pdf_path: str, **kwargs) -> BookResult:
        raise NotImplementedError

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        self.calls += 1
        if not self.responses:
            raise RuntimeError("FakePageAdapter exhausted")
        return self.responses.pop(0)


class StubVisionAdapter:
    api_key = "test-key"
    base_url = "https://stub"
    model = "stub"

    def __init__(self, decisions: list[str] | None = None):
        self._decisions = list(decisions or [])
        self.arbitrated: list = []

    def arbitrate_divergence(self, divergence, page_img, confusion_set=None, bucket=None):
        self.arbitrated.append(divergence)
        decision = self._decisions.pop(0) if self._decisions else "manual"
        from kzocr.scheduler.cross_align import DivergenceArbitration
        return DivergenceArbitration(page_no=divergence.page_no, decision=decision)


def _text_pages(*texts: str) -> list[PageResult]:
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _text_pages_conf(conf: float = 0.97, *texts: str) -> list[PageResult]:
    return [PageResult(page_num=i, text=t, confidence=conf) for i, t in enumerate(texts)]


def _reg(
    tier1_pages: list[PageResult] | None = None,
    tier2_texts: list[str] | None = None,
    tier3_texts: list[str] | None = None,
) -> EngineRegistry:
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


def _render_gen(n: int):
    for i in range(n):
        yield PageInput(page_num=i, img=None)


def _render_gen_with_img(n: int):
    for i in range(n):
        yield PageInput(page_num=i, img=np.zeros((8, 8, 3), dtype=np.uint8))


def _patch_glm_stub(monkeypatch, adapter) -> None:
    monkeypatch.setattr(
        _verifier.VisionRecheckAdapter, "glm_default",
        staticmethod(lambda: adapter),
    )


# ── 主测试 helper：在 parallel 模式下编排一本书 ──
def _run_parallel(pdf, book_code, cfg, reg, overrides, monkeypatch, n_pages, with_img, workers=0):
    """monkeypatch 渲染桩 + 开启页级并发，运行编排。"""
    monkeypatch.setattr(_orc, "render_pages", lambda p, c, dpi=150: _render_gen(n_pages))
    # 并发模式下每 worker 自行渲染本页（隔离）：返回 dummy 图像。
    monkeypatch.setattr(
        _orc, "_render_one_page",
        lambda p, pn, c=None: np.zeros((8, 8, 3), dtype=np.uint8),
    )
    monkeypatch.setenv("KZOCR_PAGE_PARALLEL", "1")
    if workers:
        monkeypatch.setenv("KZOCR_PAGE_WORKERS", str(workers))
    return orchestrate_book(pdf, book_code, cfg, reg, overrides=overrides)


def _run_serial(pdf, book_code, cfg, reg, overrides, monkeypatch, n_pages, with_img):
    """默认串行路径（关闭页级并发），用于等价性对照。"""
    gen = _render_gen_with_img if with_img else _render_gen
    monkeypatch.setattr(_orc, "render_pages", lambda p, c, dpi=150: gen(n_pages))
    monkeypatch.delenv("KZOCR_PAGE_PARALLEL", raising=False)
    return orchestrate_book(pdf, book_code, cfg, reg, overrides=overrides)


# ── 1. 默认关：走串行路径，单页成功 ──
def test_parallel_off_default_serial(monkeypatch, tmp_path):
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_serial("/fp", "bk_off", cfg, reg, None, monkeypatch, 1, False)
    assert len(result.pages) == 1
    assert not result.failed_pages


# ── 2. 并行：全成功多页 ──
def test_parallel_basic_all_success(monkeypatch, tmp_path):
    reg = _reg(tier1_pages=_text_pages("甲", "乙", "丙"))
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_parallel("/fp", "bk_p1", cfg, reg, None, monkeypatch, 3, False)
    assert len(result.pages) == 3
    texts = {p.text for p in result.pages}
    assert texts == {"甲", "乙", "丙"}
    assert not result.failed_pages


# ── 3. 并行：Tier1 失败 → Tier3 成功 ──
def test_parallel_tier1_fail_tier3_success(monkeypatch, tmp_path):
    reg = _reg(
        tier1_pages=_text_pages("附子 20g"),
        tier3_texts=["黄芪补气，方用萆薢分清饮"],
    )
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_parallel("/fp", "bk_p2", cfg, reg, None, monkeypatch, 1, False)
    assert len(result.pages) == 1
    assert "萆薢" in result.pages[0].text
    assert not result.failed_pages


# ── 4. 并行：全部失败 → HumanGate ──
def test_parallel_all_fail_human_gate(monkeypatch, tmp_path):
    reg = _reg(tier1_pages=_text_pages("附子 20g"), tier3_texts=["附子 20g"])
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_parallel("/fp", "bk_p3", cfg, reg, None, monkeypatch, 1, False)
    assert 0 in result.failed_pages
    assert "All tiers failed" in result.failed_pages[0]


# ── 5. 等价性：并行 vs 串行（全成功多页）──
def test_parallel_equivalence_all_success(monkeypatch, tmp_path):
    reg = _reg(tier1_pages=_text_pages("甲", "乙", "丙", "丁"))
    serial = _run_serial("/fp", "bk_eq_s", StubConfig(db_dir=str(tmp_path)), reg, None, monkeypatch, 4, False)
    parallel = _run_parallel("/fp", "bk_eq_p", StubConfig(db_dir=str(tmp_path)), reg, None, monkeypatch, 4, False)
    s_map = {p.page_num: p.text for p in serial.pages}
    p_map = {p.page_num: p.text for p in parallel.pages}
    assert s_map == p_map
    assert serial.failed_pages == parallel.failed_pages
    assert set(serial.uncertain_pages) == set(parallel.uncertain_pages)


# ── 6. 并行 + VL 交叉校验：high 分歧被仲裁且不进人工队列 ──
def test_parallel_cross_check_vl_resolved(monkeypatch, tmp_path):
    adapter = StubVisionAdapter(["accepted_a", "accepted_a"])
    _patch_glm_stub(monkeypatch, adapter)
    reg = _reg(
        tier1_pages=_text_pages_conf(0.97, "黄芪三钱，方用萆薢分清饮"),
        tier2_texts=["黄芪二钱，方用萆薢分清饮"],
    )
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    result = _run_parallel(
        "/fp", "bk_pvl", cfg, reg,
        EngineOverrides(enable_cross_check=True), monkeypatch, 1, True,
    )
    assert result.pages
    from kzocr.storage.db import BookDB
    db = BookDB("bk_pvl", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    assert any(d["priority"] in ("P0", "P1") for d in divs)
    assert all(d["status"] == "accepted_a" for d in divs if d["priority"] in ("P0", "P1"))
    # 即便 VL 已裁决（accepted_a），所有 high 分歧仍进人工队列（一字不差，不再自动接受跳过）
    anomalies = db.get_unresolved_anomalies()
    assert any(a["page_num"] == 0 and "cross_divergence" in a["details"] for a in anomalies)


# ── 7. 渲染隔离：每 worker 独立渲染本页（调用次数 = 页数）──
def test_parallel_render_isolation(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _fake_render(pdf, pn, c=None):
        calls["n"] += 1
        return np.zeros((8, 8, 3), dtype=np.uint8)

    monkeypatch.setattr(_orc, "render_pages", lambda p, c, dpi=150: _render_gen(4))
    monkeypatch.setattr(_orc, "_render_one_page", _fake_render)
    monkeypatch.setenv("KZOCR_PAGE_PARALLEL", "1")
    reg = _reg(tier1_pages=_text_pages("甲", "乙", "丙", "丁"))
    cfg = StubConfig(db_dir=str(tmp_path))
    _ = orchestrate_book("/fp", "bk_iso", cfg, reg)
    # 4 页各由独立 worker 渲染 1 次（合并阶段不重复渲染）
    assert calls["n"] == 4


# ── 8. 大书 + 受限 worker：全部页处理完 ──
def test_parallel_large_book_bounded_workers(monkeypatch, tmp_path):
    n = 12
    reg = _reg(tier1_pages=_text_pages(*[f"页{i}" for i in range(n)]))
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_parallel("/fp", "bk_big", cfg, reg, None, monkeypatch, n, False, workers=2)
    # 12 页全部成功采纳
    assert len(result.pages) == n
    assert not result.failed_pages


# ── 9. 单页容错（单元）：_process_one_page_safe 捕获异常转 failed 态 ──
def test_process_one_page_safe_catches_exception(monkeypatch):
    def _boom(pn, pi, ctx):
        raise RuntimeError("render xref broken")

    monkeypatch.setattr(_orc, "_process_one_page", _boom)
    out = _orc._process_one_page_safe(7, PageInput(page_num=7, img=None), object())
    assert out.failed is True
    assert "RuntimeError" in out.failed_reason
    assert "render xref broken" in out.failed_reason
    assert out.appended is False
    assert out.tier1_passed is False  # 触发合并阶段跳过 db 写 / 分歧最终化


def test_process_one_page_safe_passthrough_on_success(monkeypatch):
    stub = _orc._PageOutcome(
        page_num=2, verdict=_verifier.GlyphVerdict(status="PASS", confidence=0.9),
        final_text="玄参", appended=True,
    )
    monkeypatch.setattr(_orc, "_process_one_page", lambda pn, pi, ctx: stub)
    assert _orc._process_one_page_safe(2, PageInput(page_num=2, img=None), object()) is stub


# ── 10. 单页容错（集成）：某页损坏不中止整书，进 failed_pages ──
def test_parallel_one_page_error_does_not_abort_book(monkeypatch, tmp_path):
    orig = _orc._process_one_page

    def _side(pn, pi, ctx):
        if pn == 1:
            raise RuntimeError("boom on page 1")
        return orig(pn, pi, ctx)

    monkeypatch.setattr(_orc, "_process_one_page", _side)
    reg = _reg(tier1_pages=_text_pages("甲", "乙", "丙"))
    cfg = StubConfig(db_dir=str(tmp_path))
    result = _run_parallel("/fp", "bk_ft", cfg, reg, None, monkeypatch, 3, False)
    # 第 1 页失败，其余两页正常产出
    assert 1 in result.failed_pages
    assert "boom on page 1" in result.failed_pages[1]
    assert len(result.pages) == 2
    assert {p.text for p in result.pages} == {"甲", "丙"}
