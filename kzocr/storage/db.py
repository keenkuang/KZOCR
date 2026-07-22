"""BookDB：单书 SQLite 存储，KZOCR 的**系统 of record**（书籍全量数据）。

承载：QA 进度状态机（page_progress + hierarchy_anomaly）、逐页内容（page：
整页文本/置信度/字符级 bbox）、层级内容（line：页-段-行-字，含人工终校 human_final）、
人工校对记录（proofread：从 custom.db 导入回写）、跨引擎分歧（cross_divergence）等。

定位（见 docs/plans/db-layering.md §5）：BookDB 是书籍全量数据的权威源；PostgreSQL 主库
只存元数据（BookRegistry + BookMeta + BookContentTree 快照），不存逐行 OCR 结果；
custom.db（zai 校对工作台）是从 BookResult 打出的可移植校对包，校对后由 import_proofread_package
导入回写本库。

每个 book_code 对应一个 SQLite 文件，默认路径 $KZOCR_DB_DIR/{book_code}.db
（KZOCR_DB_DIR 未设时回退 cwd/db）。
"""

from __future__ import annotations

import logging
import os
import json
import sqlite3
from typing import Any, Optional

from kzocr.engine.types import BookResult, GlyphVerdict

_logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS page_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_num        INTEGER NOT NULL UNIQUE,
    char_count      INTEGER DEFAULT 0,
    -- OCR 阶段
    ocr_status      TEXT DEFAULT 'pending' CHECK (ocr_status IN ('pending','processing','success','failed','skipped')),
    ocr_attempts    INTEGER DEFAULT 0,
    ocr_elapsed_ms  INTEGER DEFAULT 0,
    ocr_error       TEXT DEFAULT '',
    -- 字形验证阶段
    verify_status   TEXT DEFAULT 'PENDING' CHECK (verify_status IN ('PENDING','PASS','RARE','UNCERTAIN','FAIL','UNKNOWN','SKIPPED')),
    verify_details  TEXT DEFAULT '',
    -- 导入阶段（预留）
    import_status   TEXT DEFAULT 'pending' CHECK (import_status IN ('pending','imported','failed','skipped')),
    import_count    INTEGER DEFAULT 0,
    import_error    TEXT DEFAULT '',
    -- 元信息
    engine_label    TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hierarchy_anomaly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_num        INTEGER NOT NULL,
    verdict_status  TEXT NOT NULL,
    detector_chain  TEXT DEFAULT '',
    details         TEXT DEFAULT '',
    resolution      TEXT DEFAULT 'pending' CHECK (resolution IN ('pending','confirmed','fixed','wontfix')),
    note            TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code       TEXT NOT NULL,
    engine          TEXT NOT NULL DEFAULT '',
    total_pages     INTEGER DEFAULT 0,
    success_pages   INTEGER DEFAULT 0,
    fail_pages      INTEGER DEFAULT 0,
    error_rate      REAL DEFAULT 0.0,
    total_latency_ms INTEGER DEFAULT 0,
    latency_p50_ms  REAL DEFAULT 0.0,
    latency_p95_ms  REAL DEFAULT 0.0,
    pages_per_min   REAL DEFAULT 0.0,
    total_elapsed_s REAL DEFAULT 0.0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quality_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_no       TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    issues_json     TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);

-- 跨引擎分歧（借鉴 ocr_pipeline_v2：Tier1 文本 vs Tier3 文本 token 级模糊对齐）
CREATE TABLE IF NOT EXISTS cross_divergence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    page_no     INTEGER NOT NULL,
    div_type    TEXT    NOT NULL,
    a_seg       TEXT    NOT NULL DEFAULT '',
    b_seg       TEXT    NOT NULL DEFAULT '',
    a_context   TEXT    NOT NULL DEFAULT '',
    boxes       TEXT    NOT NULL DEFAULT '[]',
    priority    TEXT    NOT NULL DEFAULT 'normal',
    status      TEXT    NOT NULL DEFAULT 'pending',
    engine_a    TEXT    NOT NULL DEFAULT '',
    engine_b    TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now'))
);

-- ── 书籍全量内容表（系统 of record，按书分库）──
-- book：书级元数据（Postgres BookMeta 为权威；此处仅存内容所需书级键）
CREATE TABLE IF NOT EXISTS book (
    book_code   TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    author      TEXT DEFAULT '',
    publisher   TEXT DEFAULT '',
    pub_year    INTEGER DEFAULT 0,
    source_pdf  TEXT DEFAULT '',     -- B7: 来源 PDF 路径（按 char_boxes 切片落裁剪图）
    created_at  TEXT DEFAULT (datetime('now'))
);

-- page：整页文本 + 字符级 bbox（JSON: 每行 → 逐字 [x1,y1,x2,y2]）
CREATE TABLE IF NOT EXISTS page (
    page_num    INTEGER PRIMARY KEY,
    book_code   TEXT NOT NULL DEFAULT '',
    text        TEXT DEFAULT '',
    confidence  REAL DEFAULT 0.0,
    char_count  INTEGER DEFAULT 0,
    char_boxes  TEXT DEFAULT '[]',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- line：行级（页-段-行-字 层级）。para_seq=段序号(页内,1-based，落库按页内段落位置派生)，
-- line_seq=段内行序(1-based，落库按段内行位置派生)。二者均按位置派生，与导出/导入回路一致，
-- 不依赖 LineResult.sequence_in_paragraph / ParagraphResult.sequence_in_page 是否被引擎填充。
-- char_boxes：该行逐字 [x1,y1,x2,y2]；human_final：人工终校文本
CREATE TABLE IF NOT EXISTS line (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    page_num    INTEGER NOT NULL,
    para_seq    INTEGER NOT NULL DEFAULT 0,
    line_seq    INTEGER NOT NULL,
    text        TEXT DEFAULT '',
    char_boxes  TEXT DEFAULT '[]',
    human_final TEXT DEFAULT '',
    crop_img_path TEXT DEFAULT '',    -- B7: 行裁剪图相对路径（<db_dir>/<book_code>_crops/...png）
    UNIQUE (page_num, para_seq, line_seq)
);

-- proofread：人工校对记录（从 custom.db 导入回写）。
-- UNIQUE(line_id, corrected_text)：同一行同一改正重导入时不重复（导入回路幂等）。
-- book_code：按书隔离多回导版本（B.5）；imported_at/import_version：回导版本化审计。
CREATE TABLE IF NOT EXISTS proofread (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code       TEXT DEFAULT '',
    page_num        INTEGER NOT NULL,
    para_seq        INTEGER NOT NULL DEFAULT 0,
    line_seq        INTEGER NOT NULL DEFAULT 0,
    line_id         TEXT DEFAULT '',
    original_text   TEXT DEFAULT '',
    corrected_text  TEXT DEFAULT '',
    change_type     TEXT DEFAULT '',
    severity        TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    triggered_pattern TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    imported_at     TEXT DEFAULT '',
    import_version  INTEGER DEFAULT 0,
    UNIQUE (line_id, corrected_text)
);

-- e2e 跨引擎扩面记录（运营主库侧的「扩面元数据」归宿；此前只存 summary JSON）。
-- 按书分库：每书一本 e2e_expansion 历史，记录跑过的 pdf/书名/页数/分歧数/时间。
CREATE TABLE IF NOT EXISTS e2e_expansion (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code           TEXT NOT NULL,
    pdf                 TEXT NOT NULL DEFAULT '',
    book_title          TEXT NOT NULL DEFAULT '',
    pages_processed     INTEGER DEFAULT 0,
    pages_requested     INTEGER DEFAULT 0,
    total_divergences   INTEGER DEFAULT 0,
    high_divergences    INTEGER DEFAULT 0,
    engine_a            TEXT DEFAULT 'PaddleOCR',
    engine_b            TEXT DEFAULT 'RapidOCR',
    render_warnings_json TEXT DEFAULT '[]',
    run_at              TEXT DEFAULT (datetime('now')),
    batch               TEXT DEFAULT ''
);

-- 导入审计（B.2）：每次校对包回导落一行，记录来源/操作人/版本，供回溯。
CREATE TABLE IF NOT EXISTS import_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code    TEXT NOT NULL,
    imported_at  TEXT DEFAULT (datetime('now')),
    imported_by  TEXT DEFAULT '',
    package_hash TEXT DEFAULT '',
    lines        INTEGER DEFAULT 0,
    proofreads   INTEGER DEFAULT 0,
    import_version INTEGER DEFAULT 1
);

-- 字级规范化实体（两点修订 stage 2/3）：canonical_char 为「字」的唯一权威实体，
-- 坐标空间同 line.char_boxes（版心图 dpi=150 不缩放，原点版心图左上角）。
-- book_code 为按书分库冗余键，便于跨库聚合；层级键 (page_num, para_seq, line_seq, char_pos) 唯一。
CREATE TABLE IF NOT EXISTS canonical_char (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code        TEXT NOT NULL DEFAULT '',
    page_num         INTEGER NOT NULL,
    para_seq         INTEGER NOT NULL DEFAULT 0,
    line_seq         INTEGER NOT NULL,
    char_pos         INTEGER NOT NULL,
    char_text        TEXT NOT NULL DEFAULT '',
    bbox             TEXT NOT NULL DEFAULT '[]',     -- [x1,y1,x2,y2] 版心图 dpi=150 不缩放
    char_img_path    TEXT DEFAULT '',                -- 相对 <db_dir>/<book_code>_crops/ 的 PNG
    confidence       REAL NOT NULL DEFAULT 0.9,
    confidence_source TEXT NOT NULL DEFAULT 'default',  -- 'line'|'engine'|'default'
    final_engine     TEXT DEFAULT '',                -- 最终贡献引擎（初始 paddleocrv6）
    revised_by       TEXT NOT NULL DEFAULT 'none',   -- 'none'|'program'|'human'
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT,
    UNIQUE (page_num, para_seq, line_seq, char_pos)
);
CREATE INDEX IF NOT EXISTS idx_cc_book_page ON canonical_char(book_code, page_num);

-- 每引擎对该字的原始记录（stage 2：逐字对齐到 canonical 位置后挂载）。
CREATE TABLE IF NOT EXISTS engine_char_record (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_char_id INTEGER NOT NULL,
    engine            TEXT NOT NULL,
    char_text         TEXT NOT NULL DEFAULT '',
    confidence        REAL NOT NULL DEFAULT 0.9,
    confidence_source TEXT NOT NULL DEFAULT 'default',
    bbox              TEXT,                           -- 该引擎自身框（字数不一可能为空）
    is_final          INTEGER NOT NULL DEFAULT 0,     -- 是否最终贡献引擎（0/1）
    FOREIGN KEY (canonical_char_id) REFERENCES canonical_char(id) ON DELETE CASCADE,
    UNIQUE (canonical_char_id, engine)
);
CREATE INDEX IF NOT EXISTS idx_ecr_cc ON engine_char_record(canonical_char_id);

-- 错误识别记录（stage 3）：由 cross_divergence 派生，反哺识别率。
-- 注意：source_divergence_id 为软溯源（不强制 FK），避免 e2e 重跑 clear_cross_divergences
-- 时因外键约束阻断删除；溯源仅在写入时刻有效。
CREATE TABLE IF NOT EXISTS error_record (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    page_no             INTEGER NOT NULL,
    line_seq            INTEGER,
    char_pos            INTEGER,
    engine              TEXT NOT NULL,                -- 犯错引擎
    wrong_char          TEXT,
    correct_char        TEXT,                         -- 优先 human_final 否则 consensus
    source_divergence_id INTEGER,                     -- 软溯源：cross_divergence.id（非强制 FK）
    error_type          TEXT NOT NULL,                -- 'replace'|'delete'|'insert'
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'confirmed'|'rejected'
    confidence          REAL,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_er_engine ON error_record(engine);
CREATE INDEX IF NOT EXISTS idx_er_src ON error_record(source_divergence_id);
"""


class BookDB:
    """单书 SQLite 数据库管理器。"""

    def __init__(self, book_code: str, db_dir: str = "") -> None:
        self.book_code = book_code
        if not db_dir:
            db_dir = os.environ.get(
                "KZOCR_DB_DIR", os.path.join(os.getcwd(), "db")
            )
        os.makedirs(db_dir, exist_ok=True)
        self.db_dir = db_dir
        db_path = os.path.join(db_dir, f"{book_code}.db")
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.create_schema()

    def create_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        # 向后兼容迁移：旧 line 表无 para_seq/human_final 列 → 重建为层级结构
        # （Phase 1/2 未发布，本地 dev 库；旧行 para_seq 归 0，不丢数据）
        cur = self._conn.execute("PRAGMA table_info(line)")
        cols = [r[1] for r in cur.fetchall()]
        if "para_seq" not in cols:
            self._conn.execute("ALTER TABLE line RENAME TO _line_old")
            self._conn.execute(
                """CREATE TABLE line (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_num INTEGER NOT NULL,
                    para_seq INTEGER NOT NULL DEFAULT 0,
                    line_seq INTEGER NOT NULL,
                    text TEXT DEFAULT '',
                    char_boxes TEXT DEFAULT '[]',
                    human_final TEXT DEFAULT '',
                    crop_img_path TEXT DEFAULT '',
                    UNIQUE (page_num, para_seq, line_seq)
                )"""
            )
            # 旧行 para_seq 归 0；OR IGNORE 防止历史脏数据出现重复层级键时整库迁移失败
            self._conn.execute(
                "INSERT OR IGNORE INTO line "
                "(page_num, para_seq, line_seq, text, char_boxes, crop_img_path) "
                "SELECT page_num, 0, line_seq, text, char_boxes, "
                "COALESCE(crop_img_path, '') FROM _line_old"
            )
            self._conn.execute("DROP TABLE _line_old")
        # 迁移：line 补 crop_img_path 列（B7 裁剪图路径；旧库无此列 → ALTER 补列）
        if "crop_img_path" not in [r[1] for r in self._conn.execute("PRAGMA table_info(line)")]:
            self._conn.execute("ALTER TABLE line ADD COLUMN crop_img_path TEXT DEFAULT ''")
        # 迁移：book 补 source_pdf 列（B7 来源 PDF 路径）
        if "source_pdf" not in [r[1] for r in self._conn.execute("PRAGMA table_info(book)")]:
            self._conn.execute("ALTER TABLE book ADD COLUMN source_pdf TEXT DEFAULT ''")
        # proofread 唯一索引（防止同一行同一改正重导入重复；IF NOT EXISTS 对新建库无副作用）
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_proofread_line_correction "
            "ON proofread(line_id, corrected_text)"
        )
        # 迁移：proofread 表补 book_code / imported_at / import_version 列（B.5 多回导版本化）。
        # 旧库 proofread 无这些列 → ALTER 补列；新建库已在 _SCHEMA_SQL 含这些列，跳过。
        pcur = self._conn.execute("PRAGMA table_info(proofread)")
        pcols = [r[1] for r in pcur.fetchall()]
        if "book_code" not in pcols:
            self._conn.execute("ALTER TABLE proofread ADD COLUMN book_code TEXT DEFAULT ''")
        if "imported_at" not in pcols:
            self._conn.execute("ALTER TABLE proofread ADD COLUMN imported_at TEXT DEFAULT ''")
        if "import_version" not in pcols:
            self._conn.execute("ALTER TABLE proofread ADD COLUMN import_version INTEGER DEFAULT 0")
        self._conn.commit()

    # ── page_progress ──

    def init_page(
        self,
        page_num: int,
        char_count: int = 0,
        engine_label: str = "",
    ) -> None:
        """插入或忽略（UPSERT 幂等）。"""
        self._conn.execute(
            """INSERT OR IGNORE INTO page_progress
               (page_num, char_count, engine_label)
               VALUES (?, ?, ?)""",
            (page_num, char_count, engine_label),
        )
        self._conn.commit()

    def update_ocr(
        self,
        page_num: int,
        *,
        status: str,
        char_count: int = 0,
        error: str = "",
        latency_ms: int = 0,
        attempts: int = 1,
    ) -> None:
        self._conn.execute(
            """UPDATE page_progress SET
               ocr_status=?, char_count=?, ocr_error=?,
               ocr_elapsed_ms=?, ocr_attempts=?,
               updated_at=datetime('now')
               WHERE page_num=?""",
            (status, char_count, error, latency_ms, attempts, page_num),
        )
        self._conn.commit()

    def update_verify(
        self,
        page_num: int,
        *,
        verdict: str,
        details: str = "",
    ) -> None:
        self._conn.execute(
            """UPDATE page_progress SET
               verify_status=?, verify_details=?,
               updated_at=datetime('now')
               WHERE page_num=?""",
            (verdict, details, page_num),
        )
        self._conn.commit()

    def update_import(
        self,
        page_num: int,
        *,
        status: str,
        count: int = 0,
        error: str = "",
    ) -> None:
        self._conn.execute(
            """UPDATE page_progress SET
               import_status=?, import_count=?, import_error=?,
               updated_at=datetime('now')
               WHERE page_num=?""",
            (status, count, error, page_num),
        )
        self._conn.commit()

    def get_page_progress(self, page_num: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM page_progress WHERE page_num=?", (page_num,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_progress(
        self, *, status_filter: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if status_filter:
            rows = self._conn.execute(
                "SELECT * FROM page_progress WHERE ocr_status=? ORDER BY page_num",
                (status_filter,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM page_progress ORDER BY page_num"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 书籍内容（系统 of record）──

    def save_book(
        self,
        book_code: str,
        *,
        title: str = "",
        author: str = "",
        publisher: str = "",
        pub_year: int = 0,
        source_pdf: str = "",
    ) -> None:
        """UPSERT 书级元数据（Postgres BookMeta 为权威，此处仅存内容所需书级键）。

        source_pdf：B7 来源 PDF 路径，供按 char_boxes 切片落裁剪图。
        """
        self._conn.execute(
            """INSERT INTO book (book_code, title, author, publisher, pub_year, source_pdf)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(book_code) DO UPDATE SET
                 title=excluded.title, author=excluded.author,
                 publisher=excluded.publisher, pub_year=excluded.pub_year,
                 source_pdf=excluded.source_pdf""",
            (book_code, title, author, publisher, pub_year, source_pdf),
        )
        self._conn.commit()

    def save_page(
        self,
        book_code: str,
        page_num: int,
        *,
        text: str = "",
        confidence: float = 0.0,
        char_boxes: Optional[list[list[list[int]]]] = None,
    ) -> None:
        """UPSERT 整页内容（含字符级 bbox）。行级展开交由 save_book_result 处理（层级结构）。"""
        cb_json = json.dumps(char_boxes or [], ensure_ascii=False)
        self._conn.execute(
            """INSERT INTO page (page_num, book_code, text, confidence, char_count, char_boxes)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_num) DO UPDATE SET
                 book_code=excluded.book_code, text=excluded.text,
                 confidence=excluded.confidence, char_count=excluded.char_count,
                 char_boxes=excluded.char_boxes""",
            (page_num, book_code, text, confidence, len(text), cb_json),
        )
        self._conn.commit()

    def _save_line(
        self,
        page_num: int,
        para_seq: int,
        line_seq: int,
        *,
        text: str = "",
        char_boxes: Optional[list[list[int]]] = None,
        crop_img_path: str = "",
    ) -> None:
        """UPSERT 单行（层级键 page_num+para_seq+line_seq）。crop_img_path：B7 裁剪图相对路径。"""
        self._conn.execute(
            """INSERT INTO line (page_num, para_seq, line_seq, text, char_boxes, crop_img_path)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_num, para_seq, line_seq) DO UPDATE SET
                 text=excluded.text, char_boxes=excluded.char_boxes,
                 crop_img_path=excluded.crop_img_path""",
            (page_num, para_seq, line_seq, text,
             json.dumps(char_boxes or [], ensure_ascii=False), crop_img_path),
        )
        self._conn.commit()

    def save_book_result(self, book: BookResult) -> None:
        """把整本 BookResult 落库（book + 每页 text/confidence/char_boxes + 层级行展开）。

        若 ``book.source_pdf`` 存在，逐行按 char_boxes 包围盒切出裁剪图落盘，
        并把相对路径写入 ``line.crop_img_path``（B7）。受 ``KZOCR_CROP_IMG`` 开关控制
        （默认开；``=0`` 关闭则不落图，crop_img_path 留空）。best-effort：切图失败仅告警。
        """
        from kzocr.storage.crop_images import crop_line_to_png, close_doc_cache

        self.save_book(
            book.book_code, title=book.title, author=book.author,
            publisher=book.publisher, pub_year=book.pub_year,
            source_pdf=book.source_pdf,
        )
        crop_enabled = os.environ.get("KZOCR_CROP_IMG", "1") != "0"
        source_pdf = book.source_pdf
        doc_cache: dict = {}
        img_cache: dict = {}
        try:
            for p in book.pages:
                self.save_page(
                    book.book_code, p.page_num, text=p.text,
                    confidence=p.confidence, char_boxes=p.char_boxes,
                )
                # 层级展开行（页-段-行-字）：段序号/行序均按位置派生（1-based），
                # 与 push_book_to_zai 导出回路同源，不依赖引擎是否填充 sequence_in_* 字段，
                # 确保 导出→导入 闭环 key 自洽（C1）。
                flat = 0
                if p.paragraphs:
                    for para_seq, para in enumerate(p.paragraphs, start=1):
                        for line_seq, ln in enumerate(para.lines, start=1):
                            cb = p.char_boxes[flat] if (p.char_boxes and flat < len(p.char_boxes)) else None
                            crop_path = ""
                            if crop_enabled and source_pdf and cb:
                                crop_path = crop_line_to_png(
                                    source_pdf, p.page_num, cb, para_seq, line_seq,
                                    book.book_code, self.db_dir,
                                    doc_cache=doc_cache, img_cache=img_cache,
                                ) or ""
                            self._save_line(
                                p.page_num, para_seq, line_seq,
                                text=ln.final or ln.consensus or "",
                                char_boxes=cb, crop_img_path=crop_path,
                            )
                            flat += 1
                elif p.char_boxes:
                    for j, cb in enumerate(p.char_boxes):
                        crop_path = ""
                        if crop_enabled and source_pdf and cb:
                            crop_path = crop_line_to_png(
                                source_pdf, p.page_num, cb, 0, j,
                                book.book_code, self.db_dir,
                                doc_cache=doc_cache, img_cache=img_cache,
                            ) or ""
                        self._save_line(
                            p.page_num, 0, j, text="", char_boxes=cb,
                            crop_img_path=crop_path,
                        )
        finally:
            if doc_cache:
                close_doc_cache(doc_cache)

    def get_page(self, page_num: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM page WHERE page_num=?", (page_num,)
        ).fetchone()
        return dict(row) if row else None

    def get_page_char_boxes(self, page_num: int) -> Optional[list[list[list[int]]]]:
        """读取某页字符级 bbox（解析 JSON）。无则返回 None。"""
        row = self._conn.execute(
            "SELECT char_boxes FROM page WHERE page_num=?", (page_num,)
        ).fetchone()
        if not row:
            return None
        raw = row["char_boxes"]
        if not raw:
            return None
        data = json.loads(raw)
        return data if data else None

    def get_page_lines(self, page_num: int) -> list[dict[str, Any]]:
        """读取某页所有行（含 para_seq, line_seq, text, char_boxes, human_final）。"""
        rows = self._conn.execute(
            "SELECT * FROM line WHERE page_num=? ORDER BY para_seq, line_seq",
            (page_num,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_book_pages(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT page_num, char_count, confidence FROM page ORDER BY page_num"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_source_pdf(self) -> str:
        """读取本书来源 PDF 路径（B7 切片用）；无则返回空串。"""
        row = self._conn.execute(
            "SELECT source_pdf FROM book WHERE book_code=?", (self.book_code,)
        ).fetchone()
        return dict(row)["source_pdf"] if row else ""

    # ── 人工终校 / 校对记录（校对包导入回写）──

    def save_line_human_final(
        self, page_num: int, para_seq: int, line_seq: int, human_final: str
    ) -> None:
        """把人工终校文本写回指定行（层级键）。行不存在时仅告警跳过。"""
        cur = self._conn.execute(
            "UPDATE line SET human_final=? WHERE page_num=? AND para_seq=? AND line_seq=?",
            (human_final, page_num, para_seq, line_seq),
        )
        if cur.rowcount == 0:
            logger = _logger
            logger.warning(
                "[BookDB] save_line_human_final 未命中行 (page=%s, para=%s, line=%s)，跳过",
                page_num, para_seq, line_seq,
            )
        self._conn.commit()

    def save_proofreads(self, rows: list[dict], *, commit: bool = True) -> int:
        """批量写入人工校对记录（从 custom.db 导入）。返回实际写入条数。

        使用 INSERT OR IGNORE + UNIQUE(line_id, corrected_text)，同一行同一改正重导入时
        幂等（不重复，W3）。写入 imported_at（datetime('now')）与 import_version（取
        proof dict 的 import_version，默认 0，由 save_import_batch 统一注入，B.5）。

        commit=False 时仅执行语句、不提交（供 save_import_batch 同一事务使用）。
        """
        n = 0
        for r in rows:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO proofread
                   (page_num, para_seq, line_seq, line_id, original_text,
                    corrected_text, change_type, severity, notes, triggered_pattern,
                    book_code, imported_at, import_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
                (
                    r.get("page_num") or 0, r.get("para_seq") or 0, r.get("line_seq") or 0,
                    r.get("line_id", ""), r.get("original_text", ""),
                    r.get("corrected_text", ""), r.get("change_type", ""),
                    r.get("severity", ""), r.get("notes", ""),
                    r.get("triggered_pattern", ""),
                    r.get("book_code", ""),
                    r.get("import_version", 0),
                ),
            )
            if cur.rowcount:
                n += 1
        if commit:
            self._conn.commit()
        return n

    def get_line_human_final(
        self, page_num: int, para_seq: int, line_seq: int
    ) -> Optional[str]:
        row = self._conn.execute(
            "SELECT human_final FROM line WHERE page_num=? AND para_seq=? AND line_seq=?",
            (page_num, para_seq, line_seq),
        ).fetchone()
        return dict(row)["human_final"] if row else None

    def get_human_final_map(self) -> dict[tuple[int, int, int], str]:
        """读回全库人工终校，键为层级键 (page_num, para_seq, line_seq)。

        供 push_book_to_zai 重导出时合并已导入的人工终校（§5 闭环：BookDB 为权威源）。
        """
        rows = self._conn.execute(
            "SELECT page_num, para_seq, line_seq, human_final FROM line "
            "WHERE human_final IS NOT NULL AND human_final <> ''"
        ).fetchall()
        return {
            (r["page_num"], r["para_seq"], r["line_seq"]): r["human_final"]
            for r in rows
        }

    def save_line_human_finals(
        self, rows: list[tuple[int, int, int, str]], *, commit: bool = True
    ) -> int:
        """批量写回人工终校（导入回路用），单次提交。返回命中写入条数。

        rows: [(page_num, para_seq, line_seq, human_final), ...]
        commit=False 时仅执行语句、不提交（供 save_import_batch 同一事务使用）。
        """
        n = 0
        for page_num, para_seq, line_seq, human_final in rows:
            cur = self._conn.execute(
                "UPDATE line SET human_final=? WHERE page_num=? AND para_seq=? AND line_seq=?",
                (human_final, page_num, para_seq, line_seq),
            )
            if cur.rowcount:
                n += 1
        if commit:
            self._conn.commit()
        return n

    def get_line_char_boxes(
        self, page_num: int, para_seq: int, line_seq: int
    ) -> Optional[list[list[int]]]:
        """读取某行的字符级 bbox（解析 JSON）。无此行则返回 None。"""
        row = self._conn.execute(
            "SELECT char_boxes FROM line WHERE page_num=? AND para_seq=? AND line_seq=?",
            (page_num, para_seq, line_seq),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["char_boxes"] or "[]")

    def save_import_batch(
        self, hf_rows, proof_list, *, import_version: int = 0
    ) -> tuple[int, int]:
        """在同一事务内写回人工终校 + 校对记录（B.3 事务幂等）。

        调用 save_line_human_finals / save_proofreads（均 commit=False），全部成功后
        本次提交，返回 (n_lines, n_proofs)；任一异常则回滚并 re-raise（无 partial 写入）。
        proof_list 中每条 dict 可能含 import_version（忽略，由本方法统一设为 import_version）。
        """
        try:
            for r in proof_list:
                r["import_version"] = import_version
            n_lines = self.save_line_human_finals(hf_rows, commit=False)
            n_proofs = self.save_proofreads(proof_list, commit=False)
            self._conn.commit()
            return (n_lines, n_proofs)
        except Exception:
            self._conn.rollback()
            raise

    def record_import_audit(
        self, book_code, imported_by, package_hash, lines, proofreads, import_version
    ) -> int:
        """向 import_audit 表 INSERT 一行（B.2 审计），返回新行 id。"""
        cur = self._conn.execute(
            """INSERT INTO import_audit
               (book_code, imported_by, package_hash, lines, proofreads, import_version)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (book_code, imported_by, package_hash, lines, proofreads, import_version),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_proofreads(self, page_num: Optional[int] = None) -> list[dict[str, Any]]:
        if page_num is not None:
            rows = self._conn.execute(
                "SELECT * FROM proofread WHERE page_num=? ORDER BY id",
                (page_num,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM proofread ORDER BY id").fetchall()
        return [dict(r) for r in rows]


    @staticmethod
    def persist_book_result(book: BookResult, db_dir: str = "") -> None:
        """便捷函数：把整本 BookResult 落库到按书分库的 BookDB（系统 of record）。

        主要用于 run_engine 产出的 BookResult（含字符级 bbox）对接 BookDB。
        作为静态方法，避免破坏类体缩进（见本文件历史）。
        """
        db = BookDB(book.book_code, db_dir=db_dir)
        try:
            db.save_book_result(book)
        finally:
            db.close()

    # ── hierarchy_anomaly ──

    def record_anomaly(
        self,
        page_num: int,
        verdict: GlyphVerdict,
        detector_chain: Optional[list[str]] = None,
    ) -> None:
        """记录 E3 验证异常。"""
        self._conn.execute(
            """INSERT INTO hierarchy_anomaly
               (page_num, verdict_status, detector_chain, details)
               VALUES (?, ?, ?, ?)""",
            (
                page_num,
                verdict.status,
                ",".join(detector_chain) if detector_chain else "",
                verdict.details or "",
            ),
        )
        self._conn.commit()

    # ── cross_divergence（借鉴 ocr_pipeline_v2）──

    def write_cross_divergences(
        self,
        page_no: int,
        divs: list,
        engine_a: str = "",
        engine_b: str = "",
    ) -> int:
        """写入跨引擎分歧（kzocr.scheduler.cross_align.Divergence 列表）。返回写入行数。"""
        rows = 0
        for d in divs:
            self._conn.execute(
                """INSERT INTO cross_divergence
                   (page_no, div_type, a_seg, b_seg, a_context, boxes,
                    priority, status, engine_a, engine_b)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    d.page_no or page_no,
                    d.div_type,
                    d.a_seg,
                    d.b_seg,
                    d.a_context,
                    json.dumps(d.boxes, ensure_ascii=False),
                    d.priority,
                    d.status,
                    d.engine_a or engine_a,
                    d.engine_b or engine_b,
                ),
            )
            rows += 1
        self._conn.commit()
        return rows

    def clear_cross_divergences(self, page_nos: list[int]) -> int:
        """删除指定页号的跨引擎分歧（重新落库前幂等覆盖用）。

        返回删除行数。page_nos 为空时直接返回 0（不入 SQL 防注入占位符退化）。
        e2e 扩面（scripts/e2e_expand_books.py）每次落库前按页号清旧再重写，
        保证 cross_divergence 反映最新一次结果而不产生重复行。
        """
        if not page_nos:
            return 0
        placeholders = ",".join("?" for _ in page_nos)
        cur = self._conn.execute(
            f"DELETE FROM cross_divergence WHERE page_no IN ({placeholders})",
            list(page_nos),
        )
        self._conn.commit()
        return cur.rowcount

    def get_cross_divergences(
        self,
        page_no: int | None = None,
        priority: str | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """读取跨引擎分歧（可选按页号/优先级过滤），按 id 升序。

        priority 可为单值（精确匹配），也可为序列（IN 匹配，用于「高优先」分组
        P0/P1/high）。None 表示不过滤。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if page_no is not None:
            clauses.append("page_no=?")
            params.append(page_no)
        if priority is not None:
            if isinstance(priority, (list, tuple)):
                placeholders = ",".join("?" for _ in priority)
                clauses.append(f"priority IN ({placeholders})")
                params.extend(priority)
            else:
                clauses.append("priority=?")
                params.append(priority)
        sql = "SELECT * FROM cross_divergence"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_cross_divergence_status(
        self,
        page_no: int,
        div_type: str,
        a_seg: str,
        b_seg: str,
        status: str,
    ) -> int:
        """更新某分歧点状态（视觉仲裁后持久化裁决：accepted_a/accepted_b/both_wrong/manual 等）。

        以 (page_no, div_type, a_seg, b_seg) 定位（同页同类型同片段歧视为同一处）；
        返回更新的行数（同页多引擎比对写多条时按片段匹配，通常 1）。
        """
        cur = self._conn.execute(
            """UPDATE cross_divergence
               SET status=?
               WHERE page_no=? AND div_type=? AND a_seg=? AND b_seg=?""",
            (status, page_no, div_type, a_seg, b_seg),
        )
        self._conn.commit()
        return cur.rowcount

    # ── canonical_char / engine_char_record / error_record（两点修订 stage 2/3）──

    def save_canonical_chars(
        self, book_code: str, chars: list
    ) -> int:
        """写入字级规范化实体（UPSERT 按层级键，级联写每引擎原始记录）。

        返回写入的 canonical_char 行数。``chars`` 为 ``CanonicalChar`` 列表。
        """
        n = 0
        for c in chars:
            self._conn.execute(
                """INSERT INTO canonical_char
                   (book_code, page_num, para_seq, line_seq, char_pos, char_text,
                    bbox, char_img_path, confidence, confidence_source,
                    final_engine, revised_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(page_num, para_seq, line_seq, char_pos) DO UPDATE SET
                     book_code=excluded.book_code,
                     char_text=excluded.char_text,
                     bbox=excluded.bbox,
                     char_img_path=excluded.char_img_path,
                     confidence=excluded.confidence,
                     confidence_source=excluded.confidence_source,
                     final_engine=excluded.final_engine,
                     revised_by=excluded.revised_by,
                     updated_at=datetime('now')""",
                (
                    book_code, c.page_num, c.para_seq, c.line_seq, c.char_pos,
                    c.char_text,
                    json.dumps(c.bbox or [], ensure_ascii=False),
                    c.char_img_path or "",
                    c.confidence, c.confidence_source,
                    c.final_engine or "", c.revised_by,
                ),
            )
            cc_id = self._conn.execute(
                "SELECT id FROM canonical_char WHERE page_num=? AND para_seq=?"
                " AND line_seq=? AND char_pos=?",
                (c.page_num, c.para_seq, c.line_seq, c.char_pos),
            ).fetchone()["id"]
            self._conn.execute(
                "DELETE FROM engine_char_record WHERE canonical_char_id=?", (cc_id,)
            )
            for r in c.engine_records:
                self._conn.execute(
                    """INSERT INTO engine_char_record
                       (canonical_char_id, engine, char_text, confidence,
                        confidence_source, bbox, is_final)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cc_id, r.engine, r.char_text, r.confidence,
                        r.confidence_source,
                        json.dumps(r.bbox, ensure_ascii=False) if r.bbox else None,
                        1 if r.is_final else 0,
                    ),
                )
            n += 1
        self._conn.commit()
        return n

    def get_canonical_chars(self, page_no: int) -> list:
        """读取某页全部字级规范化实体（含每引擎原始记录），返回 ``CanonicalChar`` 列表。"""
        from kzocr.scheduler.canonical import CanonicalChar, EngineCharRecord

        rows = self._conn.execute(
            "SELECT * FROM canonical_char WHERE page_num=? "
            "ORDER BY para_seq, line_seq, char_pos",
            (page_no,),
        ).fetchall()
        out: list = []
        for r in rows:
            recs = self._conn.execute(
                "SELECT * FROM engine_char_record WHERE canonical_char_id=? "
                "ORDER BY id",
                (r["id"],),
            ).fetchall()
            engine_records = [
                EngineCharRecord(
                    engine=er["engine"],
                    char_text=er["char_text"],
                    confidence=er["confidence"],
                    confidence_source=er["confidence_source"],
                    bbox=json.loads(er["bbox"]) if er["bbox"] else None,
                    is_final=bool(er["is_final"]),
                )
                for er in recs
            ]
            out.append(
                CanonicalChar(
                    page_num=r["page_num"], para_seq=r["para_seq"],
                    line_seq=r["line_seq"], char_pos=r["char_pos"],
                    char_text=r["char_text"],
                    bbox=json.loads(r["bbox"]) if r["bbox"] else [],
                    char_img_path=r["char_img_path"] or None,
                    confidence=r["confidence"],
                    confidence_source=r["confidence_source"],
                    final_engine=r["final_engine"] or None,
                    revised_by=r["revised_by"],
                    engine_records=engine_records,
                )
            )
        return out

    def save_error_records(self, recs: list) -> int:
        """批量写入错误识别记录（``ErrorRecord`` 列表）。返回写入行数。"""
        n = 0
        for r in recs:
            self._conn.execute(
                """INSERT INTO error_record
                   (page_no, line_seq, char_pos, engine, wrong_char, correct_char,
                    source_divergence_id, error_type, status, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r.page_no, r.line_seq, r.char_pos, r.engine,
                    r.wrong_char, r.correct_char, r.source_divergence_id,
                    r.error_type, r.status, r.confidence,
                ),
            )
            n += 1
        self._conn.commit()
        return n

    def get_error_records(
        self,
        page_no: int | None = None,
        engine: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取错误识别记录（可选按页号/引擎/状态过滤），按 id 升序。"""
        clauses: list[str] = []
        params: list[Any] = []
        if page_no is not None:
            clauses.append("page_no=?")
            params.append(page_no)
        if engine is not None:
            clauses.append("engine=?")
            params.append(engine)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        sql = "SELECT * FROM error_record"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_error_stats(self, top_n: int = 20) -> dict[str, Any]:
        """聚合识别率统计（反哺接口报告部分）。

        返回：每引擎错误率（errors / 该引擎 authored 字数）、混淆对 Top-N
        （wrong→correct 频次降序）、总错误数。
        """
        engine_rows = self._conn.execute(
            "SELECT engine, COUNT(*) AS errors FROM error_record GROUP BY engine"
        ).fetchall()
        char_rows = self._conn.execute(
            "SELECT engine, COUNT(*) AS chars FROM engine_char_record GROUP BY engine"
        ).fetchall()
        char_totals = {r["engine"]: r["chars"] for r in char_rows}
        engine_error_rates: dict[str, Any] = {}
        for r in engine_rows:
            eng = r["engine"]
            chars = char_totals.get(eng, 0)
            engine_error_rates[eng] = {
                "errors": r["errors"],
                "chars": chars,
                "error_rate": round(r["errors"] / max(chars, 1), 4),
            }
        confusion = self._conn.execute(
            "SELECT wrong_char, correct_char, COUNT(*) AS cnt FROM error_record "
            "WHERE wrong_char IS NOT NULL AND correct_char IS NOT NULL "
            "GROUP BY wrong_char, correct_char ORDER BY cnt DESC LIMIT ?",
            (top_n,),
        ).fetchall()
        return {
            "engine_error_rates": engine_error_rates,
            "confusion_top": [
                {"wrong": c["wrong_char"], "correct": c["correct_char"], "count": c["cnt"]}
                for c in confusion
            ],
            "total_errors": sum(r["errors"] for r in engine_rows),
        }

    def export_error_pairs(self, out_path: str) -> int:
        """导出错误识别记录为 JSONL 训练样本（stage 3 反哺接口）。

        每行：``{"wrong", "correct", "context", "engine", "page_no", "line_seq",
        "char_pos", "status"}``。``context`` 取该字位 canonical_char.char_text（best-effort）。
        返回写出行数。
        """
        rows = self._conn.execute(
            "SELECT * FROM error_record ORDER BY id"
        ).fetchall()
        n = 0
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                for r in rows:
                    ctx = ""
                    if r["page_no"] is not None and r["line_seq"] is not None and r["char_pos"] is not None:
                        c = self._conn.execute(
                            "SELECT char_text FROM canonical_char WHERE page_num=? "
                            "AND line_seq=? AND char_pos=? LIMIT 1",
                            (r["page_no"], r["line_seq"], r["char_pos"]),
                        ).fetchone()
                        if c:
                            ctx = c["char_text"]
                    fh.write(json.dumps({
                        "wrong": r["wrong_char"],
                        "correct": r["correct_char"],
                        "context": ctx,
                        "engine": r["engine"],
                        "page_no": r["page_no"],
                        "line_seq": r["line_seq"],
                        "char_pos": r["char_pos"],
                        "status": r["status"],
                    }, ensure_ascii=False) + "\n")
                    n += 1
        except OSError as exc:
            _logger.warning("[BookDB] export_error_pairs 写文件失败 %s: %s", out_path, exc)
            raise
        return n

    def get_anomalies(
        self, *, status_filter: str = "pending"
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM hierarchy_anomaly WHERE resolution=? ORDER BY page_num",
            (status_filter,),
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_error_records(self, page_nos: list[int]) -> int:
        """删除指定页号的错误识别记录（重新落库前幂等覆盖用，同 clear_cross_divergences）。

        返回删除行数。page_nos 为空时直接返回 0。
        """
        if not page_nos:
            return 0
        placeholders = ",".join("?" for _ in page_nos)
        cur = self._conn.execute(
            f"DELETE FROM error_record WHERE page_no IN ({placeholders})",
            list(page_nos),
        )
        self._conn.commit()
        return cur.rowcount

    def get_unresolved_anomalies(
        self, book_code: str = "", *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """获取待处理异常（resolution='pending'），可联表 page_progress 获取上下文。"""
        rows = self._conn.execute(
            """SELECT a.*, p.char_count, p.engine_label
               FROM hierarchy_anomaly a
               LEFT JOIN page_progress p ON a.page_num = p.page_num
               WHERE a.resolution = 'pending'
               ORDER BY a.page_num
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_anomaly(
        self, anomaly_id: int, resolution: str, note: str = ""
    ) -> None:
        """标记异常决议。resolution: confirmed / fixed / wontfix。"""
        self._conn.execute(
            """UPDATE hierarchy_anomaly SET
               resolution=?, note=?, updated_at=datetime('now')
               WHERE id=?""",
            (resolution, note, anomaly_id),
        )
        self._conn.commit()

    # ── benchmark ──

    def write_benchmark(
        self,
        book_code: str,
        engine: str,
        total_pages: int,
        success_pages: int,
        fail_pages: int,
        total_latency_ms: int,
        total_elapsed_s: float,
    ) -> None:
        """写入单引擎的 benchmark 汇总记录。"""
        error_rate = fail_pages / max(total_pages, 1)
        pages_per_min = total_pages / max(total_elapsed_s / 60, 0.001)
        latency_p50 = self._conn.execute(
            "SELECT coalesce(avg(ocr_elapsed_ms),0) FROM page_progress WHERE ocr_status='success'"
        ).fetchone()[0]
        self._conn.execute(
            """INSERT INTO benchmark_results
               (book_code, engine, total_pages, success_pages, fail_pages,
                error_rate, total_latency_ms, latency_p50_ms, latency_p95_ms,
                pages_per_min, total_elapsed_s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                book_code, engine, total_pages, success_pages, fail_pages,
                round(error_rate, 4), total_latency_ms,
                round(latency_p50, 0), round(latency_p50, 0),
                round(pages_per_min, 2), round(total_elapsed_s, 1),
            ),
        )
        self._conn.commit()

    # ── 辅助 ──

    def close(self) -> None:
        self._conn.close()

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")
        self._conn.commit()

    # ── quality_result ──

    def save_quality_result(
        self, recipe_no: str, status: str, confidence: float = 1.0,
        issues_json: str = "[]",
    ) -> None:
        """写入单条质检结果。UPSERT (recipe_no)。"""
        self._conn.execute(
            """INSERT OR REPLACE INTO quality_result
               (recipe_no, status, confidence, issues_json)
               VALUES (?, ?, ?, ?)""",
            (recipe_no, status, confidence, issues_json),
        )
        self._conn.commit()

    # ── e2e 跨引擎扩面记录（运营主库侧的扩面元数据归宿）──

    def save_e2e_expansion(
        self,
        *,
        book_code: str,
        pdf: str,
        book_title: str,
        pages_processed: int,
        pages_requested: int = 0,
        total_divergences: int = 0,
        high_divergences: int = 0,
        engine_a: str = "PaddleOCR",
        engine_b: str = "RapidOCR",
        render_warnings: Optional[list[int]] = None,
        batch: str = "",
    ) -> int:
        """写入一条 e2e 跨引擎扩面记录（保留历史，自增 id）。返回新行 id。

        落库动机：e2e 扩面此前只写 summary JSON，运营主库完全不知哪些书被扩过面。
        此处按书分库落 BookDB（系统 of record），使「跑过什么书/多少页/什么文件名/
        何时」可经主库查询。
        """
        cur = self._conn.execute(
            """INSERT INTO e2e_expansion
               (book_code, pdf, book_title, pages_processed, pages_requested,
                total_divergences, high_divergences, engine_a, engine_b,
                render_warnings_json, batch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                book_code, pdf, book_title, pages_processed, pages_requested,
                total_divergences, high_divergences, engine_a, engine_b,
                json.dumps(render_warnings or [], ensure_ascii=False), batch,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_e2e_expansions(self, book_code: str) -> list[dict[str, Any]]:
        """读取某书的全部 e2e 扩面记录（按 run_at 升序）。"""
        rows = self._conn.execute(
            "SELECT * FROM e2e_expansion WHERE book_code=? ORDER BY run_at",
            (book_code,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_quality_results(
        self, *, status_filter: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if status_filter:
            rows = self._conn.execute(
                "SELECT * FROM quality_result WHERE status=? ORDER BY recipe_no",
                (status_filter,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM quality_result ORDER BY recipe_no"
            ).fetchall()
        return [dict(r) for r in rows]
