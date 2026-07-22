"""kzocr/doc/zai.py + export.py + proofread.py + freeze.py 覆盖补全（纯逻辑，零网络/零引擎）。

针对覆盖报告中未覆盖的分支：
- push_book_to_zai 的 is_mock 阻断分支
- 三大范式库（herb/meridian/context）+ Term + Formula/FormulaIngredient 插入分支
- export_markdown 正文/范式/术语/方剂渲染（含 out_path 写文件分支）
- 冻结库保护：默认拒覆盖抛错、overwrite=True 解除冻结重写
- import_proofread_package 无 Line 行时返回 book_code=None

复用 mock_book_result 仅用于构造页面骨架；范式/术语/方剂字段为新增，零运行时风险。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from kzocr.engine.mock import mock_book_result
from kzocr.engine.types import (
    BookResult,
    ContextPattern,
    FormulaEntry,
    FormulaIngredient,
    HerbPattern,
    MeridianPattern,
    TermEntry,
)
from kzocr.doc import push_book_to_zai, export_markdown, import_proofread_package
from kzocr.doc.freeze import freeze_custom_db
from kzocr.storage.db import BookDB


def _book_with_extras(code: str = "TCM-COV-001") -> BookResult:
    """构造一本含范式库/术语/方剂的书（is_mock 默认 False，可正常导出）。"""
    base = mock_book_result(code)
    base.is_mock = False
    base.herb_patterns = [
        HerbPattern(correct_name="白术", ocr_error_pattern="白木", pattern_type="glyph_shape",
                    is_toxic=False, severity="critical", source_books='["src"]', evidence_count=2),
    ]
    base.meridian_patterns = [
        MeridianPattern(correct_name="足三里", ocr_error_pattern="足三裹", entity_type="point",
                        meridian_belonging="胃经", body_region="下肢", severity="critical",
                        source_books='["src"]', evidence_count=1),
    ]
    base.context_patterns = [
        ContextPattern(pattern_text="同上", pattern_type="same_as_above", regex=None,
                       example="同上", discovered_count=3, source_books='["src"]'),
    ]
    base.terms = [
        TermEntry(term_name="方剂", sublib="方剂", error_pattern="方齐", correct_form="方剂",
                  scope="global", scope_score=1, confidence=0.9),
    ]
    base.formulas = [
        FormulaEntry(formula_name="四君子汤", ingredients=[
            FormulaIngredient(herb_name="人参", dosage_value="三钱", unit="钱",
                              role_in_formula="君", is_toxic=False),
            FormulaIngredient(herb_name="白术", dosage_value="三钱", unit="钱",
                              role_in_formula="臣", is_toxic=False),
        ]),
    ]
    return base


def test_push_blocks_mock_book() -> None:
    """is_mock=True 的桩数据不得入校对台，返回 blocked 标记（167-172）。"""
    res = push_book_to_zai(mock_book_result("TCM-MOCK-X"), db_path=Path(tempfile.mktemp(suffix=".db")))
    assert res.get("blocked") == "is_mock"
    assert res.get("published") is False


def test_push_inserts_pattern_libraries() -> None:
    """herb/meridian/context 三大范式库均写入 Pattern 表（322-352）。"""
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True, register_postgres=False)
        con = sqlite3.connect(pkg)
        try:
            rows = con.execute("SELECT lib, COUNT(*) FROM Pattern GROUP BY lib").fetchall()
            by_lib = {r[0]: r[1] for r in rows}
        finally:
            con.close()
        assert by_lib.get("herb") == 1
        assert by_lib.get("meridian") == 1
        assert by_lib.get("context") == 1
    finally:
        pkg.unlink(missing_ok=True)


def test_push_inserts_terms() -> None:
    """Term 表写入术语（354-362）。"""
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True, register_postgres=False)
        con = sqlite3.connect(pkg)
        try:
            count = con.execute("SELECT COUNT(*) FROM Term").fetchone()[0]
            name = con.execute("SELECT termName FROM Term").fetchone()[0]
        finally:
            con.close()
        assert count == 1
        assert name == "方剂"
    finally:
        pkg.unlink(missing_ok=True)


def test_push_inserts_formulas() -> None:
    """Formula + FormulaIngredient 表写入方剂与组分（364-379）。"""
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True, register_postgres=False)
        con = sqlite3.connect(pkg)
        try:
            fcount = con.execute("SELECT COUNT(*) FROM Formula").fetchone()[0]
            icount = con.execute("SELECT COUNT(*) FROM FormulaIngredient").fetchone()[0]
            herb = con.execute("SELECT herbName FROM FormulaIngredient").fetchone()[0]
        finally:
            con.close()
        assert fcount == 1
        assert icount == 2
        assert herb == "人参"
    finally:
        pkg.unlink(missing_ok=True)


def test_export_markdown_returns_text() -> None:
    """export_markdown 无 out_path 返回 markdown 文本（535-585）。"""
    book = _book_with_extras()
    md = export_markdown(book)
    assert isinstance(md, str)
    assert book.title in md
    assert "白术" in md          # 药名范式
    assert "足三里" in md        # 经络范式
    assert "同上" in md          # 语境范式
    assert "方剂" in md          # 术语
    assert "四君子汤" in md      # 方剂
    assert "人参" in md          # 组分


def test_export_markdown_writes_file() -> None:
    """export_markdown 带 out_path 写文件并返回路径（580-585）。"""
    book = _book_with_extras()
    out = Path(tempfile.mktemp(suffix=".md"))
    try:
        res = export_markdown(book, out_path=out)
        assert res == str(out)
        assert out.exists()
        assert book.title in out.read_text(encoding="utf-8")
    finally:
        out.unlink(missing_ok=True)


def test_push_to_frozen_db_raises() -> None:
    """目标库已冻结且无 overwrite → 抛 RuntimeError（206-210）。"""
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    pkg.write_text("x")
    try:
        freeze_custom_db(pkg)
        import pytest

        with pytest.raises(RuntimeError, match="已冻结"):
            push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True,
                            register_postgres=False)
    finally:
        pkg.unlink(missing_ok=True)
        Path(str(pkg) + ".frozen").unlink(missing_ok=True)


def test_push_overwrite_frozen_db() -> None:
    """overwrite=True 解除冻结并重写成功（211-216）。"""
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    pkg.write_text("x")
    try:
        freeze_custom_db(pkg)
        res = push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True,
                               register_postgres=False, overwrite=True)
        assert res["book_code"] == "TCM-COV-001"
        assert not Path(str(pkg) + ".frozen").exists()
    finally:
        pkg.unlink(missing_ok=True)


def test_import_no_line_rows_returns_none_book_code(monkeypatch) -> None:
    """custom.db 无 Line 行且不传 book_code → 返回 book_code=None（448）。

    退化包（无 Line 行，来源 hash 已不可信）→ 按 KZOCR_ALLOW_LEGACY=1 放行旧包，
    导入仍无法推断 book_code。
    """
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True, register_postgres=False)
        # 清空 Line 行，使导入无法从包内推断 book_code；同时移除来源校验信息
        # （无 Line 行的包来源 hash 已不匹配），模拟不可信旧包走 legacy 放行。
        con = sqlite3.connect(pkg)
        con.execute("DELETE FROM Line")
        con.execute("DROP TABLE IF EXISTS ExportMeta")
        con.commit()
        con.close()
        monkeypatch.setenv("KZOCR_ALLOW_LEGACY", "1")
        imp = import_proofread_package(db_path=pkg)
        assert imp["book_code"] is None
        assert imp["imported_lines"] == 0
        assert imp["imported_proofreads"] == 0
    finally:
        pkg.unlink(missing_ok=True)


def test_import_missing_package_raises() -> None:
    """校对包文件不存在 → FileNotFoundError（425）。"""
    import pytest

    with pytest.raises(FileNotFoundError):
        import_proofread_package(db_path=Path(tempfile.mktemp(suffix=".db")))


def test_push_bookdb_persist_failure_logged(monkeypatch) -> None:
    """BookDB 系统 of record 落库失败 → 不阻断导出，记 DATA INTEGRITY 告警（185-188）。"""
    import kzocr.storage.db as _dbmod

    def _boom(book: BookResult, db_dir: str = "") -> None:
        raise RuntimeError("simulated DB down")

    monkeypatch.setattr(_dbmod.BookDB, "persist_book_result", _boom)
    book = _book_with_extras()
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        res = push_book_to_zai(
            book, db_path=pkg, skip_prisma_marker=True,
            register_postgres=False, persist_bookdb=True,
        )
        assert res["bookdb_persisted"] is False
        assert pkg.exists()  # 导出包仍成功写出
    finally:
        pkg.unlink(missing_ok=True)


def test_export_book_markdown_missing_book_raises(tmp_path) -> None:
    """export_book_markdown book_code 不存在时抛出 ValueError。"""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS Book (bookCode TEXT PRIMARY KEY, title TEXT, publisher TEXT, pubYear INTEGER)")
    conn.close()
    import pytest
    with pytest.raises(ValueError, match="未找到书籍"):
        from kzocr.doc.export import export_book_markdown
        export_book_markdown("no-such-book", db_path=str(db))


def test_push_bookdb_persist_controlled_by_env_var(monkeypatch, tmp_path) -> None:
    """persist_bookdb 未显式传递时由 KZOCR_PERSIST_DB=1 控制。"""
    monkeypatch.setenv("KZOCR_PERSIST_DB", "1")
    monkeypatch.setenv("KZOCR_DB_DIR", str(tmp_path))
    book = _book_with_extras()
    pkg = Path(tmp_path) / "e.db"
    push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True,
                     register_postgres=False, persist_bookdb=None)
    bdb = BookDB(book.book_code, db_dir=str(tmp_path))
    try:
        pages = bdb.get_book_pages()
        assert len(pages) > 0
    finally:
        bdb.close()
