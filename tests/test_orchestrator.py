"""E4 Orchestrator 测试（v0.7 §11.5 / §11.6）。

覆盖 8 种兜底路径参数化 + 竖排跳 T1 + 额外用例。
全程 mock 引擎（FakeBookAdapter/FakePageAdapter）与渲染，
无真实 PDF / 网络依赖。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

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


class FakeBookAdapter:
    """全书引擎适配器桩。"""

    def __init__(self, pages: list[PageResult] | None = None):
        self.pages = pages or []
        self.calls = 0

    def run_book(self, pdf_path: str) -> BookResult:
        self.calls += 1
        return BookResult(book_code="test", title="Test Book", pages=self.pages)

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        raise NotImplementedError


class FakePageAdapter:
    """页级引擎适配器桩。"""

    def __init__(self, responses: list[AdapterPageResult] | None = None):
        self.responses = list(responses or [])
        self.calls = 0

    def run_book(self, pdf_path: str) -> BookResult:
        raise NotImplementedError

    def run_page(self, pi: PageInput) -> AdapterPageResult:
        self.calls += 1
        if not self.responses:
            raise RuntimeError("FakePageAdapter exhausted")
        return self.responses.pop(0)


# ── 辅助工厂 ──
def _text_pages(*texts: str) -> list[PageResult]:
    return [PageResult(page_num=i, text=t) for i, t in enumerate(texts)]


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


# ── 2. Tier1 失败 → Tier2 成功 ──
def test_tier1_fail_tier2_success(monkeypatch):
    txt_toxic = "附子 20g"  # 触发 ToxinDose FAIL(critical)
    txt_ok = "黄芪补气，方用萆薢分清饮"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier2_texts=[txt_ok],
        cloud_base_url="https://api.deepseek.com/v1",
    )
    result = orchestrate_book("/fp", "bk02", StubConfig(allow_cloud_vision=True), reg)
    assert len(result.pages) == 1
    assert "萆薢" in result.pages[0].text  # Tier2 文本被采纳
    assert not result.failed_pages


# ── 3. Tier2 云端 egress 拦截 → Tier3 兜底 ──
def test_egress_blocked_falls_to_tier3(monkeypatch):
    txt_toxic = "附子 20g"
    txt_t3 = "黄芪补气固表 T3"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier2_texts=["不会执行的文本"],
        tier3_texts=[txt_t3],
        cloud_base_url="https://blocked.invalid/v1",
    )
    # block.invalid 域名不在 egress allowlist → raise ValueError
    result = orchestrate_book("/fp", "bk03", StubConfig(allow_cloud_vision=True), reg)
    assert len(result.pages) == 1
    assert "T3" in result.pages[0].text
    # t2 should be marked UNAVAILABLE
    assert reg.get("t2") is not None
    assert reg.get("t2").status == "UNAVAILABLE"


# ── 4. Tier2 超时 → Tier3 兜底 ──
def test_tier2_timeout_falls_to_tier3(monkeypatch):
    txt_toxic = "附子 20g"
    txt_t3 = "黄芪补气 T3"
    reg = _reg(
        tier1_pages=_text_pages(txt_toxic),
        tier2_texts=["不会执行的文本"],
        tier3_texts=[txt_t3],
    )
    # 让 tier2 adapter 的 run_page 睡眠超时
    orig_run_page = reg.get("t2").adapter.run_page
    reg.get("t2").adapter.run_page = lambda pi: (time.sleep(0.3) or _page_result("x"))
    result = orchestrate_book(
        "/fp", "bk04", StubConfig(allow_cloud_vision=True, max_time_per_page_ms=20), reg
    )
    # 超时后应尝试 T3 并采用其文本
    assert len(result.pages) == 1
    assert "T3" in result.pages[0].text
    # 恢复
    reg.get("t2").adapter.run_page = orig_run_page


# ── 5. Tier3 直接成功（无 Tier2）──
def test_tier3_success_no_tier2(monkeypatch):
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
        tier2_texts=[txt_toxic],   # T2 也 FAIL
        tier3_texts=[txt_toxic],   # T3 也 FAIL
        cloud_base_url="https://blocked.invalid/v1",
    )
    # 把 blocked invalid 改为有效 URL 以让 T2 执行（而非被 egress 拦截）
    reg.get("t2").config.base_url = "https://api.deepseek.com/v1"
    result = orchestrate_book("/fp", "bk06", StubConfig(allow_cloud_vision=True), reg)
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


# ── 9. 竖排页跳过 Tier1（§4.1 / §11.6）──
def test_vertical_page_skips_tier1_text(monkeypatch):
    tier1_text = "TIER1_ONLY"
    tier2_text = "TIER2_RESULT"
    monkeypatch.setattr(_orc, "render_pages", lambda pdf, cfg, dpi=150: (
        PageInput(page_num=i, img=None, layout=PageLayout(page_num=i, is_vertical=True if i == 0 else False))
        for i in range(1)
    ))
    reg = _reg(
        tier1_pages=_text_pages(tier1_text),
        tier2_texts=[tier2_text],
        cloud_base_url="https://api.deepseek.com/v1",
    )
    result = orchestrate_book("/fp", "bk09", StubConfig(allow_cloud_vision=True), reg)
    # 竖排页不应采纳 Tier1 文本
    assert len(result.pages) == 1
    assert "TIER1_ONLY" not in result.pages[0].text
    assert "TIER2_RESULT" in result.pages[0].text


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
