"""E4 Orchestrator 测试（v0.7 §11.5 / §11.6）。

覆盖 8 种兜底路径参数化 + 竖排跳 T1 + 额外用例。
全程 mock 引擎（FakeBookAdapter/FakePageAdapter）与渲染，
无真实 PDF / 网络依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
import types

import pytest

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    BookResult,
    EngineConfig,
    PageInput,
    PageLayout,
    PageResult,
)
from kzocr.scheduler import orchestrator as _orc
from kzocr.scheduler.orchestrator import orchestrate_book
from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import EngineOverrides
from kzocr.scheduler import verifier as _verifier
from kzocr.storage.db import BookDB
from kzocr.scheduler.cross_align import DivergenceArbitration, run_cross_align

import numpy as np


# ── 桩类型 ──
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
    db_dir: str = ""     # BookDB 目录（默认 cwd / KZOCR_DB_DIR）


class FakeBookAdapter:
    """全书引擎适配器桩。"""

    def __init__(self, pages: list[PageResult] | None = None):
        self.pages = pages or []
        self.calls = 0

    def run_book(self, pdf_path: str, **kwargs) -> BookResult:
        self.calls += 1
        return BookResult(book_code="test", title="Test Book", pages=self.pages)

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        raise NotImplementedError


class CapturingBookAdapter:
    """记录 run_book 调用参数的全书引擎适配器桩（用于验证 max_pages 透传）。"""

    def __init__(self, pages: list[PageResult] | None = None):
        self.pages = pages or [PageResult(page_num=0, text="黄芪补气，方用萆薢分清饮")]
        self.run_book_kwargs: dict | None = None

    def run_book(self, pdf_path: str, **kwargs) -> BookResult:
        self.run_book_kwargs = dict(kwargs)
        return BookResult(book_code="test", title="Test Book", pages=self.pages)

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        raise NotImplementedError


class FakePageAdapter:
    """页级引擎适配器桩。"""

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
    """可配置决策的视觉仲裁桩：按 pending 顺序返回 decision。

    模拟 ``VisionRecheckAdapter.arbitrate_divergence`` 的返回语义
    （decision ∈ accepted_a/accepted_b/both_wrong/manual/uncertain），
    供验证成功/失败路径的分歧路由与状态更新。
    """

    api_key = "test-key"  # 让 orchestrator._get_vision_adapter 视为已配置
    base_url = "https://stub"
    model = "stub"

    def __init__(self, decisions: list[str] | None = None):
        self._decisions = list(decisions or [])
        self.arbitrated: list = []

    def arbitrate_divergence(self, divergence, page_img, confusion_set=None, bucket=None):
        self.arbitrated.append(divergence)
        decision = self._decisions.pop(0) if self._decisions else "manual"
        return DivergenceArbitration(page_no=divergence.page_no, decision=decision)


class StubVisionAdapterRaising(StubVisionAdapter):
    """arbitrate_divergence 始终抛异常，验证视觉仲裁失败属增强不阻断。"""

    def arbitrate_divergence(self, divergence, page_img, confusion_set=None, bucket=None):
        raise RuntimeError("vl down")


class StubDB:
    """只记录 update_cross_divergence_status 调用的轻量 DB 桩（helper 单测用）。"""

    def __init__(self):
        self.status_updates: list[tuple] = []
        self.anomalies: list = []
        self.wrote = False

    def update_cross_divergence_status(self, page_no, div_type, a_seg, b_seg, status):
        self.status_updates.append((page_no, div_type, a_seg, b_seg, status))

    def write_cross_divergences(self, page_no, divs, engine_a, engine_b):
        self.wrote = True

    def record_anomaly(self, page_num, verdict, detector_chain=None):
        self.anomalies.append((page_num, verdict))


# ── 辅助工厂 ──
def _text_pages(*texts: str) -> list[PageResult]:
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


def _text_pages_conf(conf: float = 0.97, *texts: str) -> list[PageResult]:
    """带高置信度的成功页构造（避开 conf≤gate 门控 continue，使成功路径进入 cross-check）。"""
    return [PageResult(page_num=i, text=t, confidence=conf) for i, t in enumerate(texts)]


def _page_result(text: str) -> AdapterPageResult:
    return AdapterPageResult(text=text)


def _reg(
    tier1_pages: list[PageResult] | None = None,
    tier2_texts: list[str] | None = None,
    tier3_texts: list[str] | None = None,
    *,
    cloud_base_url: str = "https://api.deepseek.com/v1",
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
            EngineConfig(base_url=cloud_base_url),
            adapter=FakePageAdapter([_page_result(t) for t in tier2_texts]),
        )
    if tier3_texts is not None:
        reg.register_adapter(
            AdapterMeta(name="t3", label="T3", tier=3),
            EngineConfig(),
            adapter=FakePageAdapter([_page_result(t) for t in tier3_texts]),
        )
    return reg


def _render_gen(n: int):
    """生成 n 个裸 PageInput 的模拟渲染。"""
    for i in range(n):
        yield PageInput(page_num=i, img=None)


# ── 前置：monkeypatch render_pages 为 _render_gen ──
@pytest.fixture(autouse=True)
def _patch_render(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(1))


# ── 1. Tier1 成功 ──
def test_tier1_success(monkeypatch):
    """全书引擎产出通过验证的文本 → 直接采纳，无降级调用。"""
    reg = _reg(tier1_pages=_text_pages("黄芪补气，方用萆薢分清饮"))
    result = orchestrate_book("/fakepath", "bk01", StubConfig(), reg)
    assert len(result.pages) == 1
    assert "萆薢" in result.pages[0].text
    assert not result.failed_pages
    # 确认未调用 T2/T3（适配器未注册仍无调用）
    b_adapter: FakeBookAdapter = reg.get("t1").adapter  # type: ignore
    assert b_adapter.calls == 1


# ── 2. Tier1 失败 → Tier3 成功（跳过 Tier2）──
def test_tier1_fail_tier3_success_herb(monkeypatch):
    txt_toxic = "附子 20g"  # 触发 ToxinDose FAIL(critical)
    txt_ok = "黄芪补气，方用萆薢分清饮"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier3_texts=[txt_ok],
    )
    result = orchestrate_book("/fp", "bk02", StubConfig(), reg)
    assert len(result.pages) == 1
    assert "萆薢" in result.pages[0].text  # Tier3 文本被采纳
    assert not result.failed_pages


# ── 3. Tier1 失败 → Tier3 兜底（跳过 Tier2 云端）──
def test_tier1_fail_tier3_cloud_fallback(monkeypatch):
    txt_toxic = "附子 20g"
    txt_t3 = "黄芪补气固表 T3"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier3_texts=[txt_t3],
    )
    result = orchestrate_book("/fp", "bk03", StubConfig(), reg)
    assert len(result.pages) == 1
    assert "T3" in result.pages[0].text


# ── 4. Tier3 成功（无 Tier2）──
def test_tier3_success_no_tier2_blocked_cloud(monkeypatch):
    """Tier2 失败文本 -> Tier3 成功文本。"""
    txt_toxic = "附子 20g"
    txt_t3 = "黄芪补气 T3"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier2_texts=[txt_toxic],
        tier3_texts=[txt_t3],
        cloud_base_url="https://blocked.invalid/v1",
    )
    reg.get("t2").config.base_url = "https://api.deepseek.com/v1"
    result = orchestrate_book("/fp", "bk04", StubConfig(allow_cloud_vision=True), reg)
    assert len(result.pages) == 1
    assert "T3" in result.pages[0].text


def test_tier3_only_success(monkeypatch):
    txt_toxic = "附子 20g"
    txt_t3 = "黄芪补气 T3"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier3_texts=[txt_t3],
    )
    result = orchestrate_book("/fp", "bk05", StubConfig(allow_cloud_vision=True), reg)
    assert len(result.pages) == 1
    assert "T3" in result.pages[0].text
    assert not result.failed_pages


# ── 6. 全部失败 → HumanGate ──
def test_all_tiers_fail_human_gate(monkeypatch):
    txt_toxic = "附子 20g"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier3_texts=[txt_toxic],   # T3 也 FAIL
    )
    result = orchestrate_book("/fp", "bk06", StubConfig(), reg)
    assert 0 in result.failed_pages
    assert "All tiers failed" in result.failed_pages[0]


# ── 7. UNCERTAIN 容错：字符尖峰页被记为 uncertain ──
def test_uncertain_tolerance(monkeypatch):
    # Page1 长文本触发 CharCountSpike（邻居 Page0/2 短→median 小）→ UNCERTAIN
    short = "短"
    long_text = "内容" * 200  # ~400 字
    reg = _reg(tier1_pages=_text_pages(short, long_text, short))
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(3))
    result = orchestrate_book("/fp", "bk07", StubConfig(), reg)
    # Page1 (index 1) → UNCERTAIN, 记入 uncertain_pages
    assert 1 in result.uncertain_pages
    # Page0 和 Page2 PASS → 在 pages 中
    assert len(result.pages) == 2


# ── 8. 预算耗尽截断 ──
def test_budget_exhaustion(monkeypatch):
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(5))
    cfg = StubConfig(max_pages=2, allow_cloud_vision=True)
    reg = _reg(
        tier1_pages=[
            PageResult(page_num=i, text="黄芪补气，方用萆薢分清饮") for i in range(5)
        ],
    )
    result = orchestrate_book("/fp", "bk08", cfg, reg)
    # 只处理了 2 页（page0, page1）
    assert len(result.pages) == 2


# ── 8.5 Tier1 run_book 接收 max_pages（orchestrator 卡顿修复回归）──
def test_tier1_run_book_receives_max_pages(monkeypatch):
    """orchestrate_book 必须把 budget.max_pages 透传给 Tier1 适配器的 run_book，
    否则 run_book 会全本扫描几百页古籍导致长时间卡顿（根因）。"""
    adapter = CapturingBookAdapter()
    reg = EngineRegistry()
    reg.register_adapter(
        AdapterMeta(name="t1", label="T1", tier=1, batch_capable=True),
        EngineConfig(),
        adapter=adapter,
    )
    cfg = StubConfig(max_pages=5)
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(1))
    orchestrate_book("/fp", "bk-max", cfg, reg)
    assert adapter.run_book_kwargs is not None
    assert adapter.run_book_kwargs.get("max_pages") == 5


# ── 9. 竖排页跳过 Tier1（§4.1 / §11.6）→ Tier3 兜底 ──
def test_vertical_page_skips_tier1_text(monkeypatch):
    tier1_text = "TIER1_ONLY"
    tier3_text = "TIER3_RESULT"
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: (
        PageInput(page_num=i, img=None, layout=PageLayout(page_num=i, is_vertical=True if i == 0 else False))
        for i in range(1)
    ))
    reg = _reg(
        tier1_pages=_text_pages(tier1_text),
        tier3_texts=[tier3_text],
    )
    result = orchestrate_book("/fp", "bk09", StubConfig(), reg)
    # 竖排页不应采纳 Tier1 文本
    assert len(result.pages) == 1
    assert "TIER1_ONLY" not in result.pages[0].text
    assert "TIER3_RESULT" in result.pages[0].text


# ── 10. pinned_engine 覆盖 ──
def test_pinned_engine_overrides_selection(monkeypatch):
    # tier1 有失败文本，但 pinned 覆盖后只执行被 pinned 的引擎
    txt_toxic = "附子 20g"
    txt_pinned = "从 pinned 引擎来"
    reg = EngineRegistry()
    reg.register_adapter(
        AdapterMeta(name="t1", label="T1", tier=1, batch_capable=True),
        EngineConfig(),
        adapter=FakeBookAdapter(_text_pages(txt_toxic)),
    )
    reg.register_adapter(
        AdapterMeta(name="special", label="Special", tier=2, requires_network=True),
        EngineConfig(base_url="https://api.deepseek.com/v1"),
        adapter=FakePageAdapter([_page_result(txt_pinned)]),
    )
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: _render_gen(1))
    result = orchestrate_book(
        "/fp", "bk10",
        StubConfig(allow_cloud_vision=True),
        reg,
        overrides=EngineOverrides(pinned_engine="special"),
    )
    assert len(result.pages) == 1
    assert "pinned" in result.pages[0].text


# ── 11. conf≤gate 置信度门控 ──
def test_conf_gate_low_conf_held_for_review(tmp_path):
    """引擎置信度 ≤ 门限的 PASS 页：不自动入库（pending）+ 记录 conf_low 异常。

    验证门控修正前的关键缺陷：低置信度 PASS 页原本在 continue 前已被 imported，
    兜底门控（循环末尾）对其不可达，导致低置信度页直接入库。
    """
    pages = [PageResult(page_num=0, text="黄芪补气，方用萆薢分清饮", confidence=0.82)]
    reg = _reg(tier1_pages=pages)
    result = orchestrate_book(
        "/fp", "bk_gate_low", StubConfig(db_dir=str(tmp_path)), reg
    )
    # 文本仍产出（只是挂起待复核）
    assert len(result.pages) == 1
    assert not result.failed_pages
    db = BookDB("bk_gate_low", db_dir=str(tmp_path))
    assert db.get_page_progress(0)["import_status"] == "pending"
    anomalies = db.get_unresolved_anomalies()
    assert any(a["page_num"] == 0 and "conf_low" in a["details"] for a in anomalies)


def test_conf_gate_high_conf_imported(tmp_path):
    """引擎置信度 > 门限的 PASS 页：正常入库（imported），无 gate 异常。"""
    pages = [PageResult(page_num=0, text="黄芪补气，方用萆薢分清饮", confidence=0.97)]
    reg = _reg(tier1_pages=pages)
    result = orchestrate_book(
        "/fp", "bk_gate_high", StubConfig(db_dir=str(tmp_path)), reg
    )
    assert len(result.pages) == 1
    db = BookDB("bk_gate_high", db_dir=str(tmp_path))
    assert db.get_page_progress(0)["import_status"] == "imported"
    anomalies = db.get_unresolved_anomalies()
    assert not any("conf_low" in a["details"] for a in anomalies)


def test_conf_gate_threshold_env(monkeypatch, tmp_path):
    """门限可配置：把 _CONF_GATE 提升到 0.95 后，conf=0.93 的 PASS 页也被挂起。

    （生产环境该常量由环境变量 KZOCR_CONF_GATE 在模块导入时赋值，这里直接
      monkeypatch 模块级常量以验证门控确实读取阈值。）
    """
    monkeypatch.setattr(_orc, "_CONF_GATE", 0.95)
    pages = [PageResult(page_num=0, text="黄芪补气，方用萆薢分清饮", confidence=0.93)]
    reg = _reg(tier1_pages=pages)
    orchestrate_book("/fp", "bk_gate_env", StubConfig(db_dir=str(tmp_path)), reg)
    db = BookDB("bk_gate_env", db_dir=str(tmp_path))
    assert db.get_page_progress(0)["import_status"] == "pending"
    anomalies = db.get_unresolved_anomalies()
    assert any(a["page_num"] == 0 and "conf_low" in a["details"] for a in anomalies)


# ── 12. 高分歧页视觉仲裁（Box-Guided VL，§5.5 增强闭环）──
def _high_divergences() -> list:
    """构造含多个中文数字分歧的对，确保 cross_align 产出 ≥2 个 high 分歧点。"""
    divs = run_cross_align(
        0,
        "黄芪三钱，当归二钱，白术一钱",
        "黄芪二钱，当归三钱，白术三钱",
        confusion_set={},
    )
    high = [d for d in divs if d.priority == "high"]
    assert len(high) >= 2, f"预期 ≥2 个 high 分歧，实际 {len(high)}"
    return high


def _patch_glm_stub(monkeypatch, adapter) -> None:
    """把 VisionRecheckAdapter.glm_default 替换为返回固定 stub 的静态方法。"""
    monkeypatch.setattr(
        _verifier.VisionRecheckAdapter, "glm_default",
        staticmethod(lambda: adapter),
    )


def _render_gen_with_img(n: int):
    """生成 n 个带 dummy 图像 PageInput 的模拟渲染（触发失败路径视觉仲裁）。"""
    for i in range(n):
        yield PageInput(page_num=i, img=np.zeros((8, 8, 3), dtype=np.uint8))


# ── 12.1 helper：无视觉能力 → 全部 unresolved，不更新状态 ──
def test_arbitrate_helper_no_vision():
    """vision_adapter=None 或 page_img=None 时，helper 不调 VL、不更新状态，
    全部 high 分歧以 unresolved 返回，交由调用方进人工队列。"""
    high = _high_divergences()
    db = StubDB()
    # 无 vision_adapter
    out = _orc._arbitrate_high_divergences(0, high, None, None, None, db, {})
    assert out["resolved"] == []
    assert out["unresolved"] == high
    assert db.status_updates == []
    # 有 vision_adapter 但无图像
    db2 = StubDB()
    out2 = _orc._arbitrate_high_divergences(
        0, high, None, StubVisionAdapter(), None, db2, {},
    )
    assert out2["resolved"] == []
    assert out2["unresolved"] == high
    assert db2.status_updates == []


# ── 12.2 helper：VL 路由（accepted_a → resolved，manual → unresolved）──
def test_arbitrate_helper_vision_routing():
    high = _high_divergences()
    va = StubVisionAdapter(["accepted_a", "manual"])  # 剩余分歧 stub 默认 manual
    db = StubDB()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = _orc._arbitrate_high_divergences(0, high, img, va, None, db, {})
    assert len(out["resolved"]) == 1          # 第一个分歧被 VL 接受
    assert len(out["unresolved"]) == len(high) - 1  # 其余进人工队列
    assert len(db.status_updates) == len(high)       # 每处分歧都更新仲裁状态
    assert db.status_updates[0][4] == "accepted_a"
    assert all(s[4] == "manual" for s in db.status_updates[1:])


# ── 12.3 helper：VL 全部 manual → 全部 unresolved，状态仍更新 ──
def test_arbitrate_helper_vision_manual():
    high = _high_divergences()
    va = StubVisionAdapter(["manual", "manual"])
    db = StubDB()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = _orc._arbitrate_high_divergences(0, high, img, va, None, db, {})
    assert out["resolved"] == []
    assert len(out["unresolved"]) == len(high)
    assert len(db.status_updates) == len(high)


# ── 12.4 helper：VL 抛异常 → 兜底 manual，不阻断、不更新状态 ──
def test_arbitrate_helper_vision_exception():
    high = _high_divergences()
    va = StubVisionAdapterRaising()
    db = StubDB()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = _orc._arbitrate_high_divergences(0, high, img, va, None, db, {})
    assert out["resolved"] == []
    assert out["unresolved"] == high
    assert db.status_updates == []  # 异常分支未持久化状态


# ── 12.5 helper：保守模式覆盖 VL 接受 → 全部进人工 ──
def test_arbitrate_helper_conservative_overrides_accept():
    """conservative=True 时，即便 VL 给出明确接受裁决，high 分歧也全部留人工复核，
    不自动接受（high 占比高的书 VL unresolved 率高，自动接受不可靠，见 v4 扩面结论）。"""
    high = _high_divergences()
    va = StubVisionAdapter(["accepted_a", "accepted_b"])  # VL 明确接受
    db = StubDB()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = _orc._arbitrate_high_divergences(0, high, img, va, None, db, {}, conservative=True)
    assert out["resolved"] == []
    assert len(out["unresolved"]) == len(high)
    # VL 仍被调用以记录裁决状态（供人工复核参考）
    assert len(db.status_updates) == len(high)


# ── 12.6 helper：默认（非保守）仍按 VL 裁决路由（回归守护）──
def test_arbitrate_helper_non_conservative_keeps_accept():
    high = _high_divergences()
    va = StubVisionAdapter(["accepted_a", "manual"])
    db = StubDB()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = _orc._arbitrate_high_divergences(0, high, img, va, None, db, {})
    assert len(out["resolved"]) == 1
    assert len(out["unresolved"]) == len(high) - 1


# ── 12.7 _is_conservative 阈值与最小样本 ──
def test_is_conservative_threshold_and_min():
    # 样本不足 MIN_PAGES：不进入保守（避免早期 high 占比翻跳）
    assert _orc._is_conservative({"div": 5, "high": 5}) is False
    # 样本充足但占比低于阈值：非保守
    assert _orc._is_conservative({"div": 10, "high": 2}) is False
    # 样本充足且占比 ≥ 阈值：保守
    assert _orc._is_conservative({"div": 10, "high": 5}) is True
    assert _orc._is_conservative({"div": 100, "high": 40}) is True
    # 边界恰好 0.40
    assert _orc._is_conservative({"div": 10, "high": 4}) is True


# ── 12.8 成功路径：tally 回写 + 保守模式经 VL 仍全部进人工 ──
def test_run_success_cross_check_threads_tally(monkeypatch):
    """_run_success_cross_check 将全书分歧累计回写 tally，并按 tally 越阈值进入
    保守模式（VL 接受也不自动接受 high 分歧）。"""
    fake_candidate = types.SimpleNamespace(meta=types.SimpleNamespace(name="rapid"))
    monkeypatch.setattr(_orc, "_safe_select_candidates", lambda *a, **k: [fake_candidate])
    monkeypatch.setattr(
        _orc, "_run_single_engine_with_timeout",
        lambda *a, **k: types.SimpleNamespace(text="黄芪二钱，当归三钱"),
    )
    db = StubDB()
    va = StubVisionAdapter(["accepted_a", "accepted_a"])  # VL 明确接受
    tally = {"div": 100, "high": 50}  # 已越阈值 → 保守
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    page_input = PageInput(page_num=0, img=img)
    before_div = tally["div"]
    is_consensus = _orc._run_success_cross_check(
        0, "黄芪三钱，当归二钱", page_input,
        scheduler=object(), registry=object(), db=db,
        confusion_set={}, budget=object(), overrides=object(),
        page_layout=object(), max_time_per_page_ms=60000,
        vision_adapter=va, bucket=None, engine_a="paddle",
        char_boxes=None, tally=tally,
    )
    assert is_consensus is False  # 有分歧 → 非共识页
    assert tally["div"] > before_div  # tally 被回写（当前页分歧计入）
    assert db.wrote is True  # 分歧已落库
    # 保守模式：VL 虽接受，high 分歧仍全部进人工 → 记录 anomaly
    assert len(db.anomalies) == 1



# ── 12.5 成功路径：high 分歧 + 无 VL → 全部进人工队列（行为不变）──
def test_success_cross_check_high_no_vl(monkeypatch, tmp_path):
    """本机无 key：成功页 high 分歧仍全部进 M4 复核队列（allow_cloud_vision 仅用于
    放开 Tier2 候选，_get_vision_adapter 无 key 返回 None → 静默跳过 VL 不崩）。"""
    monkeypatch.setattr(
        _orc, "render_pages",
        lambda pdf, cfg, dpi=150: _render_gen_with_img(1),
    )
    reg = _reg(
        tier1_pages=_text_pages_conf(0.97, "黄芪三钱，方用萆薢分清饮"),
        tier2_texts=["黄芪二钱，方用萆薢分清饮"],
    )
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    result = orchestrate_book(
        "/fp", "bk_succ_novl", cfg, reg,
        overrides=EngineOverrides(enable_cross_check=True),
    )
    assert result.pages  # 成功页文本仍产出
    db = BookDB("bk_succ_novl", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    assert any(d["priority"] == "high" for d in divs)
    anomalies = db.get_unresolved_anomalies()
    assert any(a["page_num"] == 0 and "cross_divergence" in a["details"] for a in anomalies)


# ── 12.5b 成功路径：char_boxes 经 run_cross_align 流入 cross_divergence.boxes（Box-Guided 就绪）──
def test_success_cross_check_boxes_a_flows_to_db(monkeypatch, tmp_path):
    """成功页带 char_boxes 时，跨引擎单字分歧应携带非空 boxes 落库，
    供 §5.5 视觉仲裁精确裁框（box_guided），而非整页退化。"""
    monkeypatch.setattr(
        _orc, "render_pages",
        lambda pdf, cfg, dpi=150: _render_gen_with_img(1),
    )
    text_a = "黄芪三钱，方用萆薢分清饮"
    char_boxes = [[[i, 0, i + 1, 1] for i in range(len(text_a))]]
    pages = [PageResult(page_num=0, text=text_a, confidence=0.97, char_boxes=char_boxes)]
    reg = _reg(tier1_pages=pages, tier2_texts=["黄芪二钱，方用萆薢分清饮"])
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    orchestrate_book(
        "/fp", "bk_boxes_flow", cfg, reg,
        overrides=EngineOverrides(enable_cross_check=True),
    )
    db = BookDB("bk_boxes_flow", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    assert divs, "应产生跨引擎分歧"
    # 至少一条分歧携带非空 boxes（box_guided 裁框可用）
    boxed = [d for d in divs if d.get("boxes") not in ("[]", "", None)]
    assert boxed, "char_boxes 应流入 cross_divergence.boxes"
    # 单字「三↔二」分歧应恰好带 1 个框（box_guided 条件）
    single = [
        d for d in boxed
        if d["boxes"].startswith("[[") and d["boxes"].count("]") == 2
    ]
    assert single, "单字分歧应携带恰好 1 个框"


# ── 12.6 成功路径：high 分歧 + VL 全部 accepted → 不进人工队列 ──
def test_success_cross_check_high_vl_resolved(monkeypatch, tmp_path):
    """VL 已裁决全部 high 分歧时，成功页不再入 M4 复核队列（增强见效）。"""
    adapter = StubVisionAdapter(["accepted_a", "accepted_a"])
    _patch_glm_stub(monkeypatch, adapter)
    monkeypatch.setattr(
        _orc, "render_pages",
        lambda pdf, cfg, dpi=150: _render_gen_with_img(1),
    )
    reg = _reg(
        tier1_pages=_text_pages_conf(0.97, "黄芪三钱，方用萆薢分清饮"),
        tier2_texts=["黄芪二钱，方用萆薢分清饮"],
    )
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    result = orchestrate_book(
        "/fp", "bk_succ_vl", cfg, reg,
        overrides=EngineOverrides(enable_cross_check=True),
    )
    assert result.pages
    db = BookDB("bk_succ_vl", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    assert any(d["priority"] == "high" for d in divs)
    # 全部 high 分歧状态被 VL 更新为 accepted_a
    assert all(d["status"] == "accepted_a" for d in divs if d["priority"] == "high")
    # resolved 不进人工队列
    anomalies = db.get_unresolved_anomalies()
    assert not any(a["page_num"] == 0 and "cross_divergence" in a["details"] for a in anomalies)


# ── 12.7 失败路径：high 分歧 + 有图 + VL → 更新状态且仍进队列 ──
def test_failure_path_high_vl_arbitrates(monkeypatch, tmp_path):
    """Tier1 失败 → Tier3 兜底路径的 high 分歧经 VL 仲裁更新状态；
    失败路径对所有 high 分歧仍进 M4 复核队列（行为不变），仅增强状态更新。"""
    adapter = StubVisionAdapter(["both_wrong"])
    _patch_glm_stub(monkeypatch, adapter)
    monkeypatch.setattr(
        _orc, "render_pages",
        lambda pdf, cfg, dpi=150: _render_gen_with_img(1),
    )
    reg = _reg(
        tier1_pages=_text_pages("附子 20g"),
        tier3_texts=["附子 30g"],
    )
    cfg = StubConfig(allow_cloud_vision=True, db_dir=str(tmp_path))
    result = orchestrate_book("/fp", "bk_fail_vl", cfg, reg)
    assert 0 in result.failed_pages  # 毒性文本仍 HumanGate
    db = BookDB("bk_fail_vl", db_dir=str(tmp_path))
    divs = db.get_cross_divergences(page_no=0)
    assert any(d["priority"] == "high" for d in divs)
    # VL 裁决写入状态
    assert any(d["status"] == "both_wrong" for d in divs if d["priority"] == "high")
    # 失败路径对所有 high 分歧仍进人工队列
    anomalies = db.get_unresolved_anomalies()
    assert any(a["page_num"] == 0 and "cross_divergence" in a["details"] for a in anomalies)

