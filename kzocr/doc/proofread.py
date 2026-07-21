"""校对包导入：把 zai 校对台的校对结果回写 BookDB（系统 of record）。

提取自 ``kzocr/adapter/to_zai_prisma.py`` — 文档模块重构 v0.23.0。

v0.25.0 阶段 0：新增 ``validate_proofread_package`` 安全校验 + ``register_postgres`` 默认改为 False。
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from .zai import _resolve_db, _resolve_bookdb_path

logger = logging.getLogger(__name__)

# 最大可导入行数上限（防 DoS / 超大包）
_MAX_IMPORT_LINES = 50000

# 校对包必须包含的核心表
_REQUIRED_TABLES = {"Book", "Page", "Paragraph", "Line", "Proofread"}


def validate_proofread_package(db_path: Path, *, max_lines: int = _MAX_IMPORT_LINES) -> dict:
    """只读校验校对包的安全性与完整性。

    在 ``import_proofread_package`` 之前调用，对外部不可信包做前置检查：

    - 只读连接打开，确保不篡改源包
    - 校验 Schema（必须包含核心表）
    - 校验行数上限（防 DoS）

    Args:
        db_path: 校对包路径。
        max_lines: Line + Proofread 总行数上限（默认 50000）。

    Returns:
        {"valid": True, "line_count": N, "proofread_count": M}

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: Schema 不完整 / 行数超限 / 非 SQLite 文件。
        sqlite3.DatabaseError: 数据库损坏。
    """
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"校对包不存在：{path}")

    # 只读连接，确保不修改源包
    conn = sqlite3.connect(f"file://{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # 获取现有表
        existing = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = _REQUIRED_TABLES - existing
        if missing:
            raise ValueError(f"校对包 schema 不完整，缺少表：{', '.join(sorted(missing))}")

        line_count = conn.execute("SELECT COUNT(*) FROM Line").fetchone()[0]
        proofread_count = conn.execute("SELECT COUNT(*) FROM Proofread").fetchone()[0]
        total = line_count + proofread_count
        if total > max_lines:
            raise ValueError(
                f"校对包行数超限（{total} > {max_lines}），"
                "请确认文件未被篡改或分批导入"
            )
        return {"valid": True, "line_count": line_count, "proofread_count": proofread_count}
    finally:
        conn.close()


def import_proofread_package(db_path: Optional[Path] = None,
                              zai_path: Optional[Path] = None,
                              *, book_code: Optional[str] = None,
                              db_dir: str = "",
                              register_postgres: bool = False,
                              skip_validation: bool = False) -> dict:
    """把校对后的 custom.db 校对结果写回 BookDB（系统 of record）。

    读取 Line.humanFinal（人工终校）与 Proofread，按层级键
    (pageNum, paraSeq, seqInPara) → (page_num, para_seq, line_seq) 映射回写。
    无 humanFinal 的行视为未校对，跳过（BookDB 已存有引擎 final/consensus）。

    book_code 缺省时从 custom.db 的 Line.bookCode 推断。

    Args:
        db_path: 校对包（custom.db）显式路径，覆盖 config.zai_db。
        zai_path: 同 db_path（别名），二者皆空时用 config.zai_db。
        book_code: 显式书籍编码；缺省时从包内 Line.bookCode 推断。
        db_dir: BookDB 目录（默认 KZOCR_DB_DIR 或 cwd/db），回写目标。
        register_postgres: best-effort 把导入的 Proofread 归档进 Postgres
            LineCorrectionArchive（无 PG / 归档失败则静默跳过，不阻断导入）。
            交付模式默认关闭（default=False）。
        skip_validation: 跳过 ``validate_proofread_package`` 前置校验。
            仅内部/测试用，外部不可信包必须校验。

    Returns:
        {"book_code", "imported_lines", "imported_proofreads"}
    """
    db = _resolve_db(db_path, zai_path).resolve()
    if not db.exists():
        raise FileNotFoundError(f"校对包不存在：{db}")
    if not skip_validation:
        validate_proofread_package(db)

    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if book_code:
            where = " WHERE bookCode=?"
            params = (book_code,)
        else:
            where = ""
            params = ()
        line_rows = conn.execute(
            "SELECT pageNum, paraSeq, seqInPara, humanFinal, bookCode FROM Line" + where,
            params,
        ).fetchall()
        proof_rows = conn.execute(
            "SELECT pageNum, paraSeq, seqInPara, lineId, originalText, "
            "correctedText, changeType, severity, notes, "
            "triggeredPattern, bookCode FROM Proofread" + where,
            params,
        ).fetchall()
    finally:
        conn.close()

    bc = book_code or (line_rows[0]["bookCode"] if line_rows else None)
    if not bc:
        return {"book_code": None, "imported_lines": 0, "imported_proofreads": 0}

    target_db_dir = db_dir or os.environ.get("KZOCR_DB_DIR", "")
    from kzocr.storage.db import BookDB
    book_db = BookDB(bc, db_dir=target_db_dir)
    try:
        hf_rows = [
            (r["pageNum"], r["paraSeq"], r["seqInPara"], r["humanFinal"])
            for r in line_rows if r["humanFinal"]
        ]
        imported_lines = book_db.save_line_human_finals(hf_rows)

        proof_list = [
            {
                "page_num": r["pageNum"], "para_seq": r["paraSeq"],
                "line_seq": r["seqInPara"],
                "line_id": r["lineId"], "original_text": r["originalText"],
                "corrected_text": r["correctedText"],
                "change_type": r["changeType"], "severity": r["severity"],
                "notes": r["notes"], "triggered_pattern": r["triggeredPattern"],
            }
            for r in proof_rows
        ]
        imported_proofreads = book_db.save_proofreads(proof_list)
    finally:
        book_db.close()

    # best-effort：把人工校对记录归档进 Postgres（运营/审计用）
    if register_postgres and imported_proofreads:
        _import_proofreads_to_postgres(bc, proof_rows, target_db_dir)

    return {
        "book_code": bc,
        "imported_lines": imported_lines,
        "imported_proofreads": imported_proofreads,
    }


def _import_proofreads_to_postgres(book_code: str, proof_rows: list,
                                   db_dir: str = "") -> None:
    """best-effort：把导入的 Proofread 记录归档进 Postgres（LineCorrectionArchive）。"""
    try:
        from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
    except ImportError:
        return
    try:
        db_pg = RuntimeDB()
        bookdb_path = str(_resolve_bookdb_path(book_code))
        with db_pg.get_cursor() as cur:
            cur.execute(
                "SELECT id FROM BookRegistry WHERE db_path=%s ORDER BY id DESC LIMIT 1",
                (bookdb_path,),
            )
            row = cur.fetchone()
        if not row:
            return
        book_id = row["id"]
        for r in proof_rows:
            original_line_id = (
                r["pageNum"] * 100000 + r["paraSeq"] * 1000 + r["seqInPara"]
            )
            db_pg.archive_line_correction(
                book_id,
                original_line_id,
                original_text=r["originalText"] or "",
                corrected_text=r["correctedText"] or "",
                corrected_by="human",
                correction_stage="human",
            )
    except Exception as e:
        logger.warning("[doc.proofread] Postgres 校对记录归档失败，跳过：%s", e)
