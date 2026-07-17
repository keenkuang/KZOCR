"""KZOCR 引擎适配器 — 包装真实 OCR 引擎为 EngineRunner 协议。

适配器职责：初始化引擎 → 接收 PageInput → 调用引擎推理 → 返回 AdapterPageResult
（含 text/confidence/char_confidences/boxes 等归一化字段）。

当前适配器：
- PaddleOCRAdapter：包装 PaddleOCR PP-OCRv6（CPU，无需密钥）
- RapidOCRAdapter：包装 RapidOCR（onnxruntime CPU，无需密钥）

用法：
    from kzocr.engine.adapters import PaddleOCRAdapter
    adapter = PaddleOCRAdapter()
    result = adapter.run_page(PageInput(page_num=0, img=numpy_img))
    print(result.text)       # 识别文本
    print(result.boxes)      # 行级 bbox 列表
"""
from __future__ import annotations

from kzocr.engine.types import AdapterPageResult, PageInput


class PaddleOCRAdapter:
    """包装 PaddleOCR PP-OCRv6 为 EngineRunner（页级）。

    引擎输出归一化：
    - text：拼接所有识别行的文本
    - boxes：每行为 [x1,y1,x2,y2]（quad → 矩形外框）
    - char_confidences：展平所有字符的置信度（引擎逐字输出）
    """

    def __init__(self) -> None:
        self._engine = None

    def _lazy_init(self) -> None:
        if self._engine is not None:
            return
        from paddleocr import PaddleOCR

        self._engine = PaddleOCR(show_log=False)

    def run_page(self, page: PageInput) -> AdapterPageResult:
        self._lazy_init()
        img = page.img
        # PaddleOCR.ocr 返回 list[list[[quad, (text, score)]]]
        res = self._engine.ocr(img)
        return _parse_ppocr_result(res)

    def run_book(self, pdf_path: str) -> AdapterPageResult:
        raise NotImplementedError("PaddleOCRAdapter 仅支持页级（run_page），不支持书级")


def _quad_to_rect(quad) -> list[int]:
    """将 quad [[x1,y1], [x2,y2], [x3,y3], [x4,y4]] 转为 [x1,y1,x2,y2] 矩形。"""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return [min(xs), min(ys), max(xs), max(ys)]


def _parse_ppocr_result(res) -> AdapterPageResult:
    """将 PaddleOCR 原始输出解析为 AdapterPageResult。

    PaddleOCR 旧格式：res = [(quad, (text, score)), ...]（tuple 列表）
    新格式（PaddleX）：       res = [{'rec_text': ..., 'rec_score': ..., ...}]（dict 列表）
    """
    if not res:
        return AdapterPageResult(text="", confidence=0.0, boxes=[], char_confidences=[])

    texts: list[str] = []
    boxes: list[list[int]] = []
    confs: list[float] = []
    for blk in res:
        if isinstance(blk, dict):
            # PaddleX 新格式：{'rec_text': '...', 'rec_score': 0.95, 'poly': [[...]]}
            text = str(blk.get("rec_text", blk.get("rec_texts", "")))
            score = float(blk.get("rec_score", blk.get("rec_text_score", 0.0)))
            poly = blk.get("poly") or blk.get("points")
            if not text:
                continue
            texts.append(text)
            confs.append(score)
            if poly and isinstance(poly, (list, tuple)) and len(poly) >= 4:
                boxes.append(_quad_to_rect(poly[:4]))
        elif isinstance(blk, (list, tuple)):
            # 旧格式：blk = (quad, (text, score)) — 直接取两个元素，不迭代
            if len(blk) < 2:
                continue
            quad, rec = blk[0], blk[1]
            try:
                text = str(rec[0]) if isinstance(rec, (list, tuple)) else ""
                score = float(rec[1]) if isinstance(rec, (list, tuple)) else 0.0
            except (IndexError, TypeError, ValueError):
                continue
            if not text:
                continue
            texts.append(text)
            if quad and isinstance(quad, (list, tuple)) and len(quad) == 4:
                boxes.append(_quad_to_rect(quad))
            confs.append(score)
    return AdapterPageResult(
        text="".join(texts),
        confidence=sum(confs) / len(confs) if confs else 0.0,
        boxes=boxes if boxes else None,
        char_confidences=confs if confs else None,
    )


class RapidOCRAdapter:
    """包装 RapidOCR 为 EngineRunner（页级）。

    RapidOCR 输出 list[(quad, text)]；每行一个 quad + text。
    """

    def __init__(self) -> None:
        self._engine = None

    def _lazy_init(self) -> None:
        if self._engine is not None:
            return
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def run_page(self, page: PageInput) -> AdapterPageResult:
        self._lazy_init()
        img = page.img
        out, _ = self._engine(img)
        return _parse_rapidocr_result(out)

    def run_book(self, pdf_path: str) -> AdapterPageResult:
        raise NotImplementedError("RapidOCRAdapter 仅支持页级（run_page），不支持书级")


def _parse_rapidocr_result(out) -> AdapterPageResult:
    """将 RapidOCR 原始输出解析为 AdapterPageResult。"""
    if not out:
        return AdapterPageResult(text="", confidence=0.0, boxes=[], char_confidences=[])
    texts: list[str] = []
    boxes: list[list[int]] = []
    confs: list[float] = []
    for item in out:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        quad, text = item[0], str(item[1])
        if not text:
            continue
        texts.append(text)
        if quad and isinstance(quad, (list, tuple)) and len(quad) == 4:
            boxes.append(_quad_to_rect(quad))
    return AdapterPageResult(
        text="".join(texts),
        confidence=0.7,
        boxes=boxes if boxes else None,
        char_confidences=confs if confs else None,
    )
