"""stage 2/3 纯函数测试：build_canonical_chars / map_divergence_to_canonical /
derive_error_records / build_page_canonical_and_errors。

不依赖 fitz/PDF（build 仅在 source_pdf/db_dir/book_code 齐备时才切片）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kzocr.scheduler.canonical import (
    CanonicalChar,
    build_canonical_chars,
    build_page_canonical_and_errors,
    derive_error_records,
    map_divergence_to_canonical,
)


@dataclass
class StubDiv:
    """测试用 Divergence 替身（仅含派生所需字段）。"""

    div_type: str
    a_seg: str = ""
    b_seg: str = ""
    page_no: int = 1
    id: Optional[int] = None


def _boxes(n: int) -> list[list[int]]:
    return [[i * 10, 0, i * 10 + 9, 9] for i in range(n)]


# ── build_canonical_chars ──

def test_build_equal_both_engines():
    chars = build_canonical_chars(
        {"a": "甲乙丙", "b": "甲乙丙"}, "甲乙丙", _boxes(3),
        (1, 1, 1), primary_engine="a",
    )
    assert len(chars) == 3
    for c in chars:
        assert isinstance(c, CanonicalChar)
        assert len(c.engine_records) == 2
        assert c.final_engine == "a"
        assert c.bbox == [c.char_pos * 10, 0, c.char_pos * 10 + 9, 9]


def test_build_replace_single_char():
    chars = build_canonical_chars(
        {"a": "甲乙", "b": "甲丙"}, "甲丙", _boxes(2),
        (1, 1, 1), primary_engine="a",
    )
    assert [c.char_text for c in chars] == ["甲", "丙"]
    # 第 0 字：两引擎均为 甲，final=a
    assert chars[0].final_engine == "a"
    assert {r.engine for r in chars[0].engine_records} == {"a", "b"}
    # 第 1 字：a=乙, b=丙, consensus=丙 → final=b
    assert chars[1].final_engine == "b"
    recs = {r.engine: r.char_text for r in chars[1].engine_records}
    assert recs == {"a": "乙", "b": "丙"}


def test_build_delete_b_missing_char():
    # consensus 只有 甲乙（不含丙），canonical 仅 2 字，b 也读 甲乙 → 两引擎都在
    chars = build_canonical_chars(
        {"a": "甲乙丙", "b": "甲乙"}, "甲乙", _boxes(2),
        (1, 1, 1), primary_engine="a",
    )
    assert len(chars) == 2
    for c in chars:
        assert len(c.engine_records) == 2


def test_build_insert_b_extra_char():
    chars = build_canonical_chars(
        {"a": "甲乙", "b": "甲乙丙"}, "甲乙", _boxes(2),
        (1, 1, 1), primary_engine="a",
    )
    assert len(chars) == 2
    for c in chars:
        assert len(c.engine_records) == 2


def test_build_empty_engines():
    assert build_canonical_chars({}, "甲乙", _boxes(2), (1, 1, 1)) == []


# ── map_divergence_to_canonical ──

def test_map_replace_found():
    div = StubDiv("replace", a_seg="乙", b_seg="丙")
    assert map_divergence_to_canonical(div, "甲丙") == [1]


def test_map_delete_not_in_canonical():
    div = StubDiv("delete", a_seg="丙", b_seg="")
    assert map_divergence_to_canonical(div, "甲乙") == []


def test_map_insert_not_in_canonical():
    div = StubDiv("insert", a_seg="", b_seg="丙")
    assert map_divergence_to_canonical(div, "甲乙") == []


# ── derive_error_records ──

def test_derive_replace_b_wrong():
    # canonical=甲丙：gold[1]=丙 == b_seg → engine_a 错（把 丙 读成 乙）
    div = StubDiv("replace", a_seg="乙", b_seg="丙", id=10)
    recs = derive_error_records(div, "甲丙", "a", "b", 1, line_seq=1,
                                source_divergence_id=div.id)
    assert len(recs) == 1
    r = recs[0]
    assert r.engine == "a" and r.wrong_char == "乙" and r.correct_char == "丙"
    assert r.error_type == "replace" and r.source_divergence_id == 10


def test_derive_replace_a_wrong():
    div = StubDiv("replace", a_seg="乙", b_seg="丙", id=11)
    recs = derive_error_records(div, "甲丙", "a", "b", 1, line_seq=1, human_final="甲乙")
    # human_final[1]=乙 == a_seg → b 错
    assert len(recs) == 1
    assert recs[0].engine == "b" and recs[0].wrong_char == "丙" and recs[0].correct_char == "乙"


def test_derive_replace_both_wrong():
    # canonical=甲丁：两片段 乙/丙 均不在 canonical → 无法锚定 gold 字位 → correct=None
    div = StubDiv("replace", a_seg="乙", b_seg="丙", id=12)
    recs = derive_error_records(div, "甲丁", "a", "b", 1, line_seq=1)
    assert len(recs) == 2
    assert {r.engine for r in recs} == {"a", "b"}
    for r in recs:
        assert r.correct_char is None
        assert r.char_pos is None


def test_derive_delete_and_insert():
    del_div = StubDiv("delete", a_seg="丙", b_seg="", id=20)
    recs = derive_error_records(del_div, "甲乙", "a", "b", 1, line_seq=1)
    assert len(recs) == 1
    assert recs[0].error_type == "delete" and recs[0].engine == "b"
    assert recs[0].wrong_char is None and recs[0].correct_char == "丙"

    ins_div = StubDiv("insert", a_seg="", b_seg="丙", id=21)
    recs = derive_error_records(ins_div, "甲乙", "a", "b", 1, line_seq=1)
    assert len(recs) == 1
    assert recs[0].error_type == "insert" and recs[0].engine == "b"
    assert recs[0].wrong_char == "丙" and recs[0].correct_char is None


# ── build_page_canonical_and_errors ──

def test_build_page_helper():
    page_lines = [(1, 1, "甲乙", _boxes(2)), (1, 2, "丙丁", _boxes(2))]
    canon, errs = build_page_canonical_and_errors(
        page_lines, "甲乙\n丙丁", "甲乙\n丙丁", "a", "b", 1,
    )
    assert len(canon) == 4
    assert errs == []
    # 验证层级键正确
    assert {(c.para_seq, c.line_seq, c.char_pos) for c in canon} == {
        (1, 1, 0), (1, 1, 1), (1, 2, 0), (1, 2, 1)
    }


def test_build_page_helper_with_div():
    page_lines = [(1, 1, "甲丙", _boxes(2))]
    divs = [StubDiv("replace", a_seg="乙", b_seg="丙", id=99)]
    canon, errs = build_page_canonical_and_errors(
        page_lines, "甲乙", "甲丙", "a", "b", 1, divs=divs,
    )
    assert len(errs) == 1
    # 该行星共识=甲丙，gold[1]=丙 == b_seg → engine_a 错（读 丙 成 乙）
    assert errs[0].engine == "a" and errs[0].wrong_char == "乙" and errs[0].correct_char == "丙"
