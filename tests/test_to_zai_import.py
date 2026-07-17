"""to_zai_prisma 导入回路 + 冻结单测（mock 数据，无引擎/Postgres）。

验证：push_book_to_zai 导出可移植校对包 → 人工改 humanFinal → import_proofread_package
按层级键写回 BookDB；freeze_custom_db 冻结旧库。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from kzocr.engine.mock import mock_book_result
from kzocr.engine.types import (
    BookResult, PageResult, ParagraphResult, LineResult,
)
from kzocr.adapter.to_zai_prisma import (
    push_book_to_zai, import_proofread_package, freeze_custom_db,
)
from kzocr.storage.db import BookDB


def test_export_then_import_roundtrip():
    book = mock_book_result("TCM-IMP-001")
    book.is_mock = False  # 实际数据允许写入

    tmp_bookdb = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp_bookdb
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = tempfile.mktemp(suffix=".db")
    try:
        # 1) 导出校对包（push 同时落 BookDB 系统 of record）
        res = push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True)
        assert res["book_code"] == "TCM-IMP-001"
        assert "bookdb_path" in res

        # 2) 模拟人工终校：在 custom.db 写 humanFinal + 一条 Proofread
        con = sqlite3.connect(pkg)
        con.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("方用白术三钱，茯苓二钱。（人工终校）",),
        )
        con.execute(
            "INSERT INTO Proofread (id,pageNum,bookCode,paraSeq,seqInPara,lineId,originalText,"
            "correctedText,changeType,severity,notes,triggeredPattern) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p1", 1, "TCM-IMP-001", 1, 1, "TCM-IMP-001-P1-1-1",
             "方用白木三钱", "方用白术三钱", "herb", "critical",
             "白木→白术", "HERB-白术"),
        )
        con.commit()
        con.close()

        # 3) 导入回写 BookDB（层级键映射）
        imp = import_proofread_package(db_path=pkg, book_code="TCM-IMP-001")
        assert imp["book_code"] == "TCM-IMP-001"
        assert imp["imported_lines"] == 1
        # 校对包含 mock 自带 2 条 + 手动插入 1 条 = 3 条
        assert imp["imported_proofreads"] == 3

        # 4) 断言 BookDB 收到人工终校与校对记录
        db = BookDB("TCM-IMP-001", db_dir=tmp_bookdb)
        try:
            hf = db.get_line_human_final(1, 1, 1)
            assert hf == "方用白术三钱，茯苓二钱。（人工终校）"
            proof = db.get_proofreads(page_num=1)
            assert len(proof) == 2
            assert any("方用白术三钱" in (p["corrected_text"] or "") for p in proof)
        finally:
            db.close()
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp_bookdb).glob("TCM-IMP-001.db*"):
            f.unlink()
        Path(pkg).unlink(missing_ok=True)


def test_import_ignores_unproofed_lines():
    """无 humanFinal 的行视为未校对，不应覆盖 BookDB 已有引擎结果。"""
    book = mock_book_result("TCM-IMP-002")
    book.is_mock = False
    tmp_bookdb = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp_bookdb
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = tempfile.mktemp(suffix=".db")
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True)
        # 仅写 Proofread（无 humanFinal 改动）
        con = sqlite3.connect(pkg)
        con.execute(
            "INSERT INTO Proofread (id,pageNum,bookCode,paraSeq,seqInPara,lineId,originalText,"
            "correctedText,changeType,severity,notes,triggeredPattern) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p2", 2, "TCM-IMP-002", 1, 1, "TCM-IMP-002-P2-1-1",
             "取足三裹", "取足三里", "meridian", "critical", "足三裹→足三里", "MER-足三里"),
        )
        con.commit()
        con.close()

        imp = import_proofread_package(db_path=pkg, book_code="TCM-IMP-002")
        assert imp["imported_lines"] == 0
        # 校对包含 mock 自带 2 条 + 手动插入 1 条 = 3 条
        assert imp["imported_proofreads"] == 3
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp_bookdb).glob("TCM-IMP-002.db*"):
            f.unlink()
        Path(pkg).unlink(missing_ok=True)


def test_import_infers_book_code_from_package():
    """不传 book_code 时，应从 custom.db 的 Line.bookCode 列推断（修复 CRITICAL KeyError）。"""
    book = mock_book_result("TCM-IMP-003")
    book.is_mock = False
    tmp_bookdb = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp_bookdb
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = tempfile.mktemp(suffix=".db")
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True)
        # 不传 book_code，依赖包内 bookCode 推断
        imp = import_proofread_package(db_path=pkg)
        assert imp["book_code"] == "TCM-IMP-003"
        # mock 自带 2 条 proofread 应被导入
        assert imp["imported_proofreads"] == 2

        # 改一条 humanFinal 后再导入（不传 book_code），应写回 BookDB
        con = sqlite3.connect(pkg)
        con.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=2 AND paraSeq=1 AND seqInPara=1",
            ("取足三里、合谷以调气和胃。（人工终校）",),
        )
        con.commit()
        con.close()
        imp2 = import_proofread_package(db_path=pkg)
        assert imp2["book_code"] == "TCM-IMP-003"
        assert imp2["imported_lines"] == 1
        db = BookDB("TCM-IMP-003", db_dir=tmp_bookdb)
        try:
            hf = db.get_line_human_final(2, 1, 1)
            assert hf == "取足三里、合谷以调气和胃。（人工终校）"
        finally:
            db.close()
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp_bookdb).glob("TCM-IMP-003.db*"):
            f.unlink()
        Path(pkg).unlink(missing_ok=True)


def test_export_import_multi_paragraph():
    """多段落页（code-reviewer W2）：导出/导入闭环 para_seq 按位置正确映射，不串段。"""
    pages = [PageResult(
        page_num=1,
        paragraphs=[
            ParagraphResult(sequence_in_page=1, lines=[
                LineResult(sequence_in_paragraph=1, final="甲", consensus="甲"),
                LineResult(sequence_in_paragraph=2, final="乙", consensus="乙"),
            ]),
            ParagraphResult(sequence_in_page=2, lines=[
                LineResult(sequence_in_paragraph=1, final="丙", consensus="丙"),
            ]),
        ],
    )]
    book = BookResult(book_code="TCM-MP-001", title="多段测试", pages=pages)
    book.is_mock = False

    tmp_bookdb = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp_bookdb
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = tempfile.mktemp(suffix=".db")
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True)
        # 校验导出包层级键按位置派生：段1两行(1,1)(1,2)，段2一行(2,1)
        con = sqlite3.connect(pkg)
        keys = [tuple(r) for r in con.execute(
            "SELECT paraSeq,seqInPara FROM Line WHERE pageNum=1 ORDER BY paraSeq,seqInPara"
        ).fetchall()]
        con.close()
        assert keys == [(1, 1), (1, 2), (2, 1)]

        # 人工终校第 2 段第 1 行（para_seq=2, seqInPara=1）
        con = sqlite3.connect(pkg)
        con.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=2 AND seqInPara=1",
            ("丙（终校）",),
        )
        con.commit()
        con.close()

        imp = import_proofread_package(db_path=pkg, book_code="TCM-MP-001")
        assert imp["imported_lines"] == 1
        db = BookDB("TCM-MP-001", db_dir=tmp_bookdb)
        try:
            # 第 2 段第 1 行收到终校
            assert db.get_line_human_final(1, 2, 1) == "丙（终校）"
            # 第 1 段第 1 行未被误写
            assert db.get_line_human_final(1, 1, 1) == ""
        finally:
            db.close()
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp_bookdb).glob("TCM-MP-001.db*"):
            f.unlink()
        Path(pkg).unlink(missing_ok=True)


def test_reexport_preserves_human_final():
    """重导出闭环（W1）：import 写回 BookDB 的 human_final 应在再次导出时合并进新包。"""
    book = mock_book_result("TCM-RX-001")
    book.is_mock = False

    tmp_bookdb = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp_bookdb
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg1 = tempfile.mktemp(suffix=".db")
    pkg2 = tempfile.mktemp(suffix=".db")
    try:
        # 第一版导出
        push_book_to_zai(book, db_path=pkg1, skip_prisma_marker=True)
        # 人工终校 page1 段1 行1
        con = sqlite3.connect(pkg1)
        con.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("方用白术三钱，茯苓二钱。（终校）",),
        )
        con.commit()
        con.close()
        # 导入写回 BookDB（系统 of record）
        imp = import_proofread_package(db_path=pkg1, book_code="TCM-RX-001")
        assert imp["imported_lines"] == 1

        # 重新导出到新路径
        res2 = push_book_to_zai(book, db_path=pkg2, skip_prisma_marker=True, persist_bookdb=True)
        assert res2["bookdb_persisted"] is True

        # 新包应包含已导入的人工终校（闭环无损）
        con = sqlite3.connect(pkg2)
        hf = con.execute(
            "SELECT humanFinal FROM Line WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1"
        ).fetchone()[0]
        con.close()
        assert hf == "方用白术三钱，茯苓二钱。（终校）"
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp_bookdb).glob("TCM-RX-001.db*"):
            f.unlink()
        Path(pkg1).unlink(missing_ok=True)
        Path(pkg2).unlink(missing_ok=True)


def test_freeze_custom_db():
    pkg = tempfile.mktemp(suffix=".db")
    Path(pkg).write_text("x")
    try:
        freeze_custom_db(pkg)
        assert Path(pkg + ".frozen").exists()
        # 应剥夺写权限（0440）
        assert (os.stat(pkg).st_mode & 0o222) == 0
    finally:
        Path(pkg).unlink(missing_ok=True)
        Path(pkg + ".frozen").unlink(missing_ok=True)
