"""book_result_from_tcm_ocr 转换器单测（无引擎/Postgres）。

验证 §3.1 字段映射：page_number、按 bbox y 排序、fused_text、
confidence、char_boxes 类型折算（List[Dict] 浮点 → list[list[list[int]]]）、
LineResult 不含 char_boxes。
"""
from __future__ import annotations

from kzocr.engine.types import BookResult, LineResult
from kzocr.tcm_ocr.pipeline.book_result_convert import book_result_from_tcm_ocr


def _page_results():
    return [
        {
            "page_number": 1,
            "lines": [
                # 乱序：y=300 的行应在 y=100 之后
                {
                    "bbox": [10, 300, 200, 320],
                    "fused_text": "第二行文本",
                    "confidence": 0.88,
                    "char_bboxes": [
                        {"char": "第", "conf": 0.9, "bbox": [12.4, 300, 30.1, 320]},
                        {"char": "二", "conf": 0.9, "bbox": [31.0, 300, 50.2, 320]},
                    ],
                    "engine_results": {"paddleocr": {"text": "第二行文本", "confidence": 0.88}},
                },
                {
                    "bbox": [10, 100, 200, 120],
                    "fused_text": "第一行文本",
                    "confidence": 0.95,
                    "char_bboxes": [
                        {"char": "第", "conf": 0.9, "bbox": [12.0, 100, 30.0, 120]},
                    ],
                    "engine_results": {"paddleocr": {"text": "第一行文本", "confidence": 0.95}},
                },
                # 缺 bbox 的行（兜底 [0,0,0,0]，排最前）
                {
                    "bbox": [10, 50, 200, 70],
                    "fused_text": "第零行（无 y 冲突）",
                    "confidence": 0.7,
                },
            ],
        },
        {
            "page_number": 2,
            "lines": [
                {
                    "bbox": [10, 90, 200, 110],
                    "fused_text": "第三页唯一行",
                    "confidence": 0.9,
                    "char_bboxes": [],  # 空 char_bboxes → 不注入
                },
            ],
        },
    ]


def test_page_num_from_page_number():
    br = book_result_from_tcm_ocr(_page_results(), book_code="TCM-CONV-001")
    assert isinstance(br, BookResult)
    assert br.book_code == "TCM-CONV-001"
    assert [p.page_num for p in br.pages] == [1, 2]


def test_lines_sorted_by_bbox_y():
    br = book_result_from_tcm_ocr(_page_results(), book_code="X")
    page1 = br.pages[0]
    # 单段，段内行按 bbox y 升序：y=50, y=100, y=300
    texts = [ln.final for ln in page1.paragraphs[0].lines]
    assert texts == ["第零行（无 y 冲突）", "第一行文本", "第二行文本"]


def test_text_from_fused_text():
    br = book_result_from_tcm_ocr(_page_results(), book_code="X")
    line = br.pages[0].paragraphs[0].lines[1]
    assert line.final == "第一行文本"
    assert line.consensus == "第一行文本"
    assert line.confidence == 0.95
    # engine_texts 映射保留
    assert line.engine_texts.get("paddleocr") == "第一行文本"


def test_char_boxes_converted_to_int_list():
    br = book_result_from_tcm_ocr(_page_results(), book_code="X")
    page1 = br.pages[0]
    cb = page1.char_boxes
    # 3 行 → 每行逐字框
    assert cb is not None
    assert len(cb) == 3
    # 第二行（排序后 index 2）有 2 个逐字框，浮点已 round 成 int
    assert cb[2] == [[12, 300, 30, 320], [31, 300, 50, 320]]
    # 第 2 页唯一行 char_bboxes 为空 → 对应为空 list（非 None，因 page_char_boxes 已建）
    assert br.pages[1].char_boxes == [[]]


def test_line_result_has_no_char_boxes_field():
    """LineResult 类型不含 char_boxes（§3.1 注意事项）。"""
    br = book_result_from_tcm_ocr(_page_results(), book_code="X")
    line = br.pages[0].paragraphs[0].lines[0]
    assert isinstance(line, LineResult)
    assert not hasattr(line, "char_boxes")


def test_empty_page_results():
    br = book_result_from_tcm_ocr([], book_code="EMPTY")
    assert br.pages == []


def test_char_boxes_skips_malformed_dets():
    """逐字框折算对畸形 det 应跳过而非崩溃（缺 bbox 键 / bbox 不足 4 值）。"""
    page_results = [
        {
            "page_number": 1,
            "lines": [
                {
                    "bbox": [10, 100, 200, 120],
                    "fused_text": "含畸形框的行",
                    "confidence": 0.9,
                    "char_bboxes": [
                        {"char": "正", "bbox": [12.6, 100, 30.4, 120]},  # 合法 → [13,100,30,120]
                        {"char": "缺bbox"},                            # 缺 bbox → 跳过
                        {"char": "短bbox", "bbox": [1, 2]},            # 不足 4 值 → 跳过
                        {"char": "负", "bbox": [5.2, 100.7, 9, 120]},  # 合法 → [5,101,9,120]
                    ],
                },
            ],
        },
    ]
    br = book_result_from_tcm_ocr(page_results, book_code="MALFORMED")
    cb = br.pages[0].char_boxes
    assert cb is not None
    assert len(cb) == 1
    # 仅 2 个合法 det 保留，浮点已 round
    assert cb[0] == [[13, 100, 30, 120], [5, 101, 9, 120]]


def test_line_without_char_bboxes_key():
    """行完全没有 char_bboxes 键时折算为空 list（非 None）。"""
    page_results = [
        {
            "page_number": 1,
            "lines": [
                {"bbox": [10, 100, 200, 120], "fused_text": "无框行", "confidence": 0.9},
            ],
        },
    ]
    br = book_result_from_tcm_ocr(page_results, book_code="NOKEY")
    # page_char_boxes 已构造 → 对应行是空 list
    assert br.pages[0].char_boxes == [[]]


def test_page_confidence_is_mean_of_line_confidences():
    """逐页置信度应取行级置信度均值，而非写死 0.9。"""
    page_results = [
        {
            "page_number": 1,
            "lines": [
                {"bbox": [0, 100, 10, 120], "fused_text": "甲", "confidence": 0.8},
                {"bbox": [0, 130, 10, 150], "fused_text": "乙", "confidence": 1.0},
            ],
        },
    ]
    br = book_result_from_tcm_ocr(page_results, book_code="CONF")
    assert br.pages[0].confidence == 0.9  # (0.8 + 1.0) / 2

    # 单行页置信度等于该行置信度
    single = [
        {"page_number": 2, "lines": [{"bbox": [0, 100, 10, 120], "fused_text": "丙", "confidence": 0.42}]},
    ]
    br2 = book_result_from_tcm_ocr(single, book_code="CONF2")
    assert br2.pages[0].confidence == 0.42
