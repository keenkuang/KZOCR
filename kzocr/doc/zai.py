"""zai 校对台数据库操作：写入 OCR 结果到可移植校对包（custom.db）。

提取自 ``kzocr/adapter/to_zai_prisma.py`` — 文档模块重构 v0.23.0。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import fitz

from .. import config
from ..engine.types import BookResult
from ..scheduler.cross_align import compute_vl_marks

logger = logging.getLogger(__name__)


# 自动建表：列名对齐 prisma/schema.prisma（写库所需子集）
_SCHEMA_DDL = [
    """CREATE TABLE IF NOT EXISTS Book (
        bookCode TEXT PRIMARY KEY, title TEXT, author TEXT, publisher TEXT,
        pubYear INTEGER, pubEra TEXT, bookType TEXT, source TEXT,
        pageCount INTEGER, lineCount INTEGER, cerValue REAL, lineAccuracy REAL,
        isMock INTEGER, source_pdf TEXT)""",
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
        extraCharAlert TEXT, charLevelJson TEXT,
        crop_img BLOB, charBoxes TEXT, vl_marks TEXT)""",
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
    """CREATE TABLE IF NOT EXISTS ExportMeta (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tool_version TEXT,
        exported_at TEXT DEFAULT (datetime('now')), book_code TEXT,
        source_hash TEXT, signature TEXT)""",
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


def _migrate_line(conn: sqlite3.Connection) -> None:
    """向前兼容旧结构 custom.db：补齐 Line/Book 新增列（烘焙裁图/字符框/来源 PDF）。

    用 ``PRAGMA table_info`` 探测，缺失则 ``ALTER TABLE ... ADD COLUMN``。
    在建表后、写数据前调用（使用 package 的 sqlite conn）。
    """
    line_cols = {r[1] for r in conn.execute("PRAGMA table_info(Line)").fetchall()}
    if "crop_img" not in line_cols:
        conn.execute("ALTER TABLE Line ADD COLUMN crop_img BLOB")
    if "charBoxes" not in line_cols:
        conn.execute("ALTER TABLE Line ADD COLUMN charBoxes TEXT DEFAULT ''")
    if "vl_marks" not in line_cols:
        conn.execute("ALTER TABLE Line ADD COLUMN vl_marks TEXT DEFAULT ''")
    book_cols = {r[1] for r in conn.execute("PRAGMA table_info(Book)").fetchall()}
    if "source_pdf" not in book_cols:
        conn.execute("ALTER TABLE Book ADD COLUMN source_pdf TEXT DEFAULT ''")


def _compute_source_hash(conn: sqlite3.Connection) -> str:
    """按统一契约对 Line 表逐行哈希：id 升序，行格式见打包规范。

    ``source_hash = sha256``，逐行 ``f"{id}|{pageNum}|{paraSeq}|{seqInPara}|"
    "{engineTexts}|{consensus}\\n"``。**不含 humanFinal**（校对员会修改，纳入会导致
    合法回导被误拒）；仅覆盖生产者担保的不可变源内容。
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id,pageNum,paraSeq,seqInPara,engineTexts,consensus "
        "FROM Line ORDER BY id"
    ).fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(
            f"{r['id']}|{r['pageNum']}|{r['paraSeq']}|{r['seqInPara']}|"
            f"{r['engineTexts']}|{r['consensus']}\n".encode("utf-8")
        )
    return h.hexdigest()


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
        logger.warning("[doc.zai] Postgres 元数据注册失败，跳过：%s", e)
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
        overwrite: 是否覆盖已冻结的旧包（需显式确认）。
    """
    # B4（v0.3 冻结）：桩/降级假数据(is_mock) 不得入校对台，阻断 publish
    if getattr(book, "is_mock", False):
        logger.error(
            "[doc.zai] ⚠ 阻断 publish：桩/降级假数据(is_mock=True)，"
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
            logger.error(
                "[doc.zai][DATA INTEGRITY] BookDB 系统 of record 落库失败，"
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
                f"[doc.zai] 目标校对包已冻结（{frozen_marker}），不可静默覆盖。"
                f"请导出到新路径，或显式传 overwrite=True 以解除冻结并重写。"
            )
        logger.warning("[doc.zai] 目标校对包已冻结，overwrite=True 解除只读并重写：%s", frozen_marker)
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
    # 来源校验（D）+ 烘焙裁图（A）+ 字符框（B）所需的 BookDB 连接与 PDF 文档句柄
    _bdb = None
    _doc = None
    _page_cache: dict[int, "object"] = {}
    try:
        cur = conn.cursor()
        for ddl in _SCHEMA_DDL:
            cur.execute(ddl)
        # 自动迁移：旧结构 custom.db 补齐 Line.crop_img/charBoxes、Book.source_pdf
        _migrate_line(conn)
        # 自动迁移：旧库的四张全局表可能无 bookCode 列，补列并清空一次全域
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
        logger.info("[doc.zai] 已清空旧数据（bookCode=%s），开始写入", book.book_code)

        total_lines = sum(len(para.lines) for p in book.pages for para in p.paragraphs)

        # Book（含 source_pdf：来源 PDF 路径，供离线原图回溯）
        cur.execute(
            "INSERT INTO Book (bookCode,title,author,publisher,pubYear,pubEra,bookType,"
            "source,pageCount,lineCount,cerValue,lineAccuracy,isMock,source_pdf) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (book.book_code, book.title, book.author, book.publisher,
             book.pub_year, book.pub_era, book.book_type, book.engine_label,
             len(book.pages), total_lines, None, None,
             int(bool(getattr(book, "is_mock", False))),
             str(pdf_path) if pdf_path else ""),
        )

        counts = {"pages": 0, "paragraphs": 0, "lines": 0, "proofreads": 0,
                  "patterns": 0, "terms": 0, "formulas": 0, "ingredients": 0}

        # 重导出闭环（W1）+ 字符级校正（B）：best-effort 打开一次 BookDB，
        # 既读人工终校(hf_map) 也供逐行读 char_boxes，写完后在 finally 关闭。
        hf_map: dict[tuple[int, int, int], str] = {}
        try:
            from kzocr.storage.db import BookDB
            if os.path.exists(str(bookdb_path)):
                _bdb = BookDB(book.book_code, db_dir=os.environ.get("KZOCR_DB_DIR", ""))
                hf_map = _bdb.get_human_final_map()
        except Exception:
            logger.warning("[doc.zai] 重导出读取 BookDB 人工终校失败，跳过合并", exc_info=True)

        # 原图回溯（A）开关：KZOCR_CROP_IMG=0 关闭（默认开）
        crop_enabled = os.environ.get("KZOCR_CROP_IMG", "1") != "0"

        # Page / Paragraph / Line / Proofread
        for p in book.pages:
            page_line_count = sum(len(para.lines) for para in p.paragraphs)
            cur.execute(
                "INSERT INTO Page (pageNum,bookCode,paragraphCount,lineCount) VALUES (?,?,?,?)",
                (p.page_num, book.book_code, len(p.paragraphs), page_line_count),
            )
            counts["pages"] += 1
            # 字符级 VL 标注（Part B）：按页查主库 cross_divergence，映射到逐行字符区间
            _page_divs: list = []
            if _bdb is not None:
                try:
                    _page_divs = _bdb.get_cross_divergences(page_no=p.page_num)
                except Exception:
                    logger.warning("[doc.zai] 读取 cross_divergence 失败（page=%s），vl_marks 置空", p.page_num)
                    _page_divs = []
            _page_lines = [ln for para in p.paragraphs for ln in para.lines]
            _page_marks = compute_vl_marks(_page_lines, _page_divs)
            _line_idx = 0
            for para_seq, para in enumerate(p.paragraphs, start=1):
                para_id = f"{book.book_code}-P{p.page_num}-{para_seq}"
                cur.execute(
                    "INSERT INTO Paragraph (id,pageNum,bookCode,seqInPage,isFormulaParagraph,"
                    "verificationStatus) VALUES (?,?,?,?,?,?)",
                    (para_id, p.page_num, book.book_code, para_seq, 0, None),
                )
                counts["paragraphs"] += 1
                for line_seq, ln in enumerate(para.lines, start=1):
                    line_id = f"{book.book_code}-P{p.page_num}-{para_seq}-{line_seq}"
                    et = ln.engine_texts or {}
                    engine_texts_json = json.dumps(et, ensure_ascii=False)
                    human_final = ln.human_final or hf_map.get(
                        (p.page_num, para_seq, line_seq), ""
                    )
                    # 字符级校正（B）：逐行 char_boxes 坐标（版心裁切后图像像素坐标）。
                    # 烘焙进 custom.db 时转为「相对裁图」坐标：以该行列所有字框包围盒
                    # 左上角 (x0,y0) 为原点，与 crop_img 子图严格对齐，前端方可直接叠加。
                    cbs = None
                    if _bdb is not None:
                        getter = getattr(_bdb, "get_line_char_boxes", None)
                        if getter is not None:
                            cbs = getter(p.page_num, para_seq, line_seq)
                    char_boxes_json = "[]"
                    crop_origin = None
                    if cbs:
                        x0 = min(b[0] for b in cbs)
                        y0 = min(b[1] for b in cbs)
                        crop_origin = (x0, y0)
                        char_boxes_json = json.dumps(
                            [[b[0] - x0, b[1] - y0, b[2] - x0, b[3] - y0] for b in cbs],
                            ensure_ascii=False,
                        )
                    # 原图回溯（A）：烘焙每行裁图 crop_img（默认开，KZOCR_CROP_IMG=0 关）
                    crop_img = None
                    if crop_enabled and pdf_path is not None and cbs:
                        try:
                            from PIL import Image as PILImage
                            from ..engine.run import _crop_to_body, _pdf_page_to_numpy
                            if _doc is None:
                                _doc = fitz.open(str(pdf_path))
                            pn = p.page_num
                            if pn not in _page_cache:
                                img = _pdf_page_to_numpy(_doc[pn - 1], dpi=150)
                                _page_cache[pn] = _crop_to_body(img, page_num=pn)
                            img = _page_cache[pn]
                            x0, y0 = crop_origin
                            x1 = max(b[2] for b in cbs)
                            y1 = max(b[3] for b in cbs)
                            crop = img[y0:y1, x0:x1]
                            buf = io.BytesIO()
                            PILImage.fromarray(crop).save(buf, "PNG")
                            crop_img = buf.getvalue()
                        except Exception:
                            logger.warning(
                                "[doc.zai] 烘焙裁图失败（line=%s），crop_img 置空",
                                line_id, exc_info=True,
                            )
                            crop_img = None
                    # 字符级 VL 标注（Part B）：本行分歧区间（含左不含右）
                    _marks = _page_marks.get(_line_idx, [])
                    vl_marks_json = json.dumps(_marks, ensure_ascii=False)
                    _line_idx += 1
                    cur.execute(
                        "INSERT INTO Line (id,pageNum,bookCode,paraSeq,seqInPara,"
                        "engineTexts,consensus,llmCorrected,glyphVerified,final,"
                        "humanFinal,confidence,auditSource,headingLevel,disputed,"
                        "missingCharAlert,extraCharAlert,charLevelJson,crop_img,charBoxes,vl_marks) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (line_id, p.page_num, book.book_code, para_seq,
                         line_seq,
                         engine_texts_json, ln.consensus, ln.llm_corrected,
                         ln.glyph_verified,
                         ln.final, human_final, ln.confidence, book.engine_label,
                         None, int(ln.disputed), ln.missing_char_alert,
                         ln.extra_char_alert, ln.char_level_json,
                         crop_img, char_boxes_json, vl_marks_json),
                    )
                    counts["lines"] += 1
                    for pr in ln.proofreads:
                        cur.execute(
                            "INSERT INTO Proofread (id,pageNum,bookCode,paraSeq,"
                            "seqInPara,lineId,originalText,correctedText,"
                            "changeType,severity,notes,triggeredPattern) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (_uid(), p.page_num, book.book_code, para_seq,
                             line_seq, line_id,
                             pr.original_text, pr.corrected_text, pr.change_type,
                             pr.severity, pr.notes, pr.triggered_pattern),
                        )
                        counts["proofreads"] += 1

        # Pattern（三大范式库）
        for h in book.herb_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,"
                "isToxic,severity,sourceBooks,evidenceCount,libType,entityType,"
                "meridianBelonging,bodyRegion,patternText,regex,example,lib) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), h.correct_name, h.ocr_error_pattern, h.pattern_type,
                 int(h.is_toxic), h.severity, h.source_books, h.evidence_count,
                 1, None, None, None, None, None, None, "herb"),
            )
            counts["patterns"] += 1
        for m in book.meridian_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,"
                "isToxic,severity,sourceBooks,evidenceCount,libType,entityType,"
                "meridianBelonging,bodyRegion,patternText,regex,example,lib) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), m.correct_name, m.ocr_error_pattern, None, None,
                 m.severity, m.source_books, m.evidence_count, 2, m.entity_type,
                 m.meridian_belonging, m.body_region, None, None, None, "meridian"),
            )
            counts["patterns"] += 1
        for c in book.context_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,"
                "isToxic,severity,sourceBooks,evidenceCount,libType,entityType,"
                "meridianBelonging,bodyRegion,patternText,regex,example,lib) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), None, None, c.pattern_type,
                 None, None, c.source_books, c.discovered_count, 3, None,
                 None, None, c.pattern_text, c.regex, c.example, "context"),
            )
            counts["patterns"] += 1

        # Term
        for t in book.terms:
            cur.execute(
                "INSERT INTO Term (id,termName,sublib,errorPattern,correctForm,"
                "scope,scopeScore,confidence,sourceBooks) VALUES (?,?,?,?,?,?,?,?,?)",
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
                    "INSERT INTO FormulaIngredient (id,formulaId,herbName,"
                    "herbCorrectedName,dosageValue,unit,roleInFormula,isToxic) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (_uid(), fid, ing.herb_name, None, ing.dosage_value,
                     ing.unit, ing.role_in_formula, int(ing.is_toxic)),
                )
                counts["ingredients"] += 1

        conn.commit()

        # 来源校验（D）：写完数据后写 ExportMeta（source_hash + signature）
        source_hash = _compute_source_hash(conn)
        key = os.environ.get("KZOCR_PACKAGE_KEY", "")
        if key:
            signature = hmac.new(key.encode(), source_hash.encode(),
                                 hashlib.sha256).hexdigest()
        else:
            signature = source_hash
        import kzocr
        cur.execute(
            "INSERT INTO ExportMeta (tool_version,book_code,source_hash,signature) "
            "VALUES (?,?,?,?)",
            (kzocr.__version__, book.book_code, source_hash, signature),
        )
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
        if _bdb is not None:
            _bdb.close()
        if _doc is not None:
            _doc.close()
