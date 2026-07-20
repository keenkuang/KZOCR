"""_apply_vl_fix 自动回填 human_final 单测（真实 BookDB）。"""

from kzocr.scheduler.cross_align import Divergence, DivergenceArbitration
from kzocr.scheduler.orchestrator import _apply_vl_fix
from kzocr.storage.db import BookDB


def _setup_page_with_line(db: BookDB) -> None:
    """在 BookDB 中插入一页含一段/行数据。"""
    db._conn.execute(
        "INSERT INTO book (book_code) VALUES (?)", (db.book_code,),
    )
    db._conn.execute(
        "INSERT INTO page (page_num, book_code, text, char_boxes) VALUES (?, ?, ?, ?)",
        (0, db.book_code, "服法：附子三钱，温服", "[]"),
    )
    db._conn.execute(
        "INSERT INTO line (page_num, para_seq, line_seq, text, char_boxes) "
        "VALUES (?, ?, ?, ?, ?)",
        (0, 1, 1, "服法：附子三钱，温服", "[]"),
    )
    db._conn.commit()


def test_apply_vl_fix_accepted_a_writes_human_final(tmp_path) -> None:
    """accepted_a 回填 a_seg 到 line.human_final。"""
    db = BookDB("bk_fix", db_dir=str(tmp_path))
    _setup_page_with_line(db)

    d = Divergence(page_no=0, div_type="replace", a_seg="三钱", b_seg="二钱")
    arb = DivergenceArbitration(decision="accepted_a")
    _apply_vl_fix(db, 0, d, arb)

    result = db._conn.execute(
        "SELECT human_final FROM line WHERE page_num=0 AND para_seq=1 AND line_seq=1"
    ).fetchone()
    assert result is not None
    assert result["human_final"] == "三钱", f"expected 三钱, got {result['human_final']}"


def test_apply_vl_fix_accepted_b_writes_human_final(tmp_path) -> None:
    """accepted_b 回填 b_seg 到 line.human_final。"""
    db = BookDB("bk_fix", db_dir=str(tmp_path))
    _setup_page_with_line(db)

    d = Divergence(page_no=0, div_type="replace", a_seg="三钱", b_seg="二钱")
    arb = DivergenceArbitration(decision="accepted_b")
    _apply_vl_fix(db, 0, d, arb)

    result = db._conn.execute(
        "SELECT human_final FROM line WHERE page_num=0 AND para_seq=1 AND line_seq=1"
    ).fetchone()
    assert result is not None
    assert result["human_final"] == "二钱"


def test_apply_vl_fix_skip_non_accepted(tmp_path) -> None:
    """非 accepted_a/b 裁决不写入 human_final。"""
    db = BookDB("bk_fix", db_dir=str(tmp_path))
    _setup_page_with_line(db)

    d = Divergence(page_no=0, div_type="replace", a_seg="三钱", b_seg="二钱")
    arb = DivergenceArbitration(decision="both_wrong")
    _apply_vl_fix(db, 0, d, arb)

    result = db._conn.execute(
        "SELECT human_final FROM line WHERE page_num=0 AND para_seq=1 AND line_seq=1"
    ).fetchone()
    assert result is not None
    assert result["human_final"] == ""  # 未写入


def test_apply_vl_fix_no_match_skips_silently(tmp_path) -> None:
    """行中无匹配文本时静默跳过（不抛错）。"""
    db = BookDB("bk_fix", db_dir=str(tmp_path))
    _setup_page_with_line(db)
    # 行文本为"服法：附子三钱，温服"，"黄芪" 不匹配
    d = Divergence(page_no=0, div_type="replace", a_seg="黄芪", b_seg="黄耆")
    arb = DivergenceArbitration(decision="accepted_a")
    _apply_vl_fix(db, 0, d, arb)  # 不应抛错
    result = db._conn.execute(
        "SELECT human_final FROM line WHERE page_num=0 AND para_seq=1 AND line_seq=1"
    ).fetchone()
    assert result is not None
    assert result["human_final"] == ""  # 未写入
