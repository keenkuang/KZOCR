"""scripts/learn_confusion_from_divergences.py 的纯逻辑回归测试。

覆盖：单字 replace 对的频次统计、无序对的合并与方向判定（锚定/未锚定）、
候选报告写出、以及 --apply 仅回写已锚定高频对（mock 掉真实 learned 文件写入）。
不依赖真实 OCR 引擎 / PDF。
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

# scripts/ 不在默认 sys.path，显式加入以导入待测模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import learn_confusion_from_divergences as L  # noqa: E402

from kzocr.scheduler.cross_align import Divergence  # noqa: E402
from kzocr.storage.db import BookDB  # noqa: E402


def _write_divs(db_dir: str, book_code: str, page_divs: list[tuple[int, list[Divergence]]]) -> None:
    db = BookDB(book_code, db_dir=db_dir)
    for page_no, divs in page_divs:
        db.write_cross_divergences(page_no, divs, engine_a="PaddleOCR", engine_b="RapidOCR")
    db.close()


def test_tally_replace_pairs_counts_single_char_replaces(tmp_path):
    d = str(tmp_path)
    _write_divs(
        d,
        "book_a",
        [
            (0, [Divergence(page_no=0, div_type="replace", a_seg="茶", b_seg="荼")]),
            (1, [Divergence(page_no=1, div_type="replace", a_seg="茶", b_seg="荼")]),
            # 多字 replace 不计入
            (2, [Divergence(page_no=2, div_type="replace", a_seg="茯苓", b_seg="茯芩")]),
            # delete / insert 不计入
            (3, [Divergence(page_no=3, div_type="delete", a_seg="术", b_seg="")]),
        ],
    )
    tally = L.tally_replace_pairs(d)
    assert tally[("茶", "荼")]["count"] == 2
    assert tally[("茶", "荼")]["books"] == {"book_a"}
    assert ("茯苓", "茯芩") not in tally
    assert ("术", "") not in tally


def test_build_candidates_anchored_detection():
    tally = {
        ("茶", "荼"): {"count": 5, "books": {"a"}},
        ("a", "b"): {"count": 2, "books": {"a"}},  # 低于 min_count
    }
    # 静态集锚定 茶->荼 方向
    cands = L.build_candidates(tally, {"茶": "荼"}, min_count=3)
    assert len(cands) == 1
    c = cands[0]
    assert c.wrong == "茶" and c.correct == "荼"
    assert c.anchored is True
    assert c.total == 5 and c.books == 1


def test_build_candidates_unanchored_picks_higher_ordered_count():
    tally = {
        ("X", "Y"): {"count": 7, "books": {"a", "b"}},
        ("Y", "X"): {"count": 2, "books": {"a"}},
    }
    cands = L.build_candidates(tally, {}, min_count=3)
    assert len(cands) == 1
    c = cands[0]
    # X->Y 有序频次更高，推断 X 为误认侧
    assert c.wrong == "X" and c.correct == "Y"
    assert c.anchored is False
    assert c.books == 2


def test_run_writes_candidate_report(tmp_path):
    d = str(tmp_path)
    _write_divs(
        d,
        "book_a",
        [
            (0, [Divergence(page_no=0, div_type="replace", a_seg="茶", b_seg="荼")]),
            (1, [Divergence(page_no=1, div_type="replace", a_seg="茶", b_seg="荼")]),
            (2, [Divergence(page_no=2, div_type="replace", a_seg="杏", b_seg="杳")]),
            (3, [Divergence(page_no=3, div_type="replace", a_seg="杏", b_seg="杳")]),
        ],
    )
    out = tmp_path / "cands.json"
    summary = L.run(d, min_count=2, candidates_out=str(out))
    assert summary["candidates"] == 2
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert all("wrong" in c and "correct" in c and "anchored" in c for c in payload)


def test_run_marks_anchored_when_static_confirms(tmp_path):
    d = str(tmp_path)
    _write_divs(
        d,
        "book_a",
        [
            (0, [Divergence(page_no=0, div_type="replace", a_seg="茶", b_seg="荼")]),
            (1, [Divergence(page_no=1, div_type="replace", a_seg="茶", b_seg="荼")]),
            (2, [Divergence(page_no=2, div_type="replace", a_seg="杏", b_seg="杳")]),
            (3, [Divergence(page_no=3, div_type="replace", a_seg="杏", b_seg="杳")]),
        ],
    )
    # 静态集只锚定 茶->荼（杏/杳 未锚定，方向不可靠）
    fake_static = {"茶": "荼"}
    with mock.patch.object(L, "load_confusion_set", return_value=fake_static):
        summary = L.run(d, min_count=2, candidates_out=str(tmp_path / "c.json"))
    assert summary["anchored"] == 1
    payload = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    by_pair = {(c["wrong"], c["correct"]): c for c in payload}
    assert by_pair[("茶", "荼")]["anchored"] is True
    assert by_pair[("杏", "杳")]["anchored"] is False
