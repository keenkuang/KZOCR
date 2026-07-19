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

import numpy as np

from kzocr.engine.types import AdapterPageResult, BookResult, PageInput, PageResult


class PaddleOCRAdapter:
    """包装 PaddleOCR PP-OCRv6 为 EngineRunner（页级 + 书级）。

    引擎输出归一化：
    - text：拼接所有识别行的文本
    - boxes：每行为 [x1,y1,x2,y2]（quad → 矩形外框）
    - char_confidences：展平所有字符的置信度（引擎逐字输出）

    **性能优化**：
    - 进程级单例引擎，避免重复加载模型（~4min on CPU）
    - 启用 MKLDNN + text_recognition_batch_size=6 加速 CPU 推理
    - 跳过行朝向分类（use_textline_orientation=False，古籍扫描页无旋转）
    """

    _engine_global = None  # 进程级单例

    @classmethod
    def _get_engine(cls) -> object | None:
        """获取进程级单例 PaddleOCR 引擎。"""
        if cls._engine_global is None:
            from paddleocr import PaddleOCR
            import logging as _log
            _log.getLogger("ppocr").setLevel(_log.WARNING)
            cls._engine_global = PaddleOCR(
                use_textline_orientation=False,
                text_recognition_batch_size=6,
            )
        return cls._engine_global

    def __init__(self) -> None:
        pass

    def run_page(self, page: PageInput) -> AdapterPageResult:
        engine = self._get_engine()
        img = page.img
        # return_word_box=True → 额外返回逐字 text_word / text_word_boxes（字符级 bbox）
        # 注：PaddleOCR ≥3.7 弃用 .ocr()，改用 .predict()（输出格式一致）
        res = engine.predict(img, return_word_box=True)
        return _parse_ppocr_result(res)

    def run_book(self, pdf_path: str, book_code: str = "", max_pages: int = 0) -> BookResult:
        """书级执行：逐页渲染 → run_page → BookResult。

        Args:
            max_pages: 处理页数上限（0 = 全本）。编排器传入 budget.max_pages 以对齐
                逐页循环的实际范围，避免对几百页古籍做无谓全本前置 OCR。
        """
        import fitz
        doc = fitz.open(pdf_path)
        try:
            page_count = doc.page_count
            if max_pages and max_pages > 0:
                page_count = min(max_pages, page_count)
            pages = []
            for i in range(page_count):
                pix = doc[i].get_pixmap(dpi=150)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                pi = PageInput(page_num=i, img=img)
                result = self.run_page(pi)
                pages.append(PageResult(
                    page_num=i, text=result.text, confidence=result.confidence,
                    char_boxes=result.char_boxes,
                ))
            return BookResult(book_code=book_code, title="", pages=pages)
        finally:
            doc.close()


def _quad_to_rect(quad: list[list[float]]) -> list[int]:
    """将 quad [[x1,y1], [x2,y2], [x3,y3], [x4,y4]] 转为 [x1,y1,x2,y2] 矩形。"""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return [min(xs), min(ys), max(xs), max(ys)]


def _parse_ppocr_result(res: object) -> AdapterPageResult:
    """将 PaddleOCR 原始输出解析为 AdapterPageResult。

    支持两种格式：
    1. PaddleX 页面级 OCRResult（dict 子类）：res = [page_result]，含
       rec_texts / rec_scores / rec_polys（行级）以及 return_word_box=True 时的
       text_word（逐字）/ text_word_boxes（逐字矩形 [x1,y1,x2,y2]）。
    2. 旧格式：res = [(quad, (text, score)), ...]（每行一个 block）。
    """
    if not res:
        return AdapterPageResult(text="", confidence=0.0, boxes=None,
                                 char_boxes=None, char_confidences=None)

    # ---- PaddleX 页面级格式 ----
    first = res[0]
    if isinstance(first, dict) and "rec_texts" in first:
        texts = [str(t) for t in (first.get("rec_texts") or [])]
        scores = [float(s) for s in (first.get("rec_scores") or [])]
        line_polys = first.get("rec_polys") or first.get("dt_polys") or []
        boxes = [_quad_to_rect(p) for p in line_polys if p is not None] if line_polys else None

        # 字符级 bbox：text_word_boxes[i] 形状 (N,4)，每行逐字矩形
        char_boxes = None
        twb = first.get("text_word_boxes")
        if twb:
            char_boxes = []
            for lw in twb:
                if lw is None:
                    char_boxes.append([])
                    continue
                arr = np.array(lw) if not isinstance(lw, np.ndarray) else lw
                char_boxes.append([[int(v) for v in row] for row in arr])

        return AdapterPageResult(
            text="".join(texts),
            confidence=sum(scores) / len(scores) if scores else 0.0,
            boxes=boxes,
            char_boxes=char_boxes,
            char_confidences=scores if scores else None,
        )

    # ---- 旧格式：list of (quad, (text, score)) ----
    texts: list[str] = []
    boxes: list[list[int]] = []
    confs: list[float] = []
    for blk in res:
        if not isinstance(blk, (list, tuple)) or len(blk) < 2:
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
        char_boxes=None,
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

    def run_book(self, pdf_path: str, book_code: str = "", max_pages: int = 0) -> BookResult:
        """书级执行：逐页渲染 → run_page → BookResult。

        Args:
            max_pages: 处理页数上限（0 = 全本）。编排器传入 budget.max_pages 以对齐
                逐页循环的实际范围，避免对几百页古籍做无谓全本前置 OCR。
        """
        import fitz
        doc = fitz.open(pdf_path)
        try:
            page_count = doc.page_count
            if max_pages and max_pages > 0:
                page_count = min(max_pages, page_count)
            pages = []
            for i in range(page_count):
                pix = doc[i].get_pixmap(dpi=150)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                pi = PageInput(page_num=i, img=img)
                result = self.run_page(pi)
                pages.append(PageResult(
                    page_num=i, text=result.text, confidence=result.confidence,
                    char_boxes=result.char_boxes,
                ))
            return BookResult(book_code=book_code, title="", pages=pages)
        finally:
            doc.close()


def _parse_rapidocr_result(out) -> AdapterPageResult:
    """将 RapidOCR 原始输出解析为 AdapterPageResult。

    RapidOCR 返回 ``list[[box, text, score], ...]``：
    - box 为 4 点 quad（``(4,2)`` 转 list）；
    - text 为识别文本；
    - score 为**字符串化**的逐行置信度（RapidOCR 内部已 ``str()``）。

    此处取逐行 score 计算页级置信度，并透传 ``char_confidences``，
    使 conf≤0.90 门控对 RapidOCR 真正生效（此前 score 被丢弃、confidence 写死 0.7）。
    """
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
        # 第三项（若有）为逐行置信度；RapidOCR 已字符串化，需转回 float
        if len(item) >= 3:
            try:
                confs.append(float(item[2]))
            except (TypeError, ValueError):
                pass
    return AdapterPageResult(
        text="".join(texts),
        confidence=sum(confs) / len(confs) if confs else 0.0,
        boxes=boxes if boxes else None,
        char_confidences=confs if confs else None,
    )
