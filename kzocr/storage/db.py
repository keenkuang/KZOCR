"""F2: 结构化入库状态机（page_progress + hierarchy_anomaly）。

SQLite 中间存储，支撑逐页进度追踪与 E3 验证异常记录。

每个 book_code 对应一个 SQLite 文件，默认路径 $KZOCR_OUTPUT_DIR/db/{book_code}.db。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Optional

from kzocr.engine.types import GlyphVerdict

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
        db_path = os.path.join(db_dir, f"{book_code}.db")
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.create_schema()

    def create_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
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

    def get_anomalies(
        self, *, status_filter: str = "pending"
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM hierarchy_anomaly WHERE resolution=? ORDER BY page_num",
            (status_filter,),
        ).fetchall()
        return [dict(r) for r in rows]

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
