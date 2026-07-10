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


class MockAdapter:
    """mock 引擎适配器（Tier 1）。仅用于冒烟与 CI 回归。"""

    def __init__(self, book_code: str = "TCM-MOCK-001") -> None:
        self.book_code = book_code

    def run_book(self, pdf_path: str) -> BookResult:
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

    def __init__(self, engine_name: str = "kimi") -> None:
        self.engine_name = engine_name
        self._pipeline = None

    def _ensure_pipeline(self, config: dict) -> None:
        if self._pipeline is not None:
            return
        from tcm_ocr.pipeline.book_pipeline import BookPipeline
        self._pipeline = BookPipeline(config)

    def run_book(self, pdf_path: str, pipeline_config: Optional[dict] = None) -> BookResult:
        """处理全书。pipeline_config 仅在首次初始化时使用。"""
        if pipeline_config:
            self._ensure_pipeline(pipeline_config)
        if self._pipeline is None:
            raise RuntimeError("BookPipelineAdapter not initialized: call with pipeline_config first")
        result = self._pipeline.process_book(pdf_path, self.engine_name)
        return result

    def run_page(self, page: PageInput) -> AdapterPageResult:
        raise NotImplementedError(f"{self.engine_name} is a book-level engine; use run_book()")


class VlmPageAdapter:
    """把云端 VLM（SenseNova / DeepSeek-VL / PaddleOCR-VL）API 包装为 EngineRunner。

    注意：VLM 引擎是逐页处理的（run_page），不支持 run_book。
    """

    def __init__(self, engine_name: str) -> None:
        self.engine_name = engine_name

    def run_book(self, pdf_path: str) -> BookResult:
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
