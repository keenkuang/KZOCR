"""Module B 字符框叠加 — LineItem 字段暴露 + _read_line 防御性读取 + 坐标缩放纯函数。

覆盖：
- LineItem 暴露 char_boxes(list) / crop_img_b64(str)
- _read_line 对含 char_boxes + crop_img 的 Row → 解析为 list / 非空 base64
- _read_line 对旧包（缺两列）的 Row → 不抛异常，char_boxes=[] / crop_img_b64=""
- scale_char_box 像素空间 → 显示像素空间 的纯函数映射
"""
from __future__ import annotations

import base64
import json
import sqlite3

from kzocr.proofread.api import LineItem, _read_line, scale_char_box


def _row_with_columns(char_boxes: list, crop_img: bytes) -> sqlite3.Row:
    """构造含 char_boxes + crop_img 列的 sqlite3.Row。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE Line (
            id TEXT, pageNum INTEGER, paraSeq INTEGER, seqInPara INTEGER,
            engineTexts TEXT, consensus TEXT, humanFinal TEXT, final TEXT,
            confidence REAL, headingLevel INTEGER, auditSource TEXT,
            disputed INTEGER, charLevelJson TEXT, char_boxes TEXT, crop_img BLOB
        )"""
    )
    conn.execute(
        "INSERT INTO Line VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "L1", 1, 1, 1, "{}", "共识", "", "共识", 0.95, 0, "", 0, "",
            json.dumps(char_boxes), crop_img,
        ),
    )
    row = conn.execute("SELECT * FROM Line WHERE id='L1'").fetchone()
    conn.close()
    return row


def _row_without_columns() -> sqlite3.Row:
    """构造旧包（无 char_boxes / crop_img 列）的 sqlite3.Row。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE Line (
            id TEXT, pageNum INTEGER, paraSeq INTEGER, seqInPara INTEGER,
            engineTexts TEXT, consensus TEXT, humanFinal TEXT, final TEXT,
            confidence REAL, headingLevel INTEGER, auditSource TEXT,
            disputed INTEGER, charLevelJson TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO Line VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("L1", 1, 1, 1, "{}", "共识", "", "共识", 0.95, 0, "", 0, ""),
    )
    row = conn.execute("SELECT * FROM Line WHERE id='L1'").fetchone()
    conn.close()
    return row


# =============================================================================
# LineItem 字段暴露
# =============================================================================

def test_lineitem_has_charbox_fields() -> None:
    li = LineItem(
        id="L1", page_num=1, para_seq=1, seq_in_para=1,
        engine_texts="{}", consensus="共识", human_final="", final="共识",
        confidence=0.95, heading_level=0, audit_source="", disputed_sub=0,
        char_level_json="",
    )
    assert isinstance(li.char_boxes, list)
    assert li.char_boxes == []
    assert isinstance(li.crop_img_b64, str)
    assert li.crop_img_b64 == ""


def test_lineitem_roundtrip_charboxes() -> None:
    boxes = [[10, 20, 30, 40], [50, 20, 70, 40]]
    li = LineItem(
        id="L1", page_num=1, para_seq=1, seq_in_para=1,
        engine_texts="{}", consensus="共识", human_final="", final="共识",
        confidence=0.95, heading_level=0, audit_source="", disputed_sub=0,
        char_level_json="", char_boxes=boxes, crop_img_b64="AAA",
    )
    assert li.char_boxes == boxes
    assert li.crop_img_b64 == "AAA"


# =============================================================================
# _read_line 防御性读取
# =============================================================================

def test_read_line_with_charboxes_and_crop() -> None:
    boxes = [[10, 20, 30, 40], [50, 20, 70, 40]]
    raw = b"\x89PNG\r\n\x1a\nFAKEPNG"
    row = _row_with_columns(boxes, raw)
    li = _read_line(sqlite3.connect(":memory:"), row)
    assert li.char_boxes == boxes
    assert li.crop_img_b64 == base64.b64encode(raw).decode()


def test_read_line_empty_charboxes_treated_as_list() -> None:
    row = _row_with_columns([], b"\x89PNG")
    li = _read_line(sqlite3.connect(":memory:"), row)
    assert li.char_boxes == []
    assert li.crop_img_b64 != ""


def test_read_line_old_package_no_columns() -> None:
    row = _row_without_columns()
    # 旧包缺 char_boxes / crop_img 列，必须不抛异常
    li = _read_line(sqlite3.connect(":memory:"), row)
    assert li.char_boxes == []
    assert li.crop_img_b64 == ""
    assert li.id == "L1"
    assert li.consensus == "共识"


# =============================================================================
# scale_char_box 纯函数
# =============================================================================

def test_scale_char_box_identity_at_scale_one() -> None:
    box = [10, 20, 30, 40]
    r = scale_char_box(box, 1.0)
    assert r == {"left": 10, "top": 20, "width": 20, "height": 20}


def test_scale_char_box_scales_down() -> None:
    box = [10, 20, 30, 40]  # w=20 h=20
    r = scale_char_box(box, 0.5)
    assert r == {"left": 5, "top": 10, "width": 10, "height": 10}


def test_scale_char_box_invalid_returns_zeros() -> None:
    assert scale_char_box([], 1.0) == {
        "left": 0, "top": 0, "width": 0, "height": 0,
    }
    assert scale_char_box([1, 2], 2.0) == {
        "left": 0, "top": 0, "width": 0, "height": 0,
    }
