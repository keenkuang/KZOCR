"""把 tcm_ocr 的 BookPipeline.page_results 转换为主线 kzocr.engine.types.BookResult。

目的（DB 分层 Phase 4）：让 tcm_ocr 栈产出与主线 kzocr.engine 归一化的
BookResult（含真实 book_code + 字符级 char_boxes），从而复用
BookDB.persist_book_result / push_book_to_zai / import_proofread_package
既有闭环（见 docs/plans/tcm-ocr-unification.md §3.1）。

字段映射（均已按代码核实）：
- page_num      <- page_result["page_number"]  (page_pipeline.py:129)
- 行序          <- 按 line["bbox"][1] (y 上沿) 升序排序，保证行序=阅读序
- 文本          <- line["fused_text"]           (book_pipeline.py:656)
- 置信度        <- line["confidence"]           (book_pipeline.py:657)
- 字符框        <- line["char_bboxes"]          (页绝对，List[Dict]，由 §3.3 填充)
                  折算为 list[list[list[int]]] 仅挂在 PageResult.char_boxes
                  （LineResult 无此字段，见 types.py:52 / :88）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from kzocr.engine.types import (
    BookResult,
    LineResult,
    PageResult,
    ParagraphResult,
)


def _char_boxes_from_line(line: Dict[str, Any]) -> List[List[int]]:
    """把 extract_char_bboxes 的返回值（List[Dict]，每 dict 含 float bbox）
    折算为 list[list[int]]（每行逐字 [x1,y1,x2,y2]）。

    无 char_bboxes 时返回空 list。
    """
    raw = line.get("char_bboxes") or []
    if not raw:
        return []
    boxes: List[List[int]] = []
    for det in raw:
        bbox = det.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        boxes.append([int(round(b)) for b in bbox[:4]])
    return boxes


def book_result_from_tcm_ocr(
    page_results: List[Dict[str, Any]],
    *,
    book_code: str,
    title: str = "",
    author: str = "",
    publisher: str = "",
    pub_year: int = 0,
    engine_label: str = "kimi",
    formulas: Optional[list] = None,
    herb_patterns: Optional[list] = None,
    meridian_patterns: Optional[list] = None,
    context_patterns: Optional[list] = None,
    terms: Optional[list] = None,
) -> BookResult:
    """将 BookPipeline.page_results 转换为主线 BookResult。

    Args:
        page_results: BookPipeline.process_book 填充的 self.page_results，
            每页 dict 含 "page_number" + "lines"（行 dict 含
            "bbox"/"fused_text"/"confidence"/"char_bboxes"/"engine_results"）。
        book_code: 真实书籍编码（G2 主键一致，不填 "TCM-UNK"）。
        其余为元数据（title/author/publisher/pub_year）与知识图谱结果。

    Returns:
        主线归一化 BookResult，每页整页单段（para_seq=1），层级键按位置派生。
    """
    pages: List[PageResult] = []

    for pr in page_results:
        page_num = int(pr.get("page_number", 0) or 0)
        lines_raw = pr.get("lines", []) or []

        # 行序按 bbox y 上沿升序；缺 bbox 兜底 [0,0,0,0]，避免 KeyError
        sorted_lines = sorted(
            lines_raw,
            key=lambda ln: (
                ln.get("bbox", [0, 0, 0, 0])[1],
                ln.get("bbox", [0, 0, 0, 0])[0],
            ),
        )

        line_results: List[LineResult] = []
        page_char_boxes: List[List[List[int]]] = []

        for ln in sorted_lines:
            text = ln.get("fused_text", "") or ""
            conf = float(ln.get("confidence", 0.9) or 0.9)
            engine_results = ln.get("engine_results") or {}
            engine_texts = {
                name: (r.get("text", "") if isinstance(r, dict) else str(r))
                for name, r in engine_results.items()
            }
            line_results.append(
                LineResult(
                    final=text,
                    consensus=text,
                    confidence=conf,
                    engine_texts=engine_texts,
                    disputed=bool(ln.get("disputed", False)),
                )
            )
            page_char_boxes.append(_char_boxes_from_line(ln))

        para = ParagraphResult(sequence_in_page=1, lines=line_results)
        page_text = "\n".join(
            (lr.final or lr.consensus or "") for lr in line_results
        )
        pages.append(
            PageResult(
                page_num=page_num,
                paragraphs=[para],
                text=page_text,
                confidence=0.9,
                char_boxes=page_char_boxes or None,
            )
        )

    return BookResult(
        book_code=book_code,
        title=title,
        author=author,
        publisher=publisher,
        pub_year=pub_year,
        pages=pages,
        engine_label=engine_label,
        herb_patterns=herb_patterns or [],
        meridian_patterns=meridian_patterns or [],
        context_patterns=context_patterns or [],
        terms=terms or [],
        formulas=formulas or [],
    )
