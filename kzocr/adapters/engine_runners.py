"""v0.7 引擎适配器：将现有引擎包装为 EngineRunner 协议。

BookPipelineAdapter — 把 kimi BookPipeline 包装为 run_book（Tier 1）
VlmPageAdapter — 把云端/本地 VLM API 调用包装为 run_page（Tier 2/3）
MockAdapter — 返回 mock 数据的桩适配器
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Optional

import numpy as np
from PIL import Image

from kzocr.engine.types import (
    AdapterPageResult,
    BookResult,
    PageInput,
)
from kzocr.tcm_ocr.pipeline.book_result_convert import book_result_from_tcm_ocr


class MockAdapter:
    """mock 引擎适配器（Tier 1）。仅用于冒烟与 CI 回归。"""

    def __init__(self, book_code: str = "TCM-MOCK-001") -> None:
        self.book_code = book_code

    def run_book(self, pdf_path: str, **kwargs: object) -> BookResult:
        from kzocr.engine.mock import mock_book_result
        result = mock_book_result(book_code=self.book_code)
        # 确保每个 page 的 text 字段填充（E4 _join_paragraphs 回退机制依赖）
        for p in result.pages:
            if not p.text and p.paragraphs:
                parts = []
                for para in p.paragraphs:
                    parts.append("".join(
                        line.final or line.consensus or "" for line in para.lines
                    ))
                p.text = "\n".join(parts)
        return result

    def run_page(self, page: PageInput) -> AdapterPageResult:
        return AdapterPageResult(
            text=f"[mock] page {page.page_num}: fake OCR output",
            confidence=0.9,
        )


class BookPipelineAdapter:
    """把 kimi BookPipeline 包装为 EngineRunner（Tier 1 书级引擎）。

    内部延迟加载 tcm_ocr 包（仅在 process_book 时导入），避免无 kimi 引擎时
    加载失败。
    """

    def __init__(self, engine_name: str = "kimi", pipeline_config: Optional[dict] = None, temperature: float = 0.0) -> None:
        self.engine_name = engine_name
        self.temperature = temperature
        self._pipeline_config = pipeline_config
        self._pipeline = None
        # pipeline_config 在构造时即传入则预初始化（run.py:_init_v07_registry 注入）
        if pipeline_config:
            self._ensure_pipeline(pipeline_config)

    def _ensure_pipeline(self, config: dict) -> None:
        if self._pipeline is not None:
            return
        from kzocr.tcm_ocr.pipeline.book_pipeline import BookPipeline
        self._pipeline = BookPipeline(config)

    def run_book(self, pdf_path: str, *, book_code: str, max_pages: int = 0, **kw: object) -> BookResult:
        """处理全书并返回主线归一化 BookResult（G1 闭环）。

        Args:
            pdf_path: PDF 路径。
            book_code: 真实书籍编码（G2 主键一致；空则降级 "TCM-UNK"）。
            max_pages: 处理页数上限（0 = 全本）。编排器传入 budget.max_pages 以对齐
                逐页循环的实际范围，避免对几百页古籍做无谓全本前置 OCR。
            **kw: 透传给 book_result_from_tcm_ocr（title/author/...）。
        """
        if self._pipeline is None:
            if self._pipeline_config is None:
                raise RuntimeError(
                    "BookPipelineAdapter 未配置 pipeline_config：请在 __init__ 传入"
                )
            self._ensure_pipeline(self._pipeline_config)
        self._pipeline.process_book(pdf_path, book_code or "TCM-UNK")
        book_result = book_result_from_tcm_ocr(
            self._pipeline.page_results,
            book_code=book_code or "TCM-UNK",
            engine_label=self.engine_name,
            **kw,
        )
        # 对齐编排器逐页循环的实际处理范围（与 Tier1 前置 OCR 一致）
        if max_pages and max_pages > 0 and len(book_result.pages) > max_pages:
            book_result.pages = book_result.pages[:max_pages]
        # 落库统一由 run_engine（KZOCR_PERSIST_DB 开关）处理，避免与适配器内落库双重写。
        # 适配器仅负责产出归一化 BookResult；run_engine 已持有真实 book_code。
        return book_result

    def run_page(self, page: PageInput) -> AdapterPageResult:
        raise NotImplementedError(f"{self.engine_name} is a book-level engine; use run_book()")


class VlmPageAdapter:
    """把云端 VLM（SenseNova / DeepSeek-VL / PaddleOCR-VL）API 包装为 EngineRunner。

    注意：VLM 引擎是逐页处理的（run_page），不支持 run_book。
    """

    def __init__(self, engine_name: str, temperature: float = 0.0) -> None:
        self.engine_name = engine_name
        self.temperature = temperature

    def run_book(self, pdf_path: str, **kwargs: object) -> BookResult:
        raise NotImplementedError(
            f"{self.engine_name} is a page-level VLM engine; use run_page()"
        )

    def run_page(self, page: PageInput) -> AdapterPageResult:
        """将 PageInput 转换为 base64，调用 VLM API，返回 AdapterPageResult。

        VLM 真实调用由 Config 确定的目标 URL/Key 驱动，本桩由子类或
        调用方提供 _do_vlm_call 实现。默认返回假数据（用于测试）。
        """
        return self._do_vlm_call(page)

    def _img_to_b64(self, img: np.ndarray) -> str:
        """将 numpy 图像编码为 base64（JPEG）。"""
        pil_img = Image.fromarray(img)
        buf = BytesIO()
        pil_img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode()

    def _do_vlm_call(self, page: PageInput) -> AdapterPageResult:
        """可被子类覆盖的真实 VLM 调用。默认返回桩数据。"""
        return AdapterPageResult(
            text=f"[{self.engine_name}] page {page.page_num}: VLM OCR placeholder",
            confidence=0.5,
        )
