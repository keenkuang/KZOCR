"""kzocr.scheduler.cross_align 单元测试（借鉴 ocr_pipeline_v2 验证设计）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kzocr.scheduler.cross_align import (
    align_engines,
    run_cross_align,
    strip_punct,
    write_divergences,
)

# 形近字黑名单（wrong -> correct），取自 KZOCR resources/confusion_set.json 口径
CONFUSION = {"芩": "苓", "炙": "灸", "黄": "皇", "麥": "麦"}


def test_strip_punct_removes_punct_and_ws():
    assert strip_punct("方，木。\n水 火") == "方木水火"
    assert strip_punct("") == ""


def test_align_engines_identical_no_divergence():
    text = "治宜清热解毒利水消肿"
    divs = align_engines(text, text, confusion_set=CONFUSION)
    assert divs == []


def test_align_engines_replace_captures_segment():
    a = "瓜蒌皮"
    b = "瓜萎皮"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert len(divs) == 1
    d = divs[0]
    assert d.div_type == "replace"
    assert d.a_seg == "蒌"
    assert d.b_seg == "萎"
    assert "【蒌】" in d.a_context


def test_numeral_divergence_is_high_priority():
    # 剂量数字分歧：三↔二（中文数字，方剂书最危险）
    a = "附子三钱"
    b = "附子二钱"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert all(d.priority == "high" for d in divs)
    assert any(d.a_seg == "三" and d.b_seg == "二" for d in divs)


def test_confusion_blacklist_is_high_priority():
    # 形近字：芩↔苓
    a = "黄芩"
    b = "黄苓"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert divs[0].priority == "high"


def test_non_confusion_normal_priority():
    a = "咳嗽"
    b = "气喘"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert divs[0].priority == "normal"


def test_run_cross_align_fills_page_and_engine():
    divs = run_cross_align(
        24, "附子三钱", "附子二钱", confusion_set=CONFUSION,
        engine_a="kimi", engine_b="sensenova",
    )
    assert divs
    assert all(d.page_no == 24 for d in divs)
    assert all(d.engine_a == "kimi" for d in divs)
    assert all(d.engine_b == "sensenova" for d in divs)
    assert all(d.priority == "high" for d in divs)


def test_write_divergences_roundtrip(tmp_path: Path):
    db = tmp_path / "cross.db"
    divs = run_cross_align(
        24, "附子三钱", "附子二钱", confusion_set=CONFUSION,
        engine_a="kimi", engine_b="sensenova",
    )
    n = write_divergences(db, 24, divs, engine_a="kimi", engine_b="sensenova")
    assert n == len(divs)

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT page_no, div_type, a_seg, b_seg, priority, engine_a, engine_b "
        "FROM cross_divergence ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == len(divs)
    assert rows[0][0] == 24
    assert rows[0][4] == "high"
    assert rows[0][5] == "kimi"
    assert rows[0][6] == "sensenova"


def test_write_divergences_idempotent_table(tmp_path: Path):
    # 重复调用不应抛错（CREATE TABLE IF NOT EXISTS）
    db = tmp_path / "cross.db"
    divs = align_engines("附子三钱", "附子二钱", confusion_set=CONFUSION)
    write_divergences(db, 1, divs)
    write_divergences(db, 1, divs)
    conn = sqlite3.connect(str(db))
    cnt = conn.execute("SELECT COUNT(*) FROM cross_divergence").fetchone()[0]
    conn.close()
    assert cnt == len(divs) * 2


def test_real_tcm_snippet_divergence():
    # 参考 ocr_pipeline_v2 实测：午2~3时 ↔ 午？-3时
    a = "服法：午2-3时温服"
    b = "服法：午？-3时温服"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    # 数字 2 仍在，含数字的分歧应 high
    assert any(d.priority == "high" for d in divs)
