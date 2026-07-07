"""引擎驱动器：把一份 PDF 跑成归一化的 BookResult。

策略：
1. 若 KZOCR_USE_MOCK=1 → 直接返回桩数据。
2. 否则尝试调用 kimi 的 tcm_ocr 真实管线（BookPipeline）；任何失败都降级到桩数据，
   除非 KZOCR_REQUIRE_REAL=1（此时真实失败会抛出，便于排查）。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from kzocr import config as app_config
from .mock import mock_book_result as build_mock_book
from .types import BookResult

logger = logging.getLogger(__name__)


def run_engine(pdf_path: str, book_code: str | None = None, config=None) -> BookResult:
    cfg = config if config is not None else app_config.config
    if cfg.use_mock:
        logger.info("[engine] use_mock=True，使用桩数据")
        return build_mock_book(book_code=book_code or "TCM-MOCK-001")

    try:
        return _run_real(pdf_path, cfg, book_code)
    except Exception as exc:  # noqa: BLE001
        if cfg.require_real:
            raise
        logger.warning("[engine] 真实引擎执行失败，降级到桩数据：%s", exc)
        return build_mock_book(book_code=book_code or "TCM-MOCK-001")


def _run_real(pdf_path: str, cfg, book_code: str | None = None) -> BookResult:
    """尽力调用 kimi tcm_ocr 管线。需要 MinerU/PaddleOCR/torch/LLM 运行环境。"""
    engine_dir = Path(str(cfg.kimi_engine_dir))
    if not engine_dir.exists():
        raise RuntimeError(f"未找到 kimi 引擎目录：{engine_dir}")

    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    from tcm_ocr.pipeline.book_pipeline import BookPipeline  # noqa: F401

    pipeline_config = {
        "pdf_path": pdf_path,
        "dpi": 300,
        "thresholds": {"consensus": 0.85, "glyph": 0.9},
    }
    logger.info("[engine] 实例化 BookPipeline 并运行 %s", pdf_path)
    pipeline = BookPipeline(pipeline_config)
    result = pipeline.process(pdf_path)  # 真实方法名依 kimi 实现而定

    final_md = _read_deliverable(result)
    title = getattr(result, "title", None) or os.path.basename(pdf_path)
    return BookResult(
        book_code=book_code or "KZOCR-real",
        title=title,
        engine_label="kimi",
        final_markdown=final_md or "",
    )


def _read_deliverable(result) -> str:
    for attr in ("final_markdown", "markdown", "final_text"):
        val = getattr(result, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    for attr in ("deliverable_paths", "outputs"):
        paths = getattr(result, attr, None)
        if isinstance(paths, dict):
            for p in paths.values():
                if isinstance(p, str) and p.endswith(".md") and os.path.exists(p):
                    return Path(p).read_text(encoding="utf-8", errors="ignore")
    return ""
