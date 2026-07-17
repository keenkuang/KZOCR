"""适配器：把归一化 BookResult 导出为 zai 校对台的**可移植校对包**，并回写 BookDB / Postgres。

定位（见 docs/plans/db-layering.md §5，三层职责分明）：
- BookDB（每书一个 SQLite，系统 of record）：书籍全量数据（正文/行/段/字符级 bbox/人工终校）。
- PostgreSQL 主库（运营/审计）：仅元数据（BookRegistry + BookMeta + BookContentTree 快照），
  **不存逐行 OCR 结果**；本适配器只 best-effort 注册书籍元数据，无 PG 时静默跳过。
- custom.db（zai 校对工作台）：从 BookResult 打出的**可移植校对包**，交校对方人工校阅；
  校对后由 `import_proofread_package` 按层级键 (pageNum, paraSeq, seqInPara) →
  (page_num, para_seq, line_seq) 导入回写 BookDB。

闭环：`push_book_to_zai`（落 BookDB → 注册 Postgres 元数据 → 写 custom.db）
  →（人工在 zai 校对台改 Line.humanFinal / 加 Proofread）
  → `import_proofread_package`（回写 BookDB 的 human_final / proofread）。

zai 控制台（`tcm_ocr_zai`）完全自包含，数据模型在 `prisma/schema.prisma`。本适配器以同名表/列
（Prisma 对 SQLite 默认字段名即列名）直写其 `db/custom.db`，**不修改 zai 源码**，从而让 zai 的
人工校对工作台零改动即可校阅 kimi 引擎的产出。若目标库不存在或表缺失，会按 schema 子集自动建表。

数据模型以 `kzocr/engine/types.py` 中的 dataclass 为准（只读，本文件不修改它）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from .. import config
from ..engine.types import BookResult

logger = logging.getLogger(__name__)


# 自动建表：列名对齐 prisma/schema.prisma（写库所需子集）
_SCHEMA_DDL = [
    """CREATE TABLE IF NOT EXISTS Book (
        bookCode TEXT PRIMARY KEY, title TEXT, author TEXT, publisher TEXT,
        pubYear INTEGER, pubEra TEXT, bookType TEXT, source TEXT,
        pageCount INTEGER, lineCount INTEGER, cerValue REAL, lineAccuracy REAL,
        isMock INTEGER)""",
    """CREATE TABLE IF NOT EXISTS Page (
        pageNum INTEGER, bookCode TEXT, paragraphCount INTEGER, lineCount INTEGER)""",
    """CREATE TABLE IF NOT EXISTS Paragraph (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT,
        seqInPage INTEGER, isFormulaParagraph INTEGER, verificationStatus TEXT)""",
    """CREATE TABLE IF NOT EXISTS Line (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER,
        seqInPara INTEGER,
        engineTexts TEXT, consensus TEXT, llmCorrected TEXT, glyphVerified TEXT,
        final TEXT, humanFinal TEXT, confidence REAL, auditSource TEXT,
        headingLevel INTEGER, disputed INTEGER, missingCharAlert TEXT,
        extraCharAlert TEXT, charLevelJson TEXT)""",
    """CREATE TABLE IF NOT EXISTS Proofread (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER, seqInPara INTEGER,
        lineId TEXT, originalText TEXT, correctedText TEXT, changeType TEXT, severity TEXT,
        notes TEXT, triggeredPattern TEXT)""",
    """CREATE TABLE IF NOT EXISTS Pattern (
        id TEXT PRIMARY KEY, bookCode TEXT, correctName TEXT, ocrErrorPattern TEXT, patternType TEXT,
        isToxic INTEGER, severity TEXT, sourceBooks TEXT, evidenceCount INTEGER,
        libType INTEGER, entityType TEXT, meridianBelonging TEXT, bodyRegion TEXT,
        patternText TEXT, regex TEXT, example TEXT, lib TEXT)""",
    """CREATE TABLE IF NOT EXISTS Term (
        id TEXT PRIMARY KEY, bookCode TEXT, termName TEXT, sublib TEXT, errorPattern TEXT,
        correctForm TEXT, scope TEXT, scopeScore INTEGER, confidence REAL,
        sourceBooks TEXT)""",
    """CREATE TABLE IF NOT EXISTS Formula (
        id TEXT PRIMARY KEY, bookCode TEXT, formulaName TEXT, sourcePages TEXT, createdAt TEXT)""",
    """CREATE TABLE IF NOT EXISTS FormulaIngredient (
        id TEXT PRIMARY KEY, formulaId TEXT, bookCode TEXT, herbName TEXT, herbCorrectedName TEXT,
        dosageValue TEXT, unit TEXT, roleInFormula TEXT, isToxic INTEGER)""",
]


def _uid() -> str:
    return "c" + uuid.uuid4().hex


def _restrict_db_perms(db: Path) -> None:
    """库文件限制为本用户读写（0600），避免同机其他用户读取含敏感文本的库。"""
    try:
        os.chmod(str(db), 0o600)
    except OSError:
        pass


def _resolve_db(db_path: Optional[Path], zai_path: Optional[Path]) -> Path:
    if db_path:
        return Path(db_path)
    if zai_path:
        return Path(zai_path)
    return Path(config.config.zai_db)


def _resolve_bookdb_path(book_code: str) -> Path:
    """解析 BookDB 文件路径（与 storage.db.BookDB 一致：KZOCR_DB_DIR 或 cwd/db）。"""
    db_dir = os.environ.get("KZOCR_DB_DIR", "") or os.path.join(os.getcwd(), "db")
    return Path(db_dir) / f"{book_code}.db"


def _register_postgres_meta(book: BookResult, bookdb_path: str,
                            pdf_path: Optional[Path]) -> dict:
    """best-effort：把书籍元数据注册/更新到 PostgreSQL 主库（BookRegistry+BookMeta）。

    无 psycopg2 / 无 PG 连接时静默跳过，不阻断导出。
    """
    try:
        from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
    except ImportError:
        return {"registered": False, "reason": "psycopg2 不可用"}
    try:
        db_pg = RuntimeDB()
        with db_pg.get_cursor() as cur:
            cur.execute(
                "SELECT id FROM BookRegistry WHERE db_path=%s ORDER BY id DESC LIMIT 1",
                (bookdb_path,),
            )
            row = cur.fetchone()
        if row:
            book_id = row["id"]
            db_pg.set_book_meta(
                book_id, title=book.title, author=book.author,
                publisher=book.publisher, pub_year=book.pub_year,
            )
        else:
            book_id = db_pg.register_book(
                str(pdf_path) if pdf_path else "", db_path=bookdb_path
            )
            db_pg.set_book_meta(
                book_id, title=book.title, author=book.author,
                publisher=book.publisher, pub_year=book.pub_year,
            )
        db_pg.update_book_status(book_id, "proofreading")
        return {"registered": True, "book_id": book_id}
    except Exception as e:  # 连接失败/无表等：不阻断导出
        logger.warning("[adapter] Postgres 元数据注册失败，跳过：%s", e)
        return {"registered": False, "error": str(e)}


def push_book_to_zai(book: BookResult, db_path: Optional[Path] = None,
                     zai_path: Optional[Path] = None,
                     skip_prisma_marker: bool = False,
                     *, pdf_path: Optional[Path] = None,
                     persist_bookdb: Optional[bool] = None,
                     register_postgres: bool = True,
                     overwrite: bool = False) -> dict:
    """把归一化 BookResult 打包导出为可移植校对包（custom.db），并回写 BookDB/Postgres。

    定位（见 docs/plans/db-layering.md §5）：
    - BookDB（每书 SQLite）= 系统 of record，存全书正文/行/段/字符级 bbox/人工终校。
    - custom.db（zai 校对工作台）= 从 BookResult 打出的可移植校对包，交校对方，校对后
      由 import_proofread_package 导入回 BookDB。
    - PostgreSQL 主库 = 仅元数据（BookRegistry+BookMeta）；逐行 OCR 结果不入主库。

    Args:
        book: 归一化结果。
        db_path / zai_path: 校对包输出路径（覆盖 config.zai_db）。
        skip_prisma_marker: 跳过写 .zai_prisma_marker。
        pdf_path: 源 PDF 路径（用于 Postgres BookRegistry 注册；可空）。
        persist_bookdb: 是否落 BookDB。默认随 KZOCR_PERSIST_DB 环境变量
            （与 run_engine 同开关）；显式传值覆盖。
        register_postgres: 是否 best-effort 注册 Postgres 元数据（无 PG 时自动跳过）。
    """
    # B4（v0.3 冻结）：桩/降级假数据(is_mock) 不得入校对台，阻断 publish
    if getattr(book, "is_mock", False):
        logger.error(
            "[adapter] ⚠ 阻断 publish：桩/降级假数据(is_mock=True)，"
            "不得写入校对台（防 round2「假古籍」重演）"
        )
        return {"published": False, "blocked": "is_mock", "bookCode": book.book_code}

    bookdb_path = _resolve_bookdb_path(book.book_code)

    # (1) 落 BookDB（系统 of record）—— best-effort，失败仅告警
    do_persist = persist_bookdb if persist_bookdb is not None \
        else os.environ.get("KZOCR_PERSIST_DB", "0") in ("1", "true", "True")
    bookdb_persisted = False
    if do_persist:
        try:
            from kzocr.storage.db import BookDB
            BookDB.persist_book_result(book, db_dir=os.environ.get("KZOCR_DB_DIR", ""))
            bookdb_persisted = True
        except Exception:
            # 显眼告警：of record 落库失败仍继续写 custom.db，意味着导出包无权威库支撑，
            # 后续 import 写回的人工校对可能静默丢失（W2）。生产环境应关注此错误。
            logger.error(
                "[adapter][DATA INTEGRITY] BookDB 系统 of record 落库失败，"
                "仍继续写 custom.db（导出包无权威库支撑，人工校对可能丢失）",
                exc_info=True,
            )

    # (2) 注册 Postgres 元数据 —— best-effort，无 PG 时静默跳过
    pg_meta = {"registered": False}
    if register_postgres:
        pg_meta = _register_postgres_meta(book, str(bookdb_path), pdf_path)

    # (3) 写 zai 校对工作台包（可移植）
    db = _resolve_db(db_path, zai_path).resolve()
    # 冻结保护（W4）：若目标已被 freeze_custom_db 冻结（只读 + .frozen 标记），说明是历史旧包。
    # 默认不允许静默覆盖（避免误删人工编辑）；需显式 overwrite=True 才解除只读重写，
    # 否则抛清晰错误，引导用户导出到新路径（见 db-layering.md 闭环用法）。
    frozen_marker = Path(str(db) + ".frozen")
    if frozen_marker.exists():
        if not overwrite:
            raise RuntimeError(
                f"[adapter] 目标校对包已冻结（{frozen_marker}），不可静默覆盖。"
                f"请导出到新路径，或显式传 overwrite=True 以解除冻结并重写。"
            )
        logger.warning("[adapter] 目标校对包已冻结，overwrite=True 解除只读并重写：%s", frozen_marker)
        try:
            os.chmod(str(db), 0o600)
            frozen_marker.unlink()
        except OSError:
            pass
    elif not os.access(str(db), os.W_OK) and db.exists():
        try:
            os.chmod(str(db), 0o600)
        except OSError:
            pass
    os.makedirs(db.parent, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=30)
    _restrict_db_perms(db)
    try:
        cur = conn.cursor()
        for ddl in _SCHEMA_DDL:
            cur.execute(ddl)
        # 自动迁移：旧库的四张全局表可能无 bookCode 列，补列并清空一次全域
        # （旧全域数据归属未知，首次迁移清空，避免脏数据串书）
        for t in ("Pattern", "Term", "Formula", "FormulaIngredient"):
            try:
                cur.execute(f"SELECT bookCode FROM {t} LIMIT 1")
            except sqlite3.OperationalError:
                cur.execute(f"ALTER TABLE {t} ADD COLUMN bookCode TEXT")
                cur.execute(f"DELETE FROM {t}")

        # 幂等：同一 bookCode 先清旧数据（全部含 bookCode 列，按书隔离）
        for t in ("Proofread", "Line", "Paragraph", "Page", "Book",
                   "FormulaIngredient", "Formula", "Pattern", "Term"):
            cur.execute(f"DELETE FROM {t} WHERE bookCode=?", (book.book_code,))
        logger.info("[adapter] 已清空旧数据（bookCode=%s），开始写入", book.book_code)

        total_lines = sum(len(para.lines) for p in book.pages for para in p.paragraphs)

        # Book
        cur.execute(
            "INSERT INTO Book (bookCode,title,author,publisher,pubYear,pubEra,bookType,"
            "source,pageCount,lineCount,cerValue,lineAccuracy,isMock) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (book.book_code, book.title, book.author, book.publisher,
             book.pub_year, book.pub_era, book.book_type, book.engine_label,
             len(book.pages), total_lines, None, None,
             int(bool(getattr(book, "is_mock", False)))),
        )

        counts = {"pages": 0, "paragraphs": 0, "lines": 0, "proofreads": 0,
                  "patterns": 0, "terms": 0, "formulas": 0, "ingredients": 0}

        # 重导出闭环（W1）：best-effort 从 BookDB 重新载入人工终校，合并进导出包。
        # §5 决策 BookDB 为权威源，内存中的 ln.human_final 可能不含上一轮 import 写回的终校。
        hf_map: dict[tuple[int, int, int], str] = {}
        try:
            from kzocr.storage.db import BookDB
            if os.path.exists(str(bookdb_path)):
                _bdb = BookDB(book.book_code, db_dir=os.environ.get("KZOCR_DB_DIR", ""))
                try:
                    hf_map = _bdb.get_human_final_map()
                finally:
                    _bdb.close()
        except Exception:
            logger.warning("[adapter] 重导出读取 BookDB 人工终校失败，跳过合并", exc_info=True)

        # Page / Paragraph / Line / Proofread（页-段-行-字 层级）
        # 段序号/行序均按位置派生（1-based），与 storage.db.BookDB.save_book_result 同源，
        # 不依赖引擎是否填充 sequence_in_* 字段，保证 导出→导入 闭环 key 自洽（C1）。
        for p in book.pages:
            page_line_count = sum(len(para.lines) for para in p.paragraphs)
            cur.execute(
                "INSERT INTO Page (pageNum,bookCode,paragraphCount,lineCount) VALUES (?,?,?,?)",
                (p.page_num, book.book_code, len(p.paragraphs), page_line_count),
            )
            counts["pages"] += 1
            for para_seq, para in enumerate(p.paragraphs, start=1):
                para_id = f"{book.book_code}-P{p.page_num}-{para_seq}"
                cur.execute(
                    "INSERT INTO Paragraph (id,pageNum,bookCode,seqInPage,isFormulaParagraph,verificationStatus) "
                    "VALUES (?,?,?,?,?,?)",
                    (para_id, p.page_num, book.book_code, para_seq, 0, None),
                )
                counts["paragraphs"] += 1
                for line_seq, ln in enumerate(para.lines, start=1):
                    line_id = f"{book.book_code}-P{p.page_num}-{para_seq}-{line_seq}"
                    et = ln.engine_texts or {}
                    engine_texts_json = json.dumps(et, ensure_ascii=False)
                    # 人工终校优先用内存值，否则用 BookDB 重新载入的（重导出闭环不掉终校）
                    human_final = ln.human_final or hf_map.get((p.page_num, para_seq, line_seq), "")
                    cur.execute(
                        "INSERT INTO Line (id,pageNum,bookCode,paraSeq,seqInPara,engineTexts,consensus,"
                        "llmCorrected,glyphVerified,final,humanFinal,confidence,auditSource,"
                        "headingLevel,disputed,missingCharAlert,extraCharAlert,charLevelJson) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (line_id, p.page_num, book.book_code, para_seq,
                         line_seq,
                         engine_texts_json, ln.consensus, ln.llm_corrected, ln.glyph_verified,
                         ln.final, human_final, ln.confidence, book.engine_label,
                         None, int(ln.disputed), ln.missing_char_alert,
                         ln.extra_char_alert, ln.char_level_json),
                    )
                    counts["lines"] += 1
                    for pr in ln.proofreads:
                        cur.execute(
                            "INSERT INTO Proofread (id,pageNum,bookCode,paraSeq,seqInPara,lineId,originalText,"
                            "correctedText,changeType,severity,notes,triggeredPattern) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (_uid(), p.page_num, book.book_code, para_seq,
                             line_seq, line_id,
                             pr.original_text, pr.corrected_text, pr.change_type,
                             pr.severity, pr.notes, pr.triggered_pattern),
                        )
                        counts["proofreads"] += 1

        # 三大范式库 → 统一 Pattern 表
        for h in book.herb_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), h.correct_name, h.ocr_error_pattern, h.pattern_type, int(h.is_toxic),
                 h.severity, h.source_books, h.evidence_count, 1, None, None, None,
                 None, None, None, "herb"),
            )
            counts["patterns"] += 1
        for m in book.meridian_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), m.correct_name, m.ocr_error_pattern, None, None,
                 m.severity, m.source_books, m.evidence_count, 2, m.entity_type,
                 m.meridian_belonging, m.body_region, None, None, None, "meridian"),
            )
            counts["patterns"] += 1
        for c in book.context_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), None, None, c.pattern_type, None, None, c.source_books,
                 c.discovered_count, 3, None, None, None, c.pattern_text, c.regex,
                 c.example, "context"),
            )
            counts["patterns"] += 1

        # Term
        for t in book.terms:
            cur.execute(
                "INSERT INTO Term (id,termName,sublib,errorPattern,correctForm,scope,scopeScore,confidence,sourceBooks) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_uid(), t.term_name, t.sublib, t.error_pattern, t.correct_form,
                 t.scope, t.scope_score, t.confidence, None),
            )
            counts["terms"] += 1

        # Formula / FormulaIngredient
        for idx, f in enumerate(book.formulas):
            fid = f"F{idx}"
            cur.execute(
                "INSERT INTO Formula (id,formulaName,sourcePages,createdAt) VALUES (?,?,?,?)",
                (fid, f.formula_name, None, None),
            )
            counts["formulas"] += 1
            for ing in f.ingredients:
                cur.execute(
                    "INSERT INTO FormulaIngredient (id,formulaId,herbName,herbCorrectedName,"
                    "dosageValue,unit,roleInFormula,isToxic) VALUES (?,?,?,?,?,?,?,?)",
                    (_uid(), fid, ing.herb_name, None, ing.dosage_value,
                     ing.unit, ing.role_in_formula, int(ing.is_toxic)),
                )
                counts["ingredients"] += 1

        conn.commit()

        if not skip_prisma_marker:
            marker = Path(str(db) + ".zai_prisma_marker")
            marker.write_text(book.book_code, encoding="utf-8")

        return {
            "book_code": book.book_code,
            "db": str(db),
            "bookdb_path": str(bookdb_path),
            "bookdb_persisted": bookdb_persisted,
            "counts": counts,
            "postgres": pg_meta,
        }
    finally:
        conn.close()


def import_proofread_package(db_path: Optional[Path] = None,
                              zai_path: Optional[Path] = None,
                              *, book_code: Optional[str] = None,
                              db_dir: str = "",
                              register_postgres: bool = True) -> dict:
    """把校对后的 custom.db 校对结果写回 BookDB（系统 of record）。

    读取 Line.humanFinal（人工终校）与 Proofread，按层级键
    (pageNum, paraSeq, seqInPara) → (page_num, para_seq, line_seq) 映射回写。
    无 humanFinal 的行视为未校对，跳过（BookDB 已存有引擎 final/consensus）。

    book_code 缺省时从 custom.db 的 Line.bookCode 推断（单行 SELECT 已含 bookCode 列）。

    Args:
        db_path: 校对包（custom.db）显式路径，覆盖 config.zai_db。
        zai_path: 同 db_path（别名），二者皆空时用 config.zai_db。
        book_code: 显式书籍编码；缺省时从包内 Line.bookCode 推断。
        db_dir: BookDB 目录（默认 KZOCR_DB_DIR 或 cwd/db），回写目标。
        register_postgres: best-effort 把导入的 Proofread 归档进 Postgres
            LineCorrectionArchive（无 PG / 归档失败则静默跳过，不阻断导入）。

    Returns:
        {"book_code", "imported_lines", "imported_proofreads"}
    """
    db = _resolve_db(db_path, zai_path).resolve()
    if not db.exists():
        raise FileNotFoundError(f"校对包不存在：{db}")

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
            f"SELECT pageNum, paraSeq, seqInPara, humanFinal, bookCode FROM Line{where}", params
        ).fetchall()
        proof_rows = conn.execute(
            f"SELECT pageNum, paraSeq, seqInPara, lineId, originalText, correctedText, "
            f"changeType, severity, notes, triggeredPattern, bookCode FROM Proofread{where}", params
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
        # 批量写回人工终校（单次提交，f），仅非空行
        hf_rows = [
            (r["pageNum"], r["paraSeq"], r["seqInPara"], r["humanFinal"])
            for r in line_rows if r["humanFinal"]
        ]
        imported_lines = book_db.save_line_human_finals(hf_rows)

        proof_list = [
            {
                "page_num": r["pageNum"], "para_seq": r["paraSeq"], "line_seq": r["seqInPara"],
                "line_id": r["lineId"], "original_text": r["originalText"],
                "corrected_text": r["correctedText"], "change_type": r["changeType"],
                "severity": r["severity"], "notes": r["notes"],
                "triggered_pattern": r["triggeredPattern"],
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
            # original_line_id 为 int 列；把层级键 (pageNum, paraSeq, seqInPara) 编码进单个 int，
            # 保留页/段/行上下文（I3），而非仅传 seqInPara 丢失上下文。
            # 编码上限：假设 pageNum<1000、paraSeq<100、seqInPara<1000（超界会碰撞，古籍页面罕见）。
            original_line_id = r["pageNum"] * 100000 + r["paraSeq"] * 1000 + r["seqInPara"]
            db_pg.archive_line_correction(
                book_id,
                original_line_id,
                original_text=r["originalText"] or "",
                corrected_text=r["correctedText"] or "",
                corrected_by="human",
                correction_stage="human",
            )
    except Exception as e:
        logger.warning("[adapter] Postgres 校对记录归档失败，跳过：%s", e)


def freeze_custom_db(db_path: Path) -> None:
    """冻结旧 custom.db：设只读权限(0440) + 写 .frozen 标记，落实「旧库冻结只读」。"""
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"待冻结库不存在：{p}")
    try:
        os.chmod(str(p), 0o440)
    except OSError:
        pass
    marker = Path(str(p) + ".frozen")
    marker.write_text("frozen", encoding="utf-8")


def export_markdown(book: BookResult, out_path: Optional[Path] = None) -> Optional[str]:
    lines = [
        f"# {book.title}",
        "",
        f"> 来源：{book.publisher}（{book.pub_year}） | 引擎：{book.engine_label} | "
        f"book_code：{book.book_code}",
        "",
    ]
    for p in book.pages:
        lines.append(f"## 第 {p.page_num} 页")
        lines.append("")
        for para in p.paragraphs:
            for ln in para.lines:
                text = ln.human_final or ln.final or ln.consensus or ""
                lines.append(text)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三大永久范式库（本批沉淀）")
    lines.append("")
    lines.append("### 药名 OCR 范式")
    for h in book.herb_patterns:
        lines.append(f"- {h.correct_name} ← {h.ocr_error_pattern}（{h.pattern_type}, {h.severity}）")
    lines.append("")
    lines.append("### 经络穴位 OCR 范式")
    for m in book.meridian_patterns:
        lines.append(f"- {m.correct_name} ← {m.ocr_error_pattern}（{m.entity_type}, {m.meridian_belonging}）")
    lines.append("")
    lines.append("### 语境范式")
    for c in book.context_patterns:
        lines.append(f"- {c.pattern_text}（{c.pattern_type}）")
    lines.append("")
    lines.append("## 术语")
    for t in book.terms:
        lines.append(f"- {t.term_name}（{t.sublib}）：{t.error_pattern} → {t.correct_form}")
    lines.append("")
    lines.append("## 方剂")
    for f in book.formulas:
        lines.append(f"### {f.formula_name}")
        for ing in f.ingredients:
            lines.append(f"- {ing.herb_name} {ing.dosage_value}{ing.unit or ''}（{ing.role_in_formula or ''}）")
        lines.append("")

    md = "\n".join(lines)
    if out_path:
        op = Path(out_path)
        os.makedirs(op.parent, exist_ok=True)
        op.write_text(md, encoding="utf-8")
        return str(op)
    return md
