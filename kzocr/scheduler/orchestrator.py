"""E4: Orchestrator（编排主循环）—— v0.7 §6 / §7。

`orchestrate_book` 串接 E1(EngineRegistry) / E2(EngineScheduler) / E3(GlyphVerifier)：
Tier1 全书处理 → 逐页验证 → 失败页逐页 Tier2 云端 → Tier3 本地 → HumanGate。

真实适配器（BookPipelineAdapter / SenseNovaAdapter 等）属 E5，本模块只依赖
`EngineRunner` 协议；测试用 fake adapter 注入 `register_adapter(meta, config, adapter=...)`。

采纳 traedocu 经验：
- 每页/每引擎独立 try/except，单页异常记入 failed_pages 后继续，书级收尾必执行。
- 编排状态（pages_text / failed_pages / trace / benchmark）为可序列化结构，为将来
  断点续跑/仅重跑失败页预留（本次不落盘、不加 CLI）。
- egress 校验捕 `ValueError`（B4）；`orchestrate_book` 接收 `registry` 入参（B5）。
"""

from __future__ import annotations

import time
import logging
import os
import json
import threading
import functools
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable
from collections.abc import Iterator

from kzocr.config import Config, _safe_bool

from kzocr.engine.types import (
    AdapterPageResult,
    BookResult,
    EngineCallRecord,
    GlyphVerdict,
    PageInput,
    PageLayout,
    PageResult,
)
from kzocr.scheduler.registry import EngineRegistry, EngineRegistration
from kzocr.scheduler.scheduler import Budget, EngineOverrides, EngineScheduler, PageInfo
from kzocr.scheduler.verifier import DetectorContext, GlyphVerifier, VisionRecheckAdapter
from kzocr.scheduler.cross_align import (
    align_boxes_to_text,
    Divergence,
    DivergenceArbitration,
    load_confusion_set,
    run_cross_align,
)
from kzocr.storage.db import BookDB
from kzocr.scheduler.concurrency import run_engines_concurrent, AdaptiveController
from kzocr.engines.ratelimit import MultiTokenRateLimiter
from kzocr.scheduler.vl_budget import VLBudgetConfig, VLBudgetTracker
import numpy as np

# ── conf≤gate 置信度门控阈值 ──
# 引擎识别置信度 ≤ 此值的页在通过字形校验后仍挂起待人工复核（不自动入库）。
# 可用环境变量 KZOCR_CONF_GATE 调整（默认 0.90，与 conf≤0.90 门控一致）。
_CONF_GATE = float(os.environ.get("KZOCR_CONF_GATE", "0.90"))

_logger = logging.getLogger(__name__)

# 视觉调用串行锁：VLM 适配器（verify_with_vision / arbitrate_divergence / recheck）
# 与 vl_budget 计数在页级并发模式下被多线程共享，统一经此锁串行化，避免 ratelimit 竞争。
_vl_lock = threading.Lock()


@dataclass
class _DeferredCrossCheck:
    """跨引擎分歧比对结果（延迟模式）。

    ``_run_success_cross_check`` / ``_run_tier3_divergence`` 在 ``defer=True`` 时
    不写库、不仲裁、不更新全局 ``tally``，仅返回本页分歧数据交由合并阶段按页序处理
    （保守模式依赖跨页累计 tally，必须串行合并后才能判定）。
    """

    is_consensus: bool
    divs: list
    high: list
    engine_a: str = ""
    engine_b: str = ""
    tally_div: int = 0
    tally_high: int = 0


@dataclass
class _PageContext:
    """传给 ``_process_one_page`` 的只读上下文（页级并发工作线程使用）。

    仅含只读引用；可变共享状态（db / registry / tally / vl_budget）不在此，
    由合并阶段在主线程串行处理。
    """

    config: "Config"
    pdf_path: str
    tier1_result: Optional["BookResult"]
    tier1_candidates: list
    t1_elapsed_per_page: int
    overrides: Optional["EngineOverrides"]
    confusion_set: dict
    scheduler: "EngineScheduler"
    registry: "EngineRegistry"
    db: BookDB
    budget: "Budget"
    verifier: "GlyphVerifier"
    book_type: str
    pub_era: str
    concurrency_ctrl: "AdaptiveController"
    max_time_per_page_ms: int
    get_vision_adapter: Callable[[], Optional["VisionRecheckAdapter"]]
    get_vision_bucket: Callable[[], Optional["MultiTokenRateLimiter"]]
    vl_budget: Optional["VLBudgetTracker"]


@dataclass
class _PageOutcome:
    """单页处理的计算结果（线程本地产出，无副作用）。

    合并阶段按页序把这些数据落地：``db_ops``（页局部 db 写）+ ``tally`` 累加 +
    ``success_divs`` / ``tier3_divs``（分歧最终化，含延迟 VLM 仲裁）+ 引擎使用统计 +
    ``pages_text`` / ``pages_order`` / ``trace`` / HumanGate 字典。
    """

    page_num: int
    verdict: "GlyphVerdict"
    final_text: str
    appended: bool
    page_trace: list = field(default_factory=list)
    registry_usage: Optional[tuple] = None  # (engine, verdict, latency_ms)
    char_count: int = 0
    last_engine: str = "unknown"
    last_latency: int = 0
    db_ops: list = field(default_factory=list)  # list[functools.partial[db.xxx]]
    success_divs: list = field(default_factory=list)  # list[_DeferredCrossCheck]
    success_is_consensus: bool = False
    # 共识一致页（候选送视觉抽样）；合并阶段按保守模式自适应抽样率决定实际抽样。
    consensus_sample_request: bool = False
    tier1_passed: bool = False  # Tier1 校验通过（conf 门控判定在合并阶段最终化）
    page_conf: float = 1.0  # Tier1 引擎置信度（合并阶段用于保守模式 conf 门控）
    tier3_divs: list = field(default_factory=list)  # list[_DeferredCrossCheck]
    page_img: Optional["np.ndarray"] = None
    tally_div: int = 0
    tally_high: int = 0
    failed: bool = False
    failed_reason: str = ""
    uncertain: bool = False
    uncertain_verdict: Optional["GlyphVerdict"] = None



def render_pages(pdf_path: str, config: Config | None = None, dpi: int = 150) -> Iterator[PageInput]:
    """流式生成逐页 PageInput（N2）。真实渲染复用 engine/run.py:_pdf_page_to_numpy。

    预处理：版心裁剪（去页眉/页脚/侧边空白）+ 尺寸缩放（适配 VL 模型限制 2048px）。

    测试可 monkeypatch 本函数以 mock 渲染，避免依赖真实 PDF/网络。
    """
    import fitz  # 懒加载，避免无 PDF 场景下强制依赖
    from kzocr.engine.run import _pdf_page_to_numpy, _crop_to_body

    max_pixels = getattr(config, "max_image_pixels", 2048) if config else 2048
    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc):
            img = _pdf_page_to_numpy(page, dpi=dpi)
            # 版心裁剪：传入页码支持奇偶对称
            img = _crop_to_body(img, page_num=i)
            # 尺寸缩放：适配 VL 模型限制，最长边 ≤ max_pixels
            h, w = img.shape[:2]
            scale = min(max_pixels / max(h, w), 1.0)
            if scale < 1.0:
                from PIL import Image as PILImage
                pil = PILImage.fromarray(img)
                pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
                img = np.array(pil)
            yield PageInput(page_num=i, img=img)
    finally:
        doc.close()


def _render_one_page(pdf_path: str, page_num: int, config: "Config | None" = None) -> Optional["np.ndarray"]:
    """渲染单页为 numpy 数组（页级并发隔离用）。

    每 worker 调用本函数**独立打开**自己的 ``fitz`` 文档渲染目标页，不共享主循环文档，
    规避 PyMuPDF ``Document`` 非线程安全。复用与 ``render_pages`` 相同的版心裁剪 + 缩放管线，
    保证页级并发路径与串行渲染路径产出完全一致的 ``img``。
    """
    import fitz  # 懒加载，避免无 PDF 场景下强制依赖
    from kzocr.engine.run import _pdf_page_to_numpy, _crop_to_body

    max_pixels = getattr(config, "max_image_pixels", 2048) if config else 2048
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= doc.page_count:
            return None
        img = _pdf_page_to_numpy(doc[page_num], dpi=150)
        img = _crop_to_body(img, page_num=page_num)
        h, w = img.shape[:2]
        scale = min(max_pixels / max(h, w), 1.0)
        if scale < 1.0:
            from PIL import Image as PILImage
            pil = PILImage.fromarray(img)
            pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
            img = np.array(pil)
        return img
    finally:
        doc.close()


def _safe_select_candidates(
    scheduler: EngineScheduler,
    registry: EngineRegistry,
    tier: int,
    page_input: PageInput,
    budget: Budget,
    page_layout: Optional[PageLayout],
    overrides: Optional[EngineOverrides],
) -> list[EngineRegistration]:
    """select_candidates 的健壮包装：异常时返回空，不拖垮编排。"""
    try:
        return scheduler.select_candidates(
            registry,
            tier=tier,
            page_info=_page_info(page_input, page_layout),
            budget=budget,
            page_layout=page_layout,
            overrides=overrides,
        )
    except Exception as exc:  # pragma: no cover - 防御性
        _logger.error("[orchestrator] select_candidates tier=%d failed: %s", tier, exc)
        return []


def _page_info(page_input: PageInput, page_layout: Optional[PageLayout]) -> PageInfo:
    is_vertical = bool(page_layout and page_layout.is_vertical)
    return PageInfo(page_num=page_input.page_num, is_vertical=is_vertical)


def _run_single_engine_with_timeout(
    engine: EngineRegistration, page_input: PageInput, timeout_s: float
) -> AdapterPageResult:
    """带超时的单引擎调用（§7.3）。防止云端 VLM / 本地 LLM 挂死。"""
    result: dict = {}
    err: dict = {}

    def _target() -> None:
        try:
            result["v"] = engine.adapter.run_page(page_input)
        except Exception as exc:  # pragma: no cover - 防御性
            err["e"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"engine {engine.meta.name} timed out after {timeout_s}s")
    if "e" in err:
        raise err["e"]
    return result["v"]


def _run_success_cross_check(
    page_num: int,
    cur_text: str,
    page_input: PageInput,
    scheduler: EngineScheduler,
    registry: EngineRegistry,
    db: BookDB,
    confusion_set: dict,
    budget: Budget,
    overrides: Optional[EngineOverrides],
    page_layout: Optional[PageLayout],
    max_time_per_page_ms: int,
    vision_adapter: Optional[VisionRecheckAdapter] = None,
    bucket: Optional[MultiTokenRateLimiter] = None,
    engine_a: str = "tier1",
    char_boxes: Optional[list[list[list[int]]]] = None,
    tally: Optional[dict] = None,
    vl_budget: Optional[VLBudgetTracker] = None,
    defer: bool = False,
) -> "bool | _DeferredCrossCheck":
    """成功页跨引擎采样比对：Tier1 成功页追加 Tier2 引擎交叉验证。

    纯增强（try/except 不阻断主流程）；无 Tier2 候选时静默跳过（本机无 GPU/密钥时正常行为）。
    High 优先分歧经 record_anomaly 入 M4 队列；``vision_adapter`` 非空时对 high 分歧执行
    Box-Guided VL 仲裁（``_arbitrate_high_divergences``），仅 VL 未裁决（manual）的分歧进人工队列。

    Args:
        defer: 页级并发模式下为 ``True``。此时本函数**不写库 / 不仲裁 / 不更新全局
            ``tally``**，仅返回 ``_DeferredCrossCheck``（含本页分歧与 tally delta），
            交由合并阶段按页序最终化（保守模式依赖跨页累计 tally，须串行判定）。

    Returns:
        ``defer=False``: ``True`` = 成功运行且无分歧（共识一致页）；``False`` = 未运行 /
            无 Tier2 / 文本为空 / 有分歧。
        ``defer=True``: ``_DeferredCrossCheck``（``is_consensus`` 字段等价上述布尔）。
    """
    try:
        tier2 = _safe_select_candidates(
            scheduler, registry, 2, page_input, budget, page_layout, overrides,
        )
        if not tier2:
            return False  # 无 Tier2 候选（本机无 GPU/密钥），静默跳过
        timeout_cross = min(60000, max_time_per_page_ms) / 1000
        cross_result = _run_single_engine_with_timeout(
            tier2[0], page_input, timeout_cross,
        )
        cross_text = getattr(cross_result, "text", "") or ""
        if not cur_text or not cross_text:
            return False

        divs = run_cross_align(
            page_num, cur_text, cross_text,
            confusion_set=confusion_set,
            boxes_a=align_boxes_to_text(cur_text, char_boxes),
            engine_a=engine_a, engine_b=tier2[0].meta.name,
        )
        if not divs:
            return True  # 无分歧 → 共识一致页

        high = [d for d in divs if d.priority in ("P0", "P1", "high")]
        # 延迟模式：不写库 / 不仲裁 / 不更新 tally，仅返回本页分歧数据，
        # 交由合并阶段按页序最终化（保守模式依赖跨页累计 tally）。
        if defer:
            return _DeferredCrossCheck(
                is_consensus=False, divs=divs, high=high,
                engine_a=engine_a, engine_b=tier2[0].meta.name,
                tally_div=len(divs), tally_high=len(high),
            )

        db.write_cross_divergences(
            page_no=page_num, divs=divs,
            engine_a=engine_a, engine_b=tier2[0].meta.name,
        )
        # 全书 high 占比二级判据：样本充足且越阈值时进入保守模式，
        # 该页 high 分歧全部留人工复核（见 _is_conservative / v4 扩面结论）。
        conservative = _is_conservative(tally) if tally is not None else False
        if high:
            arb_result = _arbitrate_high_divergences(
                page_num, high,
                page_input.img if page_input.img is not None else None,
                vision_adapter, bucket, db, confusion_set,
                conservative=conservative, vl_budget=vl_budget,
            )
            # 仅 VL 无法裁决（manual）或视觉不可用时的 high 分歧进人工复核队列；
            # VL 已给出明确裁决（accepted_a/b、both_wrong）的不重复进队。
            unresolved = arb_result["unresolved"]
            if unresolved:
                db.record_anomaly(
                    page_num,
                    GlyphVerdict(
                        status="UNKNOWN", confidence=0.4,
                        details=(
                            f"cross_divergence;high={len(high)};"
                            f"arbitrated={len(arb_result['resolved'])};"
                            f"sample={unresolved[0].a_seg}↔{unresolved[0].b_seg}"
                        ),
                    ),
                    detector_chain=["CrossAlign"],
                )
        # 回写全书累计（当前页计入后续页的保守判定；本页判定用此前累计）
        if tally is not None:
            tally["div"] = tally.get("div", 0) + len(divs)
            tally["high"] = tally.get("high", 0) + len(high)
        return False  # 有分歧 → 非共识页
    except Exception as exc:
        _logger.warning("[orchestrator] success cross-check failed page=%d: %s", page_num, exc)
        return False


def _run_tier3_divergence(
    page_num: int,
    cur_text: str,
    result_text: str,
    page_input: PageInput,
    confusion_set: dict,
    engine_a: str,
    engine_b: str,
    char_boxes: Optional[list[list[list[int]]]],
    db: BookDB,
    tally: Optional[dict],
    get_vision_adapter: Callable[[], Optional[VisionRecheckAdapter]],
    get_vision_bucket: Callable[[], Optional[MultiTokenRateLimiter]],
    vl_budget: Optional[VLBudgetTracker] = None,
    defer: bool = False,
) -> Optional["_DeferredCrossCheck"]:
    """Tier3 失败路径跨引擎分歧对齐（Tier1 文本 vs Tier3 文本）。

    与成功路径同源：写入交叉分歧 → high 分歧 100% 进人工复核队列 → 视觉仲裁更新状态。
    纯函数无网络（``run_cross_align``），失败页量小，直接落库。

    ``defer=True`` 时仅返回 ``_DeferredCrossCheck``（含 tally delta），不写库 / 不仲裁 /
    不更新全局 ``tally``，交由合并阶段按页序最终化（保守模式依赖跨页累计 tally）。
    """
    if not cur_text or not result_text:
        return None
    try:
        divs = run_cross_align(
            page_num, cur_text, result_text,
            confusion_set=confusion_set,
            boxes_a=align_boxes_to_text(cur_text, char_boxes),
            engine_a=engine_a, engine_b=engine_b,
        )
        if not divs:
            return None
        high = [d for d in divs if d.priority in ("P0", "P1", "high")]
        # 延迟模式：不写库 / 不仲裁 / 不更新 tally，仅返回本页分歧数据。
        if defer:
            return _DeferredCrossCheck(
                is_consensus=False, divs=divs, high=high,
                engine_a=engine_a, engine_b=engine_b,
                tally_div=len(divs), tally_high=len(high),
            )
        db.write_cross_divergences(
            page_no=page_num, divs=divs,
            engine_a=engine_a, engine_b=engine_b,
        )
        # M4 复核队列规则：high 优先级分歧（数字/剂量、形近字）100% 进人工复核
        conservative = _is_conservative(tally) if tally is not None else False
        if high:
            db.record_anomaly(
                page_num,
                GlyphVerdict(
                    status="UNKNOWN",
                    confidence=0.4,
                    details=(
                        f"cross_divergence;high={len(high)};"
                        f"sample={high[0].a_seg}↔{high[0].b_seg}"
                    ),
                ),
                detector_chain=["CrossAlign"],
            )
            # 4.3 分歧级视觉仲裁（Box-Guided VL，退化模式）：失败路径 high 分歧经 VL 仲裁后更新状态。
            va = get_vision_adapter()
            if va is not None and page_input.img is not None:
                _arbitrate_high_divergences(
                    page_num, high, page_input.img, va,
                    get_vision_bucket(), db, confusion_set,
                    conservative=conservative,
                    vl_budget=vl_budget,
                )
        # 回写全书累计（当前页计入后续页的保守判定）
        if tally is not None:
            tally["div"] = tally.get("div", 0) + len(divs)
            tally["high"] = tally.get("high", 0) + len(high)
        return None
    except Exception as exc:  # 分歧比对属增强，绝不阻断主流程
        _logger.warning("[orchestrator] cross_align failed page=%d: %s", page_num, exc)
        return None


# v4 扩面结论落地的二级判据：全书 high 占比 ≥ 此阈值进入「保守仲裁」（多留人工），
# 低于则保持激进自动接受。阈值 0.40 对应 mi-678(45%)/全量中药速查总表(43%) 越线、
# 多数书(~0.21–0.25) 在线的实测区间。
HIGH_RATIO_CONSERVATIVE_THRESHOLD = 0.40
# 全书样本不足此页数时不进入保守模式，避免早期 high 占比翻跳。
_MIN_PAGES_FOR_RATIO = 10

# ── 工作流 B：保守模式（KZOCR_CONSERVATIVE_MODE，默认关）自适应质量参数 ──
# 实时分歧率（tally.div / 已处理页数）超阈值时，对脏书自动加严：上调共识抽样率 +
# 收紧 conf 门限，使脏书（高分歧）自动加严、干净书不受影响。
_CONSERVATIVE_MODE = _safe_bool(os.environ.get("KZOCR_CONSERVATIVE_MODE", ""), False)
_CONSERVATIVE_DIV_RATIO_THRESHOLD = 0.30   # 实时分歧率超此值进入保守加严
_CONSERVATIVE_BOOST_SAMPLE_RATE = 0.20     # 保守模式上调后的共识抽样率
_CONSERVATIVE_TIGHTEN_CONF_GATE = 0.85     # 保守模式收紧后的 conf 门限（低于默认 0.90）


def _adaptive_quality_params(tally: dict, processed_pages: int, base_sample_rate: float) -> tuple[float, float]:
    """工作流 B：保守模式下按实时分歧率动态返回 (抽样率, conf门限)。

    默认关（``_CONSERVATIVE_MODE=False``）时直接返回基线值，行为不变。开启后，已处理页数
    达 ``_MIN_PAGES_FOR_RATIO`` 且实时分歧率（``tally["div"] / processed_pages``）超阈值时，
    上调抽样率至 ``_CONSERVATIVE_BOOST_SAMPLE_RATE`` 并收紧 conf 门限至
    ``_CONSERVATIVE_TIGHTEN_CONF_GATE``，使脏书（高分歧）自动加严、干净书不受影响。

    注意：本函数读取的是「已处理页数之前」的 ``tally``（合并阶段按页序累积），故高分歧书
    在第 ``_MIN_PAGES_FOR_RATIO`` 页之后才逐步加严，避免早期样本不足翻跳（与 ``_is_conservative``
    同源设计）。
    """
    base_gate = _CONF_GATE
    if not _CONSERVATIVE_MODE:
        return base_sample_rate, base_gate
    if processed_pages < _MIN_PAGES_FOR_RATIO:
        return base_sample_rate, base_gate
    div_ratio = tally.get("div", 0) / max(processed_pages, 1)
    if div_ratio >= _CONSERVATIVE_DIV_RATIO_THRESHOLD:
        return (
            max(base_sample_rate, _CONSERVATIVE_BOOST_SAMPLE_RATE),
            min(base_gate, _CONSERVATIVE_TIGHTEN_CONF_GATE),
        )
    return base_sample_rate, base_gate


def _is_conservative(tally: dict) -> bool:
    """全书 high 占比是否进入「保守仲裁」区间。

    v4 扩面发现：high 占比高的书（mi-678 45%、全量中药速查总表 43%）送 VL 仲裁时
    unresolved 比例更高、需人工兜底更多；high 占比低的书分歧多为易判差异，可更激进
    自动接受。故当全书 high/总分歧 ≥ 阈值时，对 high 分歧更保守（多留人工复核）。

    `tally` = {"div": 累计总分歧, "high": 累计 high 分歧}。前 ``_MIN_PAGES_FOR_RATIO``
    页样本不足，不进入保守模式，避免早期翻跳。
    """
    if tally.get("div", 0) < _MIN_PAGES_FOR_RATIO:
        return False
    return (tally["high"] / tally["div"]) >= HIGH_RATIO_CONSERVATIVE_THRESHOLD


def _arbitrate_high_divergences(
    page_num: int,
    high: list[Divergence],
    page_img: Optional[np.ndarray],
    vision_adapter: Optional[VisionRecheckAdapter],
    bucket: Optional[MultiTokenRateLimiter],
    db: BookDB,
    confusion_set: dict,
    conservative: bool = False,
    vl_budget: Optional[VLBudgetTracker] = None,
) -> dict:
    """对 high 优先级跨引擎分歧执行视觉仲裁（Box-Guided VL，退化模式）。

    遍历 ``high`` 分歧调 ``vision_adapter.arbitrate_divergence`` +
    ``db.update_cross_divergence_status``，更新各分歧点的仲裁状态。返回
    ``{"resolved": [...], "unresolved": [...]}``：
    - resolved：VL 给出明确裁决（accepted_a / accepted_b / both_wrong），无需再进人工队列；
    - unresolved：VL 无法裁决（manual）或视觉不可用，需进人工复核。

    ``vl_budget`` 非 None 且预算耗尽时，停止 VL 调用、本页 high 分歧全部以
    unresolved 返回，并记一条 ``VLBudget`` 观测异常（与保守模式同语义降级）。

    纯增强：视觉不可用 / 单点异常均不影响主流程，绝不阻断编排。
    """
    if vision_adapter is None or page_img is None:
        # 无视觉能力：全部视为 unresolved，交由调用方进人工队列
        return {"resolved": [], "unresolved": high}
    resolved: list = []
    unresolved: list = []
    budget_logged = False
    for d in high:
        # W4 预算守卫：逐次检查，超预算则停止 VL 调用、本分歧留人工队列。
        # 每页记一条 VLBudget 观测异常（避免每处分歧重复记）。
        if vl_budget is not None and not vl_budget.can_spend():
            if not budget_logged:
                db.record_anomaly(
                    page_num,
                    GlyphVerdict(
                        status="UNKNOWN", confidence=0.0,
                        details=f"vl_budget_exhausted;{vl_budget.summary()}",
                    ),
                    detector_chain=["VLBudget"],
                )
                budget_logged = True
            unresolved.append(d)
            continue
        try:
            arb = vision_adapter.arbitrate_divergence(
                d, page_img, confusion_set=confusion_set, bucket=bucket,
            )
            if vl_budget is not None:
                vl_budget.spend()
            db.update_cross_divergence_status(
                page_num, d.div_type, d.a_seg, d.b_seg, arb.decision,
            )
            # VL 确认的裁决自动回填 line.human_final（纯增强，异常静默跳过）
            try:
                _apply_vl_fix(db, page_num, d, arb)
            except Exception:
                _logger.debug(
                    "[vl_fix] page=%d skipped (non-fatal)", page_num,
                )
            if conservative or arb.decision == "manual":
                # 保守模式：即便 VL 给出明确裁决，也将 high 分歧全部留人工复核，
                # 不自动接受（high 占比高的书 VL unresolved 率高，自动接受不可靠）。
                unresolved.append(d)
            else:
                resolved.append(d)
        except Exception as exc:  # 视觉仲裁属增强，绝不阻断主流程
            _logger.warning(
                "[orchestrator] cross_arbitrate failed page=%d: %s", page_num, exc,
            )
            unresolved.append(d)
    return {"resolved": resolved, "unresolved": unresolved}


def _apply_vl_fix(
    db: BookDB, page_num: int, divergence: Divergence, arb: DivergenceArbitration,
) -> None:
    """VL 仲裁裁决 accepted_a/accepted_b → 自动回填 line.human_final。

    在 ``line`` 表中搜索包含 ``a_seg`` 的行（原始引擎侧文本），
    找到后将 VL 确认的文本写入 ``human_final``（accepted_a → a_seg,
    accepted_b → b_seg）。不匹配/歧义/空白时静默跳过（不阻断人工复审流程）。

    Args:
        db: BookDB 实例。
        page_num: 页码。
        divergence: 被仲裁的 Divergence 对象（含 a_seg/b_seg）。
        arb: VL 仲裁裁决结果。
    """
    if arb.decision not in ("accepted_a", "accepted_b"):
        return
    confirmed = divergence.a_seg if arb.decision == "accepted_a" else divergence.b_seg
    if not confirmed:
        return

    lines = db.get_page_lines(page_num)
    # 用 a_seg 搜索行文本（a_seg 代表原始引擎侧输出，一定在行文本中）
    search = divergence.a_seg
    if not search:
        return
    matches = [
        (r["para_seq"], r["line_seq"])
        for r in lines
        if search in (r.get("text") or "")
    ]
    if len(matches) == 0:
        _logger.debug(
            "[vl_fix] page=%d no line containing %r", page_num, confirmed,
        )
        return
    if len(matches) > 1:
        _logger.debug(
            "[vl_fix] page=%d ambiguous %r matched %d lines, skip",
            page_num, confirmed, len(matches),
        )
        return

    para_seq, line_seq = matches[0]
    db.save_line_human_final(page_num, para_seq, line_seq, confirmed)
    _logger.info(
        "[vl_fix] page=%d line=(%d,%d) written %r (vl=%s)",
        page_num, para_seq, line_seq, confirmed, arb.decision,
    )


def _sample_consensus_error(
    page_num: int,
    text: str,
    page_img: Optional[np.ndarray],
    vision_adapter: Optional[VisionRecheckAdapter],
    db: BookDB,
    sample_rate: float,
    bucket: Optional[MultiTokenRateLimiter],
    vl_budget: Optional[VLBudgetTracker] = None,
) -> None:
    """共识一致页抽样送视觉仲裁：覆盖「两引擎同错」盲区。

    对两引擎文本一致的页面，以 sample_rate 概率抽样，调 VL 模型做整页视觉核对。
    - 有 VL 且图像可用 → 执行 recheck，FAIL/UNKNOWN 结果入 ConsensusErrorArbitration 队列
    - 无 VL / 无图像 → 记录抽样命中标记（no_vision_skip），留待人工复核
    - ``vl_budget`` 耗尽 → 记录 vl_budget_exhausted 标记，跳过 VL 调用
    """
    import random

    if sample_rate <= 0 or not text:
        return
    if random.random() >= sample_rate:
        return  # 未中签

    if vision_adapter is None or page_img is None:
        db.record_anomaly(
            page_num,
            GlyphVerdict(
                status="UNKNOWN", confidence=0.0,
                details="consensus_sampled;no_vision_skip",
            ),
            detector_chain=["ConsensusErrorArbitration"],
        )
        return

    if vl_budget is not None and not vl_budget.can_spend():
        # 预算耗尽：跳过本次 VL 复核，记一条观测异常（与 high 分歧同语义降级）
        db.record_anomaly(
            page_num,
            GlyphVerdict(
                status="UNKNOWN", confidence=0.0,
                details=f"consensus_sampled;vl_budget_exhausted;{vl_budget.summary()}",
            ),
            detector_chain=["VLBudget"],
        )
        return

    try:
        if bucket is not None:
            bucket.acquire()
        verdict = vision_adapter.recheck(
            text=text, page_img=page_img,
            engine_label="consensus-check",
        )
        if vl_budget is not None:
            vl_budget.spend()
        if verdict.status in ("FAIL", "UNKNOWN"):
            db.record_anomaly(
                page_num, verdict,
                detector_chain=["ConsensusErrorArbitration"],
            )
    except Exception as exc:
        _logger.warning(
            "[orchestrator] consensus_sample vision failed page=%d: %s",
            page_num, exc,
        )


def _record_engine_usage(
    registry: EngineRegistry,
    engine: EngineRegistration,
    verdict: GlyphVerdict,
    latency_ms: int,
    counter: dict[str, int],
) -> None:
    """记录一次引擎调用结果到 registry 与本地计数。"""
    success = verdict.status in ("PASS", "RARE")
    registry.record(
        engine.meta.name,
        success=success,
        glyph=verdict.status,
        latency_ms=latency_ms,
        pages=1,
    )
    counter[engine.meta.name] = counter.get(engine.meta.name, 0) + 1


def _build_pages_result(
    pages_text: list[str],
    pages_order: list[int],
) -> list[PageResult]:
    """将 pages_text 转换为 BookResult 所需的 list[PageResult]。

    ``pages_order`` 给出每页的真实页号（与 PDF 页序一致，含失败页缺口），
    直接用真实 page_num 而非位置索引，确保后续 ``_merge_tier1_char_boxes``
    按 page_num 合并 Tier1 字符框时不错配。
    """
    return [PageResult(page_num=n, text=t) for t, n in zip(pages_text, pages_order)]


def _merge_tier1_char_boxes(
    final_pages: list[PageResult],
    tier1_result: Optional[BookResult],
) -> None:
    """把 Tier1 适配器（adapter.run_book）产出的字符级 bbox 按 page_num 合并进最终页。

    最终 BookResult 由 pages_text 重建（无 char_boxes），而 Tier1 适配器已产出
    字符级 bbox，此处按 page_num 原地回填，保证字符级坐标不丢失。
    """
    if not tier1_result or not tier1_result.pages:
        return
    _cb_by_page = {p.page_num: p.char_boxes for p in tier1_result.pages}
    for pg in final_pages:
        if pg.page_num in _cb_by_page:
            pg.char_boxes = _cb_by_page[pg.page_num]


def _write_trace(trace_dir: str, book_code: str, trace: list[EngineCallRecord]) -> None:
    """写出逐引擎调用 trace（默认 $KZOCR_OUTPUT_DIR/trace；空字符串则禁用）。"""
    if not trace_dir:
        return
    os.makedirs(trace_dir, exist_ok=True)
    path = os.path.join(trace_dir, f"{book_code or 'book'}_trace.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for rec in trace:
            f.write(json.dumps(rec.__dict__, ensure_ascii=False) + "\n")
    _logger.info("[orchestrator] trace written: %s (%d records)", path, len(trace))


def _log_engine_report(
    book_code: str | None,
    pages_text: list[str],
    failed_pages: dict[int, str],
    uncertain_pages: dict[int, GlyphVerdict],
    counter: dict[str, int],
    elapsed_s: float,
) -> None:
    _logger.info(
        "[orchestrator] book=%s pages=%d failed=%d uncertain=%d engine_usage=%s elapsed=%.1fs",
        book_code or "unknown",
        len(pages_text),
        len(failed_pages),
        len(uncertain_pages),
        counter,
        elapsed_s,
    )


def _finalize_divergences_success(
    page_num: int,
    dcc: _DeferredCrossCheck,
    page_img: Optional["np.ndarray"],
    db: BookDB,
    get_vision_adapter: Callable[[], Optional[VisionRecheckAdapter]],
    get_vision_bucket: Callable[[], Optional[MultiTokenRateLimiter]],
    confusion_set: dict,
    vl_budget: Optional[VLBudgetTracker],
    conservative: bool,
) -> None:
    """成功路径分歧最终化（合并阶段串行执行）。

    镜像 ``_run_success_cross_check`` 的非延迟分支：写交叉分歧 → VL 仲裁（保守模式依赖
    跨页累计 tally，故必须合并阶段判定）→ 未裁决（manual）分歧进人工复核队列。
    """
    db.write_cross_divergences(
        page_no=page_num, divs=dcc.divs,
        engine_a=dcc.engine_a, engine_b=dcc.engine_b,
    )
    high = dcc.high
    if high:
        va = get_vision_adapter()
        with _vl_lock:
            arb_result = _arbitrate_high_divergences(
                page_num, high, page_img, va,
                get_vision_bucket(), db, confusion_set,
                conservative=conservative, vl_budget=vl_budget,
            )
        unresolved = arb_result["unresolved"]
        if unresolved:
            db.record_anomaly(
                page_num,
                GlyphVerdict(
                    status="UNKNOWN", confidence=0.4,
                    details=(
                        f"cross_divergence;high={len(high)};"
                        f"arbitrated={len(arb_result['resolved'])};"
                        f"sample={unresolved[0].a_seg}↔{unresolved[0].b_seg}"
                    ),
                ),
                detector_chain=["CrossAlign"],
            )


def _finalize_divergences_tier3(
    page_num: int,
    dcc: _DeferredCrossCheck,
    page_img: Optional["np.ndarray"],
    db: BookDB,
    get_vision_adapter: Callable[[], Optional[VisionRecheckAdapter]],
    get_vision_bucket: Callable[[], Optional[MultiTokenRateLimiter]],
    confusion_set: dict,
    vl_budget: Optional[VLBudgetTracker],
    conservative: bool,
) -> None:
    """Tier3 失败路径分歧最终化（合并阶段串行执行）。

    镜像 ``_run_tier3_divergence`` 的非延迟分支：写交叉分歧 → high 分歧 100% 进人工复核队列 →
    VL 仲裁更新状态。
    """
    db.write_cross_divergences(
        page_no=page_num, divs=dcc.divs,
        engine_a=dcc.engine_a, engine_b=dcc.engine_b,
    )
    high = dcc.high
    if high:
        db.record_anomaly(
            page_num,
            GlyphVerdict(
                status="UNKNOWN", confidence=0.4,
                details=(
                    f"cross_divergence;high={len(high)};"
                    f"sample={high[0].a_seg}↔{high[0].b_seg}"
                ),
            ),
            detector_chain=["CrossAlign"],
        )
        va = get_vision_adapter()
        if va is not None and page_img is not None:
            with _vl_lock:
                _arbitrate_high_divergences(
                    page_num, high, page_img, va,
                    get_vision_bucket(), db, confusion_set,
                    conservative=conservative, vl_budget=vl_budget,
                )


def _process_one_page(page_num: int, page_input: PageInput, ctx: _PageContext) -> _PageOutcome:
    """单页处理（线程本地计算，无共享状态副作用）。

    镜像 ``orchestrate_book`` 主循环页体（L760–960 主体）的逻辑：Tier1 校验 / Tier3 识别 /
    跨引擎分歧对齐 / VLM 仲裁。所有副作用（db 写 / 引擎统计 / tally 累加 / VLM 仲裁）收集进
    ``_PageOutcome``，由合并阶段按页序落地；VLM 调用经 ``_vl_lock`` 串行。

    页图像：若 ``page_input.img`` 为 None（页级并发隔离模式），本函数自行渲染该页
    （每 worker 独立 ``fitz`` 文档，见 ``_render_one_page``）。
    """
    verdict = GlyphVerdict(status="FAIL", confidence=0.0)
    page_trace: list = []
    registry_usage: Optional[tuple] = None
    page_layout = page_input.layout or PageLayout(page_num=page_num)
    final_text = ""
    t1_engine_name = ctx.tier1_candidates[0].meta.name if ctx.tier1_candidates else "unknown"

    # 渲染隔离：并发模式下 page_input.img 为 None，自行渲染本页（每 worker 独立文档）。
    if page_input.img is None:
        rendered = _render_one_page(ctx.pdf_path, page_num, ctx.config)
        page_input = PageInput(page_num=page_num, img=rendered)

    neighbor_texts: list[str] = []
    next_text = ""
    cur_char_boxes = None
    if ctx.tier1_result and page_num < len(ctx.tier1_result.pages):
        cur_p = ctx.tier1_result.pages[page_num]
        cur_text = cur_p.text or _join_paragraphs(cur_p)
        cur_char_boxes = cur_p.char_boxes
        if page_num + 1 < len(ctx.tier1_result.pages):
            nxt = ctx.tier1_result.pages[page_num + 1]
            next_text = nxt.text or _join_paragraphs(nxt)
        if page_num > 0:
            prev = ctx.tier1_result.pages[page_num - 1]
            neighbor_texts.append(prev.text or _join_paragraphs(prev))
        if next_text:
            neighbor_texts.append(next_text)
    else:
        cur_text = ""

    db_ops: list = []

    if cur_text and not page_layout.is_vertical:
        context = DetectorContext(
            page_num=page_num, engine_label=t1_engine_name,
            book_type=ctx.book_type, pub_era=ctx.pub_era,
            resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
        )
        with _vl_lock:
            verdict = ctx.verifier.verify_with_vision(
                cur_text, context,
                page_img=page_input.img if page_input.img is not None else None,
                vision_adapter=ctx.get_vision_adapter(),
            )
        page_trace.append(EngineCallRecord(
            page=page_num, tier=1, engine=t1_engine_name,
            latency_ms=ctx.t1_elapsed_per_page, glyph_status=verdict.status,
            detector_chain=list(ctx.verifier.last_detector_chain),
        ))
        if verdict.status in ("PASS", "RARE"):
            final_text = cur_text
            db_ops.append(functools.partial(ctx.db.init_page, page_num, char_count=len(cur_text), engine_label=t1_engine_name))
            db_ops.append(functools.partial(ctx.db.update_ocr, page_num, status="success", char_count=len(cur_text), latency_ms=ctx.t1_elapsed_per_page))
            db_ops.append(functools.partial(ctx.db.update_verify, page_num, verdict=verdict.status, details=verdict.details or ""))
            _page_conf = (
                ctx.tier1_result.pages[page_num].confidence
                if ctx.tier1_result and page_num < len(ctx.tier1_result.pages)
                else 1.0
            )
            # 注意：conf≤gate 门控「不在此处判定」——保守模式（KZOCR_CONSERVATIVE_MODE）
            # 的自适应门限依赖跨页累计 tally（仅合并阶段可得），故门控决策推迟到
            # _run_book_parallel 合并阶段统一处理（与串行主循环行为等价）。此处先统一
            # 置 imported，合并阶段对低置信度页再覆盖为 pending + 异常。
            db_ops.append(functools.partial(ctx.db.update_import, page_num, status="imported", count=1))
            # 增强路径：成功页跨引擎采样比对（defer 模式，分歧最终化在合并阶段）
            success_divs: list = []
            success_is_consensus = False
            if getattr(ctx.overrides, "enable_cross_check", False) and not ctx.budget.exhausted:
                r = _run_success_cross_check(
                    page_num, cur_text, page_input,
                    ctx.scheduler, ctx.registry, ctx.db,
                    ctx.confusion_set, ctx.budget, ctx.overrides, page_layout,
                    ctx.max_time_per_page_ms,
                    vision_adapter=ctx.get_vision_adapter(),
                    bucket=ctx.get_vision_bucket(),
                    engine_a=t1_engine_name, char_boxes=cur_char_boxes,
                    tally=None, vl_budget=ctx.vl_budget, defer=True,
                )
                if isinstance(r, _DeferredCrossCheck):
                    success_divs = [r]
                    success_is_consensus = r.is_consensus
            # 共识一致页（候选）；合并阶段按保守模式自适应抽样率决定是否实际抽样。
            consensus_sample_request = success_is_consensus
            return _PageOutcome(
                page_num=page_num, verdict=verdict, final_text=final_text,
                appended=True, page_trace=page_trace,
                registry_usage=(ctx.tier1_candidates[0], verdict, ctx.t1_elapsed_per_page),
                char_count=len(cur_text), last_engine=t1_engine_name, last_latency=ctx.t1_elapsed_per_page,
                db_ops=db_ops, success_divs=success_divs,
                success_is_consensus=success_is_consensus,
                consensus_sample_request=consensus_sample_request,
                tier1_passed=True, page_conf=_page_conf,
                page_img=page_input.img,
            )

    # ── Tier3（失败 / 竖排 / 缺文本）──
    result = None
    engine_name = None
    if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not ctx.budget.exhausted:
        tier3 = _safe_select_candidates(ctx.scheduler, ctx.registry, 3, page_input, ctx.budget, page_layout, ctx.overrides)
        timeout_t3 = getattr(ctx.config, "max_time_per_page_ms", 120000) / 1000
        result, engine_name = run_engines_concurrent(tier3, page_input, timeout_s=timeout_t3, max_workers=ctx.concurrency_ctrl.workers)
    if result is not None and engine_name is not None:
        t_elapsed = 500  # placeholder
        tier3_divs: list = []
        if cur_text and result.text:
            dcc = _run_tier3_divergence(
                page_num, cur_text, result.text, page_input,
                ctx.confusion_set, t1_engine_name, engine_name, cur_char_boxes,
                ctx.db, None, ctx.get_vision_adapter, ctx.get_vision_bucket, ctx.vl_budget, defer=True,
            )
            if isinstance(dcc, _DeferredCrossCheck):
                tier3_divs = [dcc]
        vctx = DetectorContext(
            page_num=page_num, engine_label=engine_name,
            book_type=ctx.book_type, pub_era=ctx.pub_era,
            resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
        )
        with _vl_lock:
            verdict = ctx.verifier.verify_with_vision(
                result.text, vctx,
                page_img=page_input.img if page_input.img is not None else None,
                vision_adapter=ctx.get_vision_adapter(),
            )
        _eng = next((e for e in tier3 if e.meta.name == engine_name), None)
        if _eng:
            page_trace.append(EngineCallRecord(
                page=page_num, tier=3, engine=engine_name,
                latency_ms=t_elapsed, glyph_status=verdict.status,
                detector_chain=list(ctx.verifier.last_detector_chain),
            ))
            registry_usage = (_eng, verdict, t_elapsed)
        if verdict.status in ("PASS", "RARE"):
            final_text = result.text

    # ── HumanGate + 逐页进度写（页局部，合并阶段落地）──
    last_engine = page_trace[-1].engine if page_trace else "unknown"
    last_latency = page_trace[-1].latency_ms if page_trace else 0
    char_count = len(final_text) if final_text else 0
    db_ops.append(functools.partial(ctx.db.init_page, page_num, char_count=char_count, engine_label=last_engine))
    ocr_ok = verdict.status in ("PASS", "RARE", "FAIL", "UNKNOWN", "UNCERTAIN")
    db_ops.append(functools.partial(ctx.db.update_ocr, page_num, status="success" if ocr_ok else "failed", char_count=char_count, latency_ms=last_latency))
    db_ops.append(functools.partial(ctx.db.update_verify, page_num, verdict=verdict.status, details=verdict.details or ""))
    _page_conf = (
        ctx.tier1_result.pages[page_num].confidence
        if ctx.tier1_result and page_num < len(ctx.tier1_result.pages) else _CONF_GATE
    )
    if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") or getattr(verdict, "force_review", False) or _page_conf <= _CONF_GATE:
        db_ops.append(functools.partial(ctx.db.record_anomaly, page_num, verdict=verdict, detector_chain=ctx.verifier.last_detector_chain))
    db_ops.append(functools.partial(ctx.db.update_import, page_num, status="imported" if verdict.status in ("PASS", "RARE") else "pending", count=1))

    outcome = _PageOutcome(
        page_num=page_num, verdict=verdict, final_text=final_text,
        appended=bool(final_text), page_trace=page_trace,
        registry_usage=registry_usage,
        char_count=char_count, last_engine=last_engine, last_latency=last_latency,
        db_ops=db_ops, tier3_divs=tier3_divs, page_img=page_input.img,
    )
    if verdict.status in ("FAIL", "UNKNOWN"):
        outcome.failed = True
        outcome.failed_reason = f"All tiers failed. Last: {verdict.details}"
    elif verdict.status == "UNCERTAIN":
        outcome.uncertain = True
        outcome.uncertain_verdict = verdict
    return outcome


def _run_book_parallel(
    pdf_path: str,
    config: "Config",
    budget: "Budget",
    overrides: Optional["EngineOverrides"],
    scheduler: "EngineScheduler",
    registry: "EngineRegistry",
    db: BookDB,
    confusion_set: dict,
    verifier: "GlyphVerifier",
    tier1_result: Optional["BookResult"],
    tier1_candidates: list,
    t1_elapsed_per_page: int,
    concurrency_ctrl: "AdaptiveController",
    vl_budget: Optional["VLBudgetTracker"],
    get_vision_adapter: Callable[[], Optional[VisionRecheckAdapter]],
    get_vision_bucket: Callable[[], Optional[MultiTokenRateLimiter]],
    book_type: str,
    pub_era: str,
    skip_pages: set[int],
    max_time_per_page_ms: int,
    max_workers: int,
) -> tuple[list[str], list[int], dict[int, str], dict[int, "GlyphVerdict"], list, dict[str, int], dict]:
    """页级并发编排（KZOCR_PAGE_PARALLEL=1）：多线程处理各页，合并阶段串行写共享状态。

    返回 ``(pages_text, pages_order, failed_pages, uncertain_pages, trace, engine_usage_counter, tally)``，
    与串行主循环产出等价。页闸（max_pages）在提交任务前切片；时间闸为软约束（提交时已过则不再提交）。
    """
    import fitz

    if os.path.exists(pdf_path):
        page_count = fitz.open(pdf_path).page_count
    else:
        page_count = len(tier1_result.pages) if tier1_result else 0
    # 切片到 max_pages 并跳过已处理页（等价于串行页闸）
    page_nums = [p for p in range(page_count) if p < budget.max_pages and p not in skip_pages]

    ctx = _PageContext(
        config=config, pdf_path=pdf_path, tier1_result=tier1_result,
        tier1_candidates=tier1_candidates, t1_elapsed_per_page=t1_elapsed_per_page,
        overrides=overrides, confusion_set=confusion_set, scheduler=scheduler,
        registry=registry, db=db, budget=budget, verifier=verifier, book_type=book_type,
        pub_era=pub_era, concurrency_ctrl=concurrency_ctrl, max_time_per_page_ms=max_time_per_page_ms,
        get_vision_adapter=get_vision_adapter, get_vision_bucket=get_vision_bucket, vl_budget=vl_budget,
    )

    pages_text: list[str] = []
    pages_order: list[int] = []
    failed_pages: dict[int, str] = {}
    uncertain_pages: dict[int, "GlyphVerdict"] = {}
    trace: list = []
    engine_usage_counter: dict[str, int] = {}
    tally: dict = {"div": 0, "high": 0}

    if not page_nums:
        return pages_text, pages_order, failed_pages, uncertain_pages, trace, engine_usage_counter, tally

    def _work(pn: int) -> _PageOutcome:
        return _process_one_page(pn, PageInput(page_num=pn, img=None), ctx)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        # executor.map 按提交顺序返回，天然按页序；worker 内部各自渲染、互不共享状态。
        outcomes = list(ex.map(_work, page_nums))

    # ── 合并阶段：按页序串行落地全部共享状态（规避 sqlite/registry 竞态）──
    base_rate = getattr(overrides, "consensus_sample_rate", 0.0)
    for i, outcome in enumerate(outcomes):
        # 工作流 B：基于「已合并页」累计 tally 的「自适应质量参数」（保守模式生效时）。
        # 合并阶段才持有跨页 tally，故 conf 门控 / 共识抽样率统一在此判定，与串行主循环等价。
        rate, gate = _adaptive_quality_params(tally, i, base_rate)
        is_conf_low = outcome.tier1_passed and outcome.page_conf <= gate
        # 1) 页局部 db 写（init/ocr/verify/imported 顺序应用；各 op 以 page_num 为键）
        for op in outcome.db_ops:
            op()
        if is_conf_low:
            # 低置信度 PASS 页：覆盖为待人工复核 + 记录异常（与串行 early-return 等价），
            # 并跳过跨引擎比对分歧最终化（串行对低置信度页亦不进入跨引擎比对路径）。
            db.update_import(outcome.page_num, status="pending", count=1)
            db.record_anomaly(
                outcome.page_num,
                GlyphVerdict(
                    status=outcome.verdict.status,
                    confidence=outcome.page_conf,
                    details=(
                        f"conf_low;engine_conf={outcome.page_conf:.3f};"
                        f"gate={gate:.2f}"
                    ),
                    force_review=True,
                ),
                detector_chain=["ConfGate"],
            )
        else:
            # 2) 分歧最终化（含延迟 VLM 仲裁，_vl_lock 串行）；conservative 取累计本页前 tally
            conservative = _is_conservative(tally)
            for dcc in outcome.success_divs:
                _finalize_divergences_success(
                    outcome.page_num, dcc, outcome.page_img, db,
                    get_vision_adapter, get_vision_bucket, confusion_set, vl_budget, conservative,
                )
            for dcc in outcome.tier3_divs:
                _finalize_divergences_tier3(
                    outcome.page_num, dcc, outcome.page_img, db,
                    get_vision_adapter, get_vision_bucket, confusion_set, vl_budget, conservative,
                )
            # 2b) tally 累加（仅最终化的分歧计入；低置信度页跳过跨引擎比对，不计入）。
            # 注意：tally 必须取自 _DeferredCrossCheck 的 tally_div/tally_high（worker 不写
            # outcome.tally_div），否则并行路径 tally 恒为 0，保守模式判定失效。
            for dcc in outcome.success_divs:
                tally["div"] += dcc.tally_div
                tally["high"] += dcc.tally_high
            for dcc in outcome.tier3_divs:
                tally["div"] += dcc.tally_div
                tally["high"] += dcc.tally_high
            # 3) 共识一致页抽样送视觉仲裁（合并阶段、_vl_lock 串行；使用自适应 rate）
            if outcome.consensus_sample_request and rate > 0:
                with _vl_lock:
                    _sample_consensus_error(
                        outcome.page_num, outcome.final_text, outcome.page_img,
                        get_vision_adapter(), db,
                        rate,
                        get_vision_bucket(), vl_budget=vl_budget,
                    )
        # 4) tally 已在 else 分支（分歧最终化后）按最终化分歧累加，供后续页保守模式判定
        # 5) 引擎使用统计（主线程串行 record）
        if outcome.registry_usage is not None:
            _record_engine_usage(
                registry, outcome.registry_usage[0], outcome.registry_usage[1],
                outcome.registry_usage[2], engine_usage_counter,
            )
        # 6) 文本 / 轨迹 / HumanGate 字典
        trace.extend(outcome.page_trace)
        if outcome.appended:
            pages_text.append(outcome.final_text)
            pages_order.append(outcome.page_num)
        if outcome.failed:
            failed_pages[outcome.page_num] = outcome.failed_reason
        elif outcome.uncertain:
            uncertain_pages[outcome.page_num] = outcome.uncertain_verdict

    return pages_text, pages_order, failed_pages, uncertain_pages, trace, engine_usage_counter, tally


def orchestrate_book(
    pdf_path: str,
    book_code: str | None,
    config: Config,
    registry: EngineRegistry,
    overrides: Optional[EngineOverrides] = None,
) -> BookResult:
    """全书编排主循环（§7.1）。

    Args:
        pdf_path: PDF 路径。
        book_code: 书籍编码（用于报告与 trace 文件名）。
        config: 配置对象，需含 max_pages / total_timeout_s / max_time_per_page_ms /
            allow_cloud_vision / book_type / pub_era / output_dir / trace_dir。
        registry: 已构建并 probe 的引擎注册中心（B5，由调用方 E5 传入）。
        overrides: 引擎覆盖（pinned_engine / prefer）。
    """
    scheduler_cfg = getattr(config, "scheduler", None)
    if scheduler_cfg is not None:
        budget = Budget(
            max_pages=scheduler_cfg.max_pages,
            max_wall_clock_ms=scheduler_cfg.total_timeout_s * 1000,
            max_time_per_page_ms=scheduler_cfg.max_time_per_page_ms,
            allow_cloud_vision=scheduler_cfg.allow_cloud_vision,
        )
    else:
        # 向后兼容：旧调用方可能传 attribute bag 而非 Config 实例
        budget = Budget(
            max_pages=getattr(config, "max_pages", 50),
            max_wall_clock_ms=int(getattr(config, "total_timeout_s", 7200)) * 1000,
            max_time_per_page_ms=getattr(config, "max_time_per_page_ms", 120000),
            allow_cloud_vision=bool(getattr(config, "allow_cloud_vision", False)),
        )
    # W4 VL 仲裁预算守卫：限制单次编排（per_run）与跨书当日（per_day）视觉仲裁调用数，
    # 防止 GLM-4V-Flash 等付费端点失控开销。默认 0=不限。
    vl_budget = VLBudgetTracker(
        VLBudgetConfig(
            per_run=scheduler_cfg.vl_budget_per_run
            if scheduler_cfg is not None
            else int(getattr(config, "vl_budget_per_run", 0) or 0),
            per_day=scheduler_cfg.vl_budget_per_day
            if scheduler_cfg is not None
            else int(getattr(config, "vl_budget_per_day", 0) or 0),
        )
    )
    verifier = GlyphVerifier()
    # 形近字黑名单（供跨引擎分歧对齐标记 high 优先级，失败路径比对时复用）
    confusion_set = load_confusion_set()
    # 视觉回看适配器（惰性初始化，仅在需要时创建）
    vision_adapter: Optional[VisionRecheckAdapter] = None
    _vision_adapter_attempted = False

    def _get_vision_adapter() -> Optional[VisionRecheckAdapter]:
        nonlocal vision_adapter, _vision_adapter_attempted
        if _vision_adapter_attempted:
            return vision_adapter
        _vision_adapter_attempted = True
        if getattr(config, "allow_cloud_vision", False):
            # 视觉回看 MUST use a DIFFERENT model/provider from the OCR engine
            # to ensure independent verification.
            # Try GLM first (free tier), then ModelScope, then SenseNova.
            try:
                vision_adapter = VisionRecheckAdapter.glm_default()
                if vision_adapter.api_key:
                    return vision_adapter
            except Exception:
                pass
            try:
                vision_adapter = VisionRecheckAdapter.modelscope_default()
                if vision_adapter.api_key:
                    return vision_adapter
            except Exception:
                pass
            try:
                vision_adapter = VisionRecheckAdapter.sensenova_default()
                if vision_adapter.api_key:
                    return vision_adapter
            except Exception:
                pass
        return None

    # 视觉仲裁共享进程级限流（复用 MultiTokenRateLimiter；默认关，仅 allow_cloud_vision 时用到）
    vision_bucket: Optional[MultiTokenRateLimiter] = None

    def _get_vision_bucket() -> Optional[MultiTokenRateLimiter]:
        nonlocal vision_bucket
        if vision_bucket is None:
            vision_bucket = MultiTokenRateLimiter(
                tokens=30, window_seconds=60, key="vision_recheck"
            )
        return vision_bucket

    scheduler = EngineScheduler()
    trace: list[EngineCallRecord] = []
    start_time = time.monotonic()

    pages_text: list[str] = []
    # 与 pages_text 一一对应的真实页号（0-based，含失败页缺口），
    # 用于重建 PageResult 时保留真实 page_num，避免按位置索引导致
    # Tier1 字符框合并错位（见 _build_pages_result / _merge_tier1_char_boxes）。
    pages_order: list[int] = []
    failed_pages: dict[int, str] = {}
    uncertain_pages: dict[int, GlyphVerdict] = {}
    engine_usage_counter: dict[str, int] = {}

    book_type = getattr(config, "book_type", "") or ""
    pub_era = getattr(config, "pub_era", "") or ""
    title = getattr(config, "title", None) or book_code or "unknown"

    # ── F3/429: 引擎限流退避状态（engine_name → 退避到期时间）──
    _rate_limited_until: dict[str, float] = {}
    if overrides is not None:
        overrides.rate_limited_until = _rate_limited_until
    # ── v0.9: 并发控制 ──
    concurrency_ctrl = AdaptiveController(
        base_workers=min(3, len(registry.list())),
        min_workers=1,
        max_workers=min(5, len(registry.list())),
    )

    # ── F2: 初始化 DB（沿用 config.db_dir 或 KZOCR_DB_DIR）──
    db_dir = getattr(config, "db_dir", "") or os.environ.get("KZOCR_DB_DIR", "")
    db = BookDB(book_code or "unknown", db_dir=db_dir)

    # ── 第 1 步：Tier1 全书处理（只执行一次）──
    tier1_candidates = _safe_select_candidates(
        scheduler, registry, 1,
        PageInput(page_num=0, img=None), budget, None, overrides,
    )
    tier1_result: Optional[BookResult] = None
    t1_elapsed_per_page = 0
    if tier1_candidates:
        t0 = time.monotonic()
        try:
            adapter = tier1_candidates[0].adapter
            if adapter is None:
                raise RuntimeError("Tier1 engine has no adapter injected")
            tier1_result = adapter.run_book(
                pdf_path, book_code=book_code, max_pages=budget.max_pages
            )
        except Exception as exc:
            _logger.error("[orchestrator] Tier 1 book engine failed: %s", exc)
        else:
            t1_elapsed = int((time.monotonic() - t0) * 1000)
            if tier1_result and tier1_result.pages:
                t1_elapsed_per_page = (
                    t1_elapsed // len(tier1_result.pages)
                    if tier1_result.pages
                    else t1_elapsed
                )

    # ── 第 2 步：逐页处理 ──
    # F3: 提前查询 DB 进度用于 resume/retry-failed 跳过
    skip_pages: set[int] = set()
    if overrides and (overrides.resume or overrides.retry_failed):
        try:
            for p in db.get_all_progress():
                if overrides.retry_failed:
                    if p["ocr_status"] in ("success",):
                        skip_pages.add(p["page_num"])
                elif overrides.resume:
                    if p["ocr_status"] == "success":
                        skip_pages.add(p["page_num"])
        except Exception:
            _logger.warning("[orchestrator] resume query failed, falling back to full run")
        _logger.info(
            "[orchestrator] resume mode: skip_pages=%d",
            len(skip_pages),
        )

    # 全书跨引擎分歧累计（供 high 占比二级判据；见 _is_conservative / v4 扩面结论）
    tally: dict = {"div": 0, "high": 0}

    # ── 页级并发编排（默认关；KZOCR_PAGE_PARALLEL=1 开启）──
    # 开启时多线程处理各页、合并阶段串行写共享状态（规避 sqlite/registry 竞态），
    # 其余逻辑与串行路径完全等价。默认关闭，冻结栈行为不变。
    page_parallel = getattr(config, "page_parallel", False) or _safe_bool(
        os.environ.get("KZOCR_PAGE_PARALLEL", ""), False
    )
    max_time_per_page_ms = budget.max_time_per_page_ms
    if page_parallel:
        max_workers = getattr(config, "page_workers", 0) or 0
        if max_workers <= 0:
            max_workers = min(os.cpu_count() or 4, 4)
        (
            pages_text, pages_order, failed_pages, uncertain_pages, trace,
            engine_usage_counter, tally,
        ) = _run_book_parallel(
            pdf_path, config, budget, overrides, scheduler, registry, db,
            confusion_set, verifier, tier1_result, tier1_candidates,
            t1_elapsed_per_page, concurrency_ctrl, vl_budget,
            _get_vision_adapter, _get_vision_bucket,
            book_type, pub_era, skip_pages, max_time_per_page_ms, max_workers,
        )
        return _finalize_book(
            book_code=book_code, config=config, db=db, registry=registry, trace=trace,
            pages_text=pages_text, pages_order=pages_order,
            failed_pages=failed_pages, uncertain_pages=uncertain_pages,
            tier1_result=tier1_result, engine_usage_counter=engine_usage_counter,
            start_time=start_time, vl_budget=vl_budget, title=title,
        )

    for page_num, page_input in enumerate(render_pages(pdf_path, config)):
        # F3: 跳过已处理页
        if page_num in skip_pages:
            _logger.debug("[orchestrator] page=%d 已处理，跳过（resume）", page_num)
            continue
        # 进度日志（每 5 页）
        if page_num % 5 == 0:
            elapsed_m = int((time.monotonic() - start_time) / 60)
            _logger.info(
                "[progress] book=%s page=%d/%d elapsed=%dm tier1=%s",
                book_code or "unknown", page_num + 1, budget.max_pages, elapsed_m,
                tier1_candidates[0].meta.name if tier1_candidates else "none",
            )

        # B6 双闸：页数闸
        if page_num >= budget.max_pages:
            _logger.warning("[orchestrator] page_limit=%d reached, truncating", budget.max_pages)
            budget.exhaust()
            break
        # B6 双闸：时间闸
        if not budget.check_time_budget(time.monotonic() - start_time):
            _logger.warning("[orchestrator] total_timeout reached at page=%d", page_num)
            budget.exhaust()
            break

        page_trace: list[EngineCallRecord] = []
        verdict = GlyphVerdict(status="FAIL", confidence=0.0)
        page_layout = page_input.layout or PageLayout(page_num=page_num)
        final_text = ""
        t1_engine_name = tier1_candidates[0].meta.name if tier1_candidates else "unknown"

        # 邻页文本（供 Leakage/CharCountSpike 检测器，N1）
        neighbor_texts: list[str] = []
        next_text = ""
        cur_char_boxes = None
        if tier1_result and page_num < len(tier1_result.pages):
            cur_p = tier1_result.pages[page_num]
            cur_text = cur_p.text or _join_paragraphs(cur_p)
            # 逐字框（供 Box-Guided VL 仲裁）；与 cur_text 平行（1 框/字），
            # 由 align_boxes_to_text 在调用点校验对齐。
            cur_char_boxes = cur_p.char_boxes
            if page_num + 1 < len(tier1_result.pages):
                nxt = tier1_result.pages[page_num + 1]
                next_text = nxt.text or _join_paragraphs(nxt)
            if page_num > 0:
                prev = tier1_result.pages[page_num - 1]
                neighbor_texts.append(prev.text or _join_paragraphs(prev))
            if next_text:
                neighbor_texts.append(next_text)
        else:
            cur_text = ""

        # ── Tier1 结果验证（竖排页跳过 Tier1，转 Tier2/3，§4.1）──
        if cur_text and not page_layout.is_vertical:
            context = DetectorContext(
                page_num=page_num,
                engine_label=t1_engine_name,
                book_type=book_type,
                pub_era=pub_era,
                resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
            )
            verdict = verifier.verify_with_vision(
                cur_text, context,
                page_img=page_input.img if page_input.img is not None else None,
                vision_adapter=_get_vision_adapter(),
            )
            page_trace.append(
                EngineCallRecord(
                    page=page_num, tier=1, engine=t1_engine_name,
                    latency_ms=t1_elapsed_per_page, glyph_status=verdict.status,
                    detector_chain=list(verifier.last_detector_chain),
                )
            )
            if verdict.status in ("PASS", "RARE"):
                final_text = cur_text
                pages_text.append(final_text)
                pages_order.append(page_num)
                _record_engine_usage(
                    registry, tier1_candidates[0], verdict, t1_elapsed_per_page, engine_usage_counter
                )
                # F2: 写入逐页进度
                db.init_page(page_num, char_count=len(cur_text), engine_label=t1_engine_name)
                db.update_ocr(page_num, status="success", char_count=len(cur_text), latency_ms=t1_elapsed_per_page)
                db.update_verify(page_num, verdict=verdict.status, details=verdict.details or "")

                # ── 工作流 B：保守模式自适应质量参数 ──
                # 基于跨页累计 tally 与已处理页数动态返回 (抽样率, conf门限)；
                # 默认关（_CONSERVATIVE_MODE=False）时回落基线值，行为不变。
                rate, gate = _adaptive_quality_params(
                    tally, page_num, getattr(overrides, "consensus_sample_rate", 0.0)
                )

                # ── conf≤gate 门控：低置信度 PASS 页挂起待人工复核 ──
                # 门控必须在 PASS 分支的 continue 之前判定：兜底门控（本循环末尾）
                # 对 PASS 页不可达（已提前 continue），否则低置信度页会被直接 imported。
                _page_conf = (
                    tier1_result.pages[page_num].confidence
                    if tier1_result and page_num < len(tier1_result.pages)
                    else 1.0
                )
                if _page_conf <= gate:
                    db.update_import(page_num, status="pending", count=1)
                    db.record_anomaly(
                        page_num,
                        GlyphVerdict(
                            status=verdict.status,
                            confidence=_page_conf,
                            details=(
                                f"conf_low;engine_conf={_page_conf:.3f};"
                                f"gate={gate:.2f}"
                            ),
                            force_review=True,
                        ),
                        detector_chain=["ConfGate"],
                    )
                    trace.extend(page_trace)
                    continue

                db.update_import(page_num, status="imported", count=1)

                # ── 增强路径：成功页跨引擎采样比对（enable_cross_check 时激活）──
                # 对成功页追加 Tier2 引擎做交叉验证，捕获 GlyphVerifier 抓不到的字符级错误
                # （因中文形近字 GlyphVerifier 无法判对错，需要双引擎比对）。
                if getattr(overrides, "enable_cross_check", False) and not budget.exhausted:
                    is_consensus = _run_success_cross_check(
                        page_num, cur_text, page_input,
                        scheduler, registry, db,
                        confusion_set, budget, overrides, page_layout,
                        budget.max_time_per_page_ms,
                        vision_adapter=_get_vision_adapter(),
                        bucket=_get_vision_bucket(),
                        engine_a=t1_engine_name,
                        char_boxes=cur_char_boxes,
                        tally=tally,
                        vl_budget=vl_budget,
                    )
                    # 共识一致页抽样送视觉仲裁（覆盖「两引擎同错」盲区）；
                    # 抽样率取保守模式自适应值（rate）。
                    if is_consensus and rate > 0:
                        _sample_consensus_error(
                            page_num, cur_text,
                            page_input.img if page_input.img is not None else None,
                            _get_vision_adapter(), db,
                            rate,
                            _get_vision_bucket(),
                            vl_budget=vl_budget,
                        )

                trace.extend(page_trace)
                continue

        # ── Tier3：本地中医 LLM（跳过 Tier2 云端，直接到此）──
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not budget.exhausted:
            tier3 = _safe_select_candidates(
                scheduler, registry, 3, page_input, budget, page_layout, overrides
            )
            timeout_t3 = getattr(config, "max_time_per_page_ms", 120000) / 1000
            result, engine_name = run_engines_concurrent(
                tier3, page_input, timeout_s=timeout_t3,
                max_workers=concurrency_ctrl.workers,
            )
        if result is not None and engine_name is not None:
            t_elapsed = 500  # placeholder
            # ── 跨引擎分歧对齐（借鉴 ocr_pipeline_v2）：Tier1 文本 vs Tier3 文本 ──
            # 失败路径上两引擎文本共存，比对提取分歧（数字/剂量+形近黑名单 high 优先），
            # 供 HumanGate / 视觉仲裁。纯函数无网络，失败页量小，直接落库。
            if cur_text and result.text:
                _run_tier3_divergence(
                    page_num, cur_text, result.text, page_input,
                    confusion_set, t1_engine_name, engine_name, cur_char_boxes,
                    db, tally, _get_vision_adapter, _get_vision_bucket, vl_budget,
                )
            vctx = DetectorContext(
                    page_num=page_num, engine_label=engine_name,
                    book_type=book_type, pub_era=pub_era,
                    resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
                )
            verdict = verifier.verify_with_vision(
                result.text, vctx,
                page_img=page_input.img if page_input.img is not None else None,
                vision_adapter=_get_vision_adapter(),
            )
            _eng = next((e for e in tier3 if e.meta.name == engine_name), None)
            if _eng:
                page_trace.append(
                    EngineCallRecord(
                        page=page_num, tier=3, engine=engine_name,
                        latency_ms=t_elapsed, glyph_status=verdict.status,
                        detector_chain=list(verifier.last_detector_chain),
                    )
                )
                _record_engine_usage(registry, _eng, verdict, t_elapsed, engine_usage_counter)
            if verdict.status in ("PASS", "RARE"):
                final_text = result.text
                pages_text.append(final_text)
                pages_order.append(page_num)

        # ── HumanGate ──
        if verdict.status in ("FAIL", "UNKNOWN"):
            failed_pages[page_num] = f"All tiers failed. Last: {verdict.details}"
            _logger.warning("[orchestrator] page=%d all tiers failed: %s", page_num, verdict.details)
        elif verdict.status == "UNCERTAIN":
            uncertain_pages[page_num] = verdict
            if final_text:
                pages_text.append(final_text)
                pages_order.append(page_num)

        trace.extend(page_trace)

        # F2: 写入逐页进度
        last_engine = page_trace[-1].engine if page_trace else "unknown"
        last_latency = page_trace[-1].latency_ms if page_trace else 0
        char_count = len(final_text) if final_text else 0
        db.init_page(page_num, char_count=char_count, engine_label=last_engine)
        ocr_ok = verdict.status in ("PASS", "RARE", "FAIL", "UNKNOWN", "UNCERTAIN")
        db.update_ocr(
            page_num, status="success" if ocr_ok else "failed",
            char_count=char_count, latency_ms=last_latency,
        )
        db.update_verify(page_num, verdict=verdict.status, details=verdict.details or "")
        # 异常入队条件：验证未通过 / 强制复核 / 引擎置信度 ≤ 门限
        _page_conf = (tier1_result.pages[page_num].confidence
                      if tier1_result and page_num < len(tier1_result.pages) else _CONF_GATE)
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") or getattr(verdict, "force_review", False) or _page_conf <= _CONF_GATE:
            db.record_anomaly(page_num, verdict=verdict, detector_chain=verifier.last_detector_chain)
        db.update_import(
            page_num, status="imported" if verdict.status in ("PASS", "RARE") else "pending", count=1,
        )

    # ── 书完成后处理（串行路径）──
    return _finalize_book(
        book_code=book_code, config=config, db=db, registry=registry, trace=trace,
        pages_text=pages_text, pages_order=pages_order,
        failed_pages=failed_pages, uncertain_pages=uncertain_pages,
        tier1_result=tier1_result, engine_usage_counter=engine_usage_counter,
        start_time=start_time, vl_budget=vl_budget, title=title,
    )


def _finalize_book(
    *,
    book_code: str | None,
    config: "Config",
    db: BookDB,
    registry: "EngineRegistry",
    trace: list,
    pages_text: list[str],
    pages_order: list[int],
    failed_pages: dict[int, str],
    uncertain_pages: dict[int, "GlyphVerdict"],
    tier1_result: Optional["BookResult"],
    engine_usage_counter: dict[str, int],
    start_time: float,
    vl_budget: Optional["VLBudgetTracker"],
    title: str,
) -> "BookResult":
    """书完成后处理（串行 / 并行路径共用）。

    持久化引擎基准 + 写 trace + 引擎报告 + VL 预算对账 + 失败率告警 + benchmark 汇总 +
    关闭 DB + 合并 Tier1 字符框 + 返回 ``BookResult``。串行与页级并发两种路径产出的最终
    ``BookResult`` 经此函数统一收口，保证行为一致。
    """
    registry.persist_benchmarks()

    trace_dir = getattr(config, "trace_dir", None) or os.path.join(
        getattr(config, "output_dir", "") or os.getcwd(), "trace"
    )
    _write_trace(trace_dir, book_code, trace)

    elapsed_s = time.monotonic() - start_time
    _log_engine_report(
        book_code, pages_text, failed_pages, uncertain_pages, engine_usage_counter, elapsed_s
    )
    # W4 VL 预算使用对账（仅在任一维度设限时有意义）
    if vl_budget is not None and (vl_budget.per_run or vl_budget.per_day):
        _logger.info(
            "[orchestrator] VL budget usage book=%s %s",
            book_code, vl_budget.summary(),
        )

    total = len(pages_text) + len(failed_pages) + len(uncertain_pages)
    failed_ratio = len(failed_pages) / max(total, 1)
    if failed_ratio > 0.3:
        _logger.error(
            "[orchestrator] book=%s failed_ratio=%.2f exceeds CRITICAL threshold (30%%)",
            book_code, failed_ratio,
        )
    elif failed_ratio > 0.1:
        _logger.warning(
            "[orchestrator] book=%s failed_ratio=%.2f exceeds threshold (10%%)",
            book_code, failed_ratio,
        )

    # 写入 benchmark 汇总
    _total_pages = len(pages_text) + len(failed_pages) + len(uncertain_pages)
    _success = len(pages_text)
    _fail = len(failed_pages)
    _total_latency = sum(r.latency_ms for r in trace)
    if book_code:
        db.write_benchmark(
            book_code=book_code,
            engine=",".join(sorted(engine_usage_counter.keys())) or "none",
            total_pages=_total_pages,
            success_pages=_success,
            fail_pages=_fail,
            total_latency_ms=_total_latency,
            total_elapsed_s=elapsed_s,
        )

    # F2: 关闭 DB
    db.close()

    final_pages = _build_pages_result(pages_text, pages_order)
    # 把 Tier1 适配器产出的字符级 bbox 带回最终页（adapter.run_book 已产出 char_boxes，
    # 但上面用 pages_text 重建页时丢弃了；按 page_num 合并回来）
    _merge_tier1_char_boxes(final_pages, tier1_result)

    return BookResult(
        book_code=book_code or "unknown",
        title=title,
        pages=final_pages,
        failed_pages=failed_pages,
        uncertain_pages=uncertain_pages,
        engine_trace=trace,
    )


def _join_paragraphs(page: PageResult) -> str:
    """将 PageResult 的段落聚合为纯文本（B2 兼容：text 为空时回退）。"""
    parts = []
    for para in page.paragraphs:
        parts.append("".join(line.final or line.consensus or "" for line in para.lines))
    return "\n".join(parts)
