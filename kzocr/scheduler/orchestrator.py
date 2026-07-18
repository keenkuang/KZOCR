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
from typing import Optional

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
from kzocr.scheduler.scheduler import Budget, EngineScheduler, EngineOverrides
from kzocr.scheduler.verifier import DetectorContext, GlyphVerifier, VisionRecheckAdapter
from kzocr.scheduler.cross_align import run_cross_align, load_confusion_set
from kzocr.storage.db import BookDB
from kzocr.scheduler.concurrency import run_engines_concurrent, AdaptiveController
from kzocr.engines.ratelimit import MultiTokenRateLimiter
import numpy as np

# ── conf≤gate 置信度门控阈值 ──
# 引擎识别置信度 ≤ 此值的页在通过字形校验后仍挂起待人工复核（不自动入库）。
# 可用环境变量 KZOCR_CONF_GATE 调整（默认 0.90，与 conf≤0.90 门控一致）。
_CONF_GATE = float(os.environ.get("KZOCR_CONF_GATE", "0.90"))

_logger = logging.getLogger(__name__)


def render_pages(pdf_path: str, config=None, dpi: int = 150):
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


def _safe_select_candidates(scheduler, registry, tier, page_input, budget, page_layout, overrides):
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


def _page_info(page_input: PageInput, page_layout: Optional[PageLayout]):
    from kzocr.scheduler.scheduler import PageInfo

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
    overrides,
    page_layout: Optional[PageLayout],
    max_time_per_page_ms: int,
    engine_a: str = "tier1",
) -> bool:
    """成功页跨引擎采样比对：Tier1 成功页追加 Tier2 引擎交叉验证。

    纯增强（try/except 不阻断主流程）；无 Tier2 候选时静默跳过（本机无 GPU/密钥时正常行为）。
    High 优先分歧经 record_anomaly 入 M4 队列；allow_cloud_vision 时可选调视觉仲裁。

    Returns:
        True  = 成功运行且无分歧（共识一致页），供调用方做共识错误抽样
        False = 未运行 / 无 Tier2 / 文本为空 / 有分歧
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
            engine_a=engine_a, engine_b=tier2[0].meta.name,
        )
        if not divs:
            return True  # 无分歧 → 共识一致页

        db.write_cross_divergences(
            page_no=page_num, divs=divs,
            engine_a=engine_a, engine_b=tier2[0].meta.name,
        )
        high = [d for d in divs if d.priority == "high"]
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
            # TODO: allow_cloud_vision 时可调 arbitrate_divergence 对 high 分歧执行 VL 仲裁
            # 复用现有逻辑（orchestrator.py:437-454），record_anomaly 后调用仲裁更新分歧状态
        return False  # 有分歧 → 非共识页
    except Exception as exc:
        _logger.warning("[orchestrator] success cross-check failed page=%d: %s", page_num, exc)
        return False


def _sample_consensus_error(
    page_num: int,
    text: str,
    page_img,
    vision_adapter: Optional[VisionRecheckAdapter],
    db: BookDB,
    sample_rate: float,
    bucket: Optional[MultiTokenRateLimiter],
) -> None:
    """共识一致页抽样送视觉仲裁：覆盖「两引擎同错」盲区。

    对两引擎文本一致的页面，以 sample_rate 概率抽样，调 VL 模型做整页视觉核对。
    - 有 VL 且图像可用 → 执行 recheck，FAIL/UNKNOWN 结果入 ConsensusErrorArbitration 队列
    - 无 VL / 无图像 → 记录抽样命中标记（no_vision_skip），留待人工复核
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

    try:
        if bucket is not None:
            bucket.acquire()
        verdict = vision_adapter.recheck(
            text=text, page_img=page_img,
            engine_label="consensus-check",
        )
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
    page_count: int,
) -> list[PageResult]:
    """将 pages_text 转换为 BookResult 所需的 list[PageResult]。"""
    return [PageResult(page_num=i, text=t) for i, t in enumerate(pages_text)]


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


def _log_engine_report(book_code, pages_text, failed_pages, uncertain_pages, counter, elapsed_s):
    _logger.info(
        "[orchestrator] book=%s pages=%d failed=%d uncertain=%d engine_usage=%s elapsed=%.1fs",
        book_code or "unknown",
        len(pages_text),
        len(failed_pages),
        len(uncertain_pages),
        counter,
        elapsed_s,
    )


def orchestrate_book(
    pdf_path: str,
    book_code: str | None,
    config,
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
    budget = Budget(
        max_pages=getattr(config, "max_pages", 50),
        max_wall_clock_ms=int(getattr(config, "total_timeout_s", 7200)) * 1000,
        max_time_per_page_ms=getattr(config, "max_time_per_page_ms", 120000),
        allow_cloud_vision=bool(getattr(config, "allow_cloud_vision", False)),
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
        if tier1_result and page_num < len(tier1_result.pages):
            cur_p = tier1_result.pages[page_num]
            cur_text = cur_p.text or _join_paragraphs(cur_p)
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
                _record_engine_usage(
                    registry, tier1_candidates[0], verdict, t1_elapsed_per_page, engine_usage_counter
                )
                # F2: 写入逐页进度
                db.init_page(page_num, char_count=len(cur_text), engine_label=t1_engine_name)
                db.update_ocr(page_num, status="success", char_count=len(cur_text), latency_ms=t1_elapsed_per_page)
                db.update_verify(page_num, verdict=verdict.status, details=verdict.details or "")

                # ── conf≤gate 门控：低置信度 PASS 页挂起待人工复核 ──
                # 门控必须在 PASS 分支的 continue 之前判定：兜底门控（本循环末尾）
                # 对 PASS 页不可达（已提前 continue），否则低置信度页会被直接 imported。
                _page_conf = (
                    tier1_result.pages[page_num].confidence
                    if tier1_result and page_num < len(tier1_result.pages)
                    else 1.0
                )
                if _page_conf <= _CONF_GATE:
                    db.update_import(page_num, status="pending", count=1)
                    db.record_anomaly(
                        page_num,
                        GlyphVerdict(
                            status=verdict.status,
                            confidence=_page_conf,
                            details=(
                                f"conf_low;engine_conf={_page_conf:.3f};"
                                f"gate={_CONF_GATE:.2f}"
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
                        engine_a=t1_engine_name,
                    )
                    # 共识一致页抽样送视觉仲裁（覆盖「两引擎同错」盲区）
                    if is_consensus and getattr(overrides, "consensus_sample_rate", 0.0) > 0:
                        _sample_consensus_error(
                            page_num, cur_text,
                            page_input.img if page_input.img is not None else None,
                            _get_vision_adapter(), db,
                            overrides.consensus_sample_rate,
                            _get_vision_bucket(),
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
                try:
                    divs = run_cross_align(
                        page_num, cur_text, result.text,
                        confusion_set=confusion_set,
                        engine_a=t1_engine_name, engine_b=engine_name,
                    )
                    if divs:
                        db.write_cross_divergences(
                            page_no=page_num, divs=divs,
                            engine_a=t1_engine_name, engine_b=engine_name,
                        )
                        # M4 复核队列规则：high 优先级分歧（数字/剂量、形近字）100% 进人工复核
                        high = [d for d in divs if d.priority == "high"]
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
                            # 4.3 分歧级视觉仲裁（Box-Guided VL，退化模式）：
                            # 仅 allow_cloud_vision 且 page_img 非空时激活（等同 --enable-arb）。
                            # 高优先级分歧经 VL 仲裁后更新状态；manual/both_wrong 已在上面入复核队列。
                            va = _get_vision_adapter()
                            if va is not None and page_input.img is not None:
                                bucket = _get_vision_bucket()
                                for d in high:
                                    try:
                                        arb = va.arbitrate_divergence(
                                            d, page_input.img,
                                            confusion_set=confusion_set, bucket=bucket,
                                        )
                                        db.update_cross_divergence_status(
                                            page_num, d.div_type, d.a_seg, d.b_seg,
                                            arb.decision,
                                        )
                                    except Exception as exc:  # 视觉仲裁属增强，绝不阻断主流程
                                        _logger.warning(
                                            "[orchestrator] cross_arbitrate failed page=%d: %s",
                                            page_num, exc,
                                        )
                except Exception as exc:  # 分歧比对属增强，绝不阻断主流程
                    _logger.warning("[orchestrator] cross_align failed page=%d: %s", page_num, exc)
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

        # ── HumanGate ──
        if verdict.status in ("FAIL", "UNKNOWN"):
            failed_pages[page_num] = f"All tiers failed. Last: {verdict.details}"
            _logger.warning("[orchestrator] page=%d all tiers failed: %s", page_num, verdict.details)
        elif verdict.status == "UNCERTAIN":
            uncertain_pages[page_num] = verdict
            if final_text:
                pages_text.append(final_text)

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

    # ── 书完成后处理 ──
    registry.persist_benchmarks()

    trace_dir = getattr(config, "trace_dir", None) or os.path.join(
        getattr(config, "output_dir", "") or os.getcwd(), "trace"
    )
    _write_trace(trace_dir, book_code, trace)

    elapsed_s = time.monotonic() - start_time
    _log_engine_report(
        book_code, pages_text, failed_pages, uncertain_pages, engine_usage_counter, elapsed_s
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

    final_pages = _build_pages_result(pages_text, len(pages_text))
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
