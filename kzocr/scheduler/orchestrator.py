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
from kzocr.scheduler.verifier import DetectorContext, GlyphVerifier
from kzocr.security.egress import validate_url

_logger = logging.getLogger(__name__)


def render_pages(pdf_path: str, config=None, dpi: int = 150):
    """流式生成逐页 PageInput（N2）。真实渲染复用 engine/run.py:_pdf_page_to_numpy。

    测试可 monkeypatch 本函数以 mock 渲染，避免依赖真实 PDF/网络。
    """
    import fitz  # 懒加载，避免无 PDF 场景下强制依赖
    from kzocr.engine.run import _pdf_page_to_numpy

    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc):
            img = _pdf_page_to_numpy(page, dpi=dpi)
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
            tier1_result = adapter.run_book(pdf_path)
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
    for page_num, page_input in enumerate(render_pages(pdf_path, config)):
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
            verdict = verifier.verify(cur_text, context)
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
                trace.extend(page_trace)
                continue

        # ── Tier2：云端视觉 LLM（逐页降级）──
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not budget.exhausted:
            tier2 = _safe_select_candidates(
                scheduler, registry, 2, page_input, budget, page_layout, overrides
            )
            for engine in tier2:
                if budget.exhausted:
                    break
                t0 = time.monotonic()
                try:
                    if engine.meta.requires_network:
                        validate_url(engine.config.base_url or "")  # B4: 抛 ValueError
                    result = _run_single_engine_with_timeout(
                        engine, page_input,
                        timeout_s=getattr(config, "max_time_per_page_ms", 120000) / 1000 * 2,
                    )
                except ValueError as exc:
                    _logger.warning("egress blocked for %s: %s", engine.meta.name, exc)
                    registry.mark_unavailable(engine.meta.name)
                    continue
                except TimeoutError:
                    _logger.warning("Tier2 engine=%s timed out", engine.meta.name)
                    continue
                except Exception as exc:
                    _logger.error("Tier2 engine=%s failed: %s", engine.meta.name, exc)
                    registry.record(engine.meta.name, success=False, error=str(exc))
                    continue
                t_elapsed = int((time.monotonic() - t0) * 1000)
                vctx = DetectorContext(
                    page_num=page_num, engine_label=engine.meta.name,
                    book_type=book_type, pub_era=pub_era,
                    resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
                )
                verdict = verifier.verify(result.text, vctx)
                page_trace.append(
                    EngineCallRecord(
                        page=page_num, tier=2, engine=engine.meta.name,
                        latency_ms=t_elapsed, glyph_status=verdict.status,
                        detector_chain=list(verifier.last_detector_chain),
                    )
                )
                _record_engine_usage(registry, engine, verdict, t_elapsed, engine_usage_counter)
                if verdict.status in ("PASS", "RARE"):
                    final_text = result.text
                    pages_text.append(final_text)
                    break

        # ── Tier3：本地中医 LLM ──
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not budget.exhausted:
            tier3 = _safe_select_candidates(
                scheduler, registry, 3, page_input, budget, page_layout, overrides
            )
            for engine in tier3:
                if budget.exhausted:
                    break
                t0 = time.monotonic()
                try:
                    result = _run_single_engine_with_timeout(
                        engine, page_input,
                        timeout_s=getattr(config, "max_time_per_page_ms", 120000) / 1000,
                    )
                except TimeoutError:
                    _logger.warning("Tier3 engine=%s timed out", engine.meta.name)
                    continue
                except Exception as exc:
                    _logger.error("Tier3 engine=%s failed: %s", engine.meta.name, exc)
                    registry.record(engine.meta.name, success=False, error=str(exc))
                    continue
                t_elapsed = int((time.monotonic() - t0) * 1000)
                vctx = DetectorContext(
                    page_num=page_num, engine_label=engine.meta.name,
                    book_type=book_type, pub_era=pub_era,
                    resources={"neighbor_texts": neighbor_texts, "next_page_text": next_text},
                )
                verdict = verifier.verify(result.text, vctx)
                page_trace.append(
                    EngineCallRecord(
                        page=page_num, tier=3, engine=engine.meta.name,
                        latency_ms=t_elapsed, glyph_status=verdict.status,
                        detector_chain=list(verifier.last_detector_chain),
                    )
                )
                _record_engine_usage(registry, engine, verdict, t_elapsed, engine_usage_counter)
                if verdict.status in ("PASS", "RARE"):
                    final_text = result.text
                    pages_text.append(final_text)
                    break

        # ── HumanGate ──
        if verdict.status in ("FAIL", "UNKNOWN"):
            failed_pages[page_num] = f"All tiers failed. Last: {verdict.details}"
            _logger.warning("[orchestrator] page=%d all tiers failed: %s", page_num, verdict.details)
        elif verdict.status == "UNCERTAIN":
            uncertain_pages[page_num] = verdict
            if final_text:
                pages_text.append(final_text)

        trace.extend(page_trace)

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

    return BookResult(
        book_code=book_code or "unknown",
        title=title,
        pages=_build_pages_result(pages_text, len(pages_text)),
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
