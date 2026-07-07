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


def _build_engine_config() -> dict:
    """从环境变量构造 kimi BookPipeline 所需的 config 字典。

    需要的最小集：
        KZOCR_ENGINE_LIB_DIR  书籍库/交付物输出目录（必须可写，默认避开 /mnt/agents）
        KZOCR_PG_DSN          PostgreSQL DSN（留空则禁用 PG 归档）
        KZOCR_TERM_KB_PATH    术语知识库路径（可选）
        KZOCR_LLM_ENABLED     是否启用云端 LLM 校对（1/true 开启，否则仅 OCR 校对）
        KZOCR_LLM_API_KEY / KZOCR_LLM_BASE_URL / KZOCR_LLM_MODEL
        KZOCR_GPU_MAP         "auto" 或设备映射
        KZOCR_PUBLISHER_BONUS 出版社准确率奖励（浮点）
    """
    cloud_llm = {
        "enabled": os.environ.get("KZOCR_LLM_ENABLED", "0") in ("1", "true", "True"),
        "api_key": os.environ.get("KZOCR_LLM_API_KEY", ""),
        "base_url": os.environ.get("KZOCR_LLM_BASE_URL", ""),
        "model": os.environ.get("KZOCR_LLM_MODEL", "qwen-max"),
    }
    return {
        "book_library_dir": os.environ.get("KZOCR_ENGINE_LIB_DIR", "/home/keen/kzocr_engine_lib"),
        "pg_dsn": os.environ.get("KZOCR_PG_DSN", ""),
        "engine_configs": {"cloud_llm": cloud_llm},
        "gpu_device_map": os.environ.get("KZOCR_GPU_MAP", "auto"),
        "term_kb_path": os.environ.get("KZOCR_TERM_KB_PATH", ""),
        "publisher_bonus": float(os.environ.get("KZOCR_PUBLISHER_BONUS", "0.02")),
    }


def _run_real(pdf_path: str, cfg, book_code: str | None = None) -> BookResult:
    """调用 kimi tcm_ocr 的 BookPipeline（需安装引擎依赖并配置环境）。"""
    engine_dir = Path(str(cfg.kimi_engine_dir))
    if not engine_dir.exists():
        raise RuntimeError(f"未找到 kimi 引擎目录：{engine_dir}（请设置 KIMI_ENGINE_DIR）")

    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    from tcm_ocr.pipeline.book_pipeline import BookPipeline

    engine_config = _build_engine_config()
    logger.info("[engine] 实例化 BookPipeline（lib_dir=%s）", engine_config["book_library_dir"])
    pipeline = BookPipeline(engine_config)
    book_id = book_code or "KZOCR-real"
    result = pipeline.process_book(pdf_path, book_id)

    final_md = _read_deliverable(result, engine_config["book_library_dir"], book_id)
    meta = getattr(pipeline, "current_book_meta", {}) or {}
    title = meta.get("title") or os.path.basename(pdf_path)
    return BookResult(
        book_code=book_id,
        title=title,
        engine_label="kimi",
        final_markdown=final_md or "",
    )


def _read_deliverable(result, lib_dir: str, book_id: str) -> str:
    """从 BookPipeline.process_book 的返回字典里取出最终 Markdown。"""
    if isinstance(result, dict):
        for attr in ("final_markdown", "markdown", "final_text"):
            val = result.get(attr)
            if isinstance(val, str) and val.strip():
                return val
        outputs = result.get("outputs")
    else:
        outputs = getattr(result, "outputs", None)

    if isinstance(outputs, dict):
        for p in outputs.values():
            if isinstance(p, str) and p.endswith(".md") and os.path.exists(p):
                return Path(p).read_text(encoding="utf-8", errors="ignore")

    base = Path(lib_dir) / book_id
    if base.exists():
        for name in ("body.md", "full.md", "final_document.md"):
            cand = base / name
            if cand.exists():
                return cand.read_text(encoding="utf-8", errors="ignore")
        mds = sorted(base.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
        if mds:
            return mds[0].read_text(encoding="utf-8", errors="ignore")
    return ""
