"""B7：行级裁剪图切片落盘（复用模块 A 烘焙管线，坐标严格对齐）。

坐标不变量（务必遵守，否则与 char_boxes 错位）：
- char_boxes 是 ``_crop_to_body`` 后**版心图**（dpi=150，**不缩放**）的像素坐标，
  原点整页图左上角。ingest 生成 char_boxes 时正是用
  ``_pdf_page_to_numpy(dpi=150)`` + ``_crop_to_body``（见 orchestrator.render_pages）。
- **绝不能用** orchestrator 的 VL 缩放（max_pixels resize）坐标——那是给视觉模型用的，
  与 char_boxes 无关。
- 因此本模块渲染管线与模块 A（kzocr/doc/zai.py:351-376）完全一致：
  ``_pdf_page_to_numpy(doc[page_num-1], dpi=150)`` + ``_crop_to_body``，**不缩放**；
  persist 阶段重新渲染同页会得到与 ingest 完全相同的版心图，坐标直接对齐。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from kzocr.engine.run import _crop_to_body, _pdf_page_to_numpy

_logger = logging.getLogger(__name__)


def render_body_page(doc, page_num: int):
    """渲染整页为版心图（dpi=150 + _crop_to_body，不缩放）。

    ``page_num`` 为 0-based（fitz 页索引）。
    """
    img = _pdf_page_to_numpy(doc[page_num], dpi=150)
    return _crop_to_body(img, page_num=page_num)


def _line_bbox(char_boxes):
    """整页版心图坐标系下，该行所有字框的包围盒 (x0, y0, x1, y1)。"""
    xs: list[int] = []
    ys: list[int] = []
    for b in char_boxes:
        # b = [x1, y1, x2, y2]
        xs.append(b[0])
        xs.append(b[2])
        ys.append(b[1])
        ys.append(b[3])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def crop_line_to_png(
    pdf_path: str,
    page_num: int,
    char_boxes,
    para_seq: int,
    line_seq: int,
    book_code: str,
    db_dir: str,
    *,
    doc_cache: Optional[dict] = None,
    img_cache: Optional[dict] = None,
) -> Optional[str]:
    """切出单行裁剪图，落 ``<db_dir>/<book_code>_crops/P{page}_L{para}_{seq}.png``。

    返回相对 ``db_dir`` 的路径（如 ``xx_crops/P3_L1_2.png``）；无可切/失败返回 ``None``。

    ``char_boxes`` 须为整页版心图坐标系（与 BookDB.line.char_boxes 一致）。
    ``page_num`` 为 1-based（与 BookDB 页码一致）。

    ``doc_cache`` / ``img_cache`` 为可选跨行缓存（按 pdf_path / (pdf_path, page_num)）：
    同一 PDF 的 doc 只打开一次、同一页只渲染一次。调用方负责在 ``finally`` 中
    ``close_doc_cache(doc_cache)`` 释放 fitz 文档。
    """
    if not pdf_path or not char_boxes:
        return None
    try:
        doc_cache = doc_cache if doc_cache is not None else {}
        img_cache = img_cache if img_cache is not None else {}
        import fitz

        if pdf_path not in doc_cache:
            doc_cache[pdf_path] = fitz.open(str(pdf_path))
        doc = doc_cache[pdf_path]
        cache_key = (pdf_path, page_num)
        if cache_key not in img_cache:
            img_cache[cache_key] = render_body_page(doc, page_num - 1)
        img = img_cache[cache_key]

        bbox = _line_bbox(char_boxes)
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            return None

        from PIL import Image as PILImage

        rel = f"{book_code}_crops/P{page_num}_L{para_seq}_{line_seq}.png"
        out_abs = os.path.join(db_dir, rel)
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        PILImage.fromarray(crop).save(out_abs, "PNG")
        return rel
    except Exception:
        _logger.warning(
            "[crop_images] 切行裁图失败（%s P%d L%d_%d）",
            book_code, page_num, para_seq, line_seq, exc_info=True,
        )
        return None


def close_doc_cache(doc_cache: dict) -> None:
    """关闭 crop_line_to_png 持有的所有 fitz 文档（best-effort）。"""
    for doc in doc_cache.values():
        try:
            doc.close()
        except Exception:
            pass
