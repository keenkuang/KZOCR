"""F2: 结构化入库状态机测试。"""

from __future__ import annotations

import os
import tempfile

import pytest

from kzocr.engine.types import GlyphVerdict
from kzocr.storage.db import BookDB


@pytest.fixture
def tmp_db():
    """创建临时 BookDB，测试结束后清理。"""
    td = tempfile.mkdtemp()
    db = BookDB("test_book", db_dir=td)
    yield db
    db.close()
    # 清理 .db-wal / .db-shm
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


def test_create_schema_tables_exist(tmp_db):
    """创建 DB 后 page_progress / hierarchy_anomaly 表存在。"""
    tables = tmp_db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in tables]
    assert "page_progress" in names
    assert "hierarchy_anomaly" in names


def test_init_page_upsert(tmp_db):
    """init_page 幂等：同页号多次调用不抛错。"""
    tmp_db.init_page(0)
    tmp_db.init_page(0)  # 第二次不应抛错
    progress = tmp_db.get_page_progress(0)
    assert progress is not None
    assert progress["page_num"] == 0


def test_update_ocr_and_verify(tmp_db):
    """更新后查询返回正确值。"""
    tmp_db.init_page(5)
    tmp_db.update_ocr(5, status="success", char_count=1200, latency_ms=3400)
    tmp_db.update_verify(5, verdict="PASS", details="all_detectors_passed")
    row = tmp_db.get_page_progress(5)
    assert row is not None
    assert row["ocr_status"] == "success"
    assert row["char_count"] == 1200
    assert row["ocr_elapsed_ms"] == 3400
    assert row["verify_status"] == "PASS"


def test_record_anomaly(tmp_db):
    """记录后 anomalies 列表包含该记录。"""
    tmp_db.init_page(3)
    verdict = GlyphVerdict(status="FAIL", confidence=1.0, details="toxin_dose;herb=附子;severity=critical", detector_name="ToxinDoseDetector")
    tmp_db.record_anomaly(3, verdict=verdict, detector_chain=["ToxinDoseDetector"])
    anoms = tmp_db.get_anomalies()
    assert len(anoms) == 1
    assert anoms[0]["page_num"] == 3
    assert anoms[0]["verdict_status"] == "FAIL"
    assert anoms[0]["resolution"] == "pending"


def test_anomaly_resolution(tmp_db):
    """resolution 变更后可过滤查询。"""
    tmp_db.init_page(1)
    v = GlyphVerdict(status="UNKNOWN", confidence=0.6)
    tmp_db.record_anomaly(1, verdict=v)
    # 确认是 pending
    pending = tmp_db.get_anomalies(status_filter="pending")
    assert len(pending) == 1
    # 标记 fixed
    tmp_db._conn.execute("UPDATE hierarchy_anomaly SET resolution='fixed' WHERE page_num=1")
    tmp_db._conn.commit()
    # 不再出现在 pending 中
    assert tmp_db.get_anomalies(status_filter="pending") == []


def test_multiple_pages(tmp_db):
    """多页写入后 all_progress 返回正确数量。"""
    for i in range(5):
        tmp_db.init_page(i)
        tmp_db.update_ocr(i, status="success", char_count=100 * (i + 1), latency_ms=500)
        tmp_db.update_verify(i, verdict="PASS" if i % 2 == 0 else "RARE")
    all_p = tmp_db.get_all_progress()
    assert len(all_p) == 5
    # 过滤
    success = tmp_db.get_all_progress(status_filter="success")
    assert len(success) == 5  # 全部成功
    pending = tmp_db.get_all_progress(status_filter="pending")
    assert len(pending) == 0


def test_book_code_isolation(tmp_db):
    """不同 book_code 的文件不串。"""
    td = os.path.dirname(tmp_db._conn.execute("PRAGMA database_list").fetchone()[2])
    # 创建另一个 BookDB
    db2 = BookDB("other_book", db_dir=td)
    try:
        db2.init_page(0)
        db2.update_ocr(0, status="success", char_count=500, latency_ms=100)
        # 第一个 db 不应看到 second_book 的数据
        assert tmp_db.get_page_progress(0) is None
    finally:
        db2.close()


def test_update_import(tmp_db):
    """导入状态更新正确。"""
    tmp_db.init_page(0)
    tmp_db.update_import(0, status="imported", count=3, error="")
    row = tmp_db.get_page_progress(0)
    assert row["import_status"] == "imported"
    assert row["import_count"] == 3


def test_get_page_progress_nonexistent(tmp_db):
    """不存在的页号返回 None。"""
    assert tmp_db.get_page_progress(999) is None


# ── quality_result ──

def test_quality_result_save_and_query(tmp_db):
    """save_quality_result 后 get_quality_results 返回。"""
    tmp_db.save_quality_result("1.1", "verified", 1.0, "[]")
    tmp_db.save_quality_result("1.2", "corrected", 0.6, '[{"field":"组成","type":"missing_field"}]')
    all_r = tmp_db.get_quality_results()
    assert len(all_r) == 2
    verified = tmp_db.get_quality_results(status_filter="verified")
    assert len(verified) == 1
    assert verified[0]["recipe_no"] == "1.1"


def test_quality_result_upsert(tmp_db):
    """同 recipe_no 写入两次 → 覆盖。"""
    tmp_db.save_quality_result("1.1", "verified", 1.0, "[]")
    tmp_db.save_quality_result("1.1", "corrected", 0.5, '[{"field":"test"}]')
    results = tmp_db.get_quality_results()
    assert len(results) == 1
    assert results[0]["confidence"] == 0.5


# ── cross_divergence ──

def test_write_cross_divergences_roundtrip(tmp_db):
    """写入分歧后读取返回同一记录。"""
    from kzocr.scheduler.cross_align import Divergence
    divs = [
        Divergence(page_no=0, div_type="replace", a_seg="三", b_seg="二",
                   a_context="【三】", priority="high", engine_a="t1", engine_b="t3"),
        Divergence(page_no=0, div_type="delete", a_seg="", b_seg="", priority="normal"),
    ]
    n = tmp_db.write_cross_divergences(0, divs, engine_a="t1", engine_b="t3")
    assert n == 2

    rows = tmp_db.get_cross_divergences()
    assert len(rows) == 2
    assert rows[0]["priority"] == "high"
    assert rows[0]["a_seg"] == "三"
    assert rows[0]["engine_a"] == "t1"
    assert rows[0]["engine_b"] == "t3"


def test_get_cross_divergences_filter(tmp_db):
    """按 page_no / priority 过滤。"""
    from kzocr.scheduler.cross_align import Divergence
    d1 = Divergence(page_no=0, div_type="replace", a_seg="三", b_seg="二", priority="high")
    d2 = Divergence(page_no=1, div_type="replace", a_seg="日", b_seg="曰", priority="normal")
    tmp_db.write_cross_divergences(0, [d1])
    tmp_db.write_cross_divergences(1, [d2])

    paged = tmp_db.get_cross_divergences(page_no=0)
    assert len(paged) == 1
    assert paged[0]["a_seg"] == "三"

    high = tmp_db.get_cross_divergences(priority="high")
    assert len(high) == 1

    none = tmp_db.get_cross_divergences(page_no=9)
    assert none == []


def test_update_cross_divergence_status(tmp_db):
    """update_cross_divergence_status 定位更新。"""
    from kzocr.scheduler.cross_align import Divergence
    d = Divergence(page_no=0, div_type="replace", a_seg="三", b_seg="二", priority="high")
    tmp_db.write_cross_divergences(0, [d])

    affected = tmp_db.update_cross_divergence_status(0, "replace", "三", "二", "accepted_a")
    assert affected == 1  # 匹配同一行

    rows = tmp_db.get_cross_divergences()
    assert rows[0]["status"] == "accepted_a"


# ── hierarchy_anomaly 补充 ──

def test_resolve_anomaly(tmp_db):
    """resolve_anomaly 更新决议状态。"""
    tmp_db.init_page(1)
    v = GlyphVerdict(status="UNKNOWN", confidence=0.6, details="test")
    tmp_db.record_anomaly(1, verdict=v)
    anoms = tmp_db.get_anomalies()
    assert len(anoms) == 1
    aid = anoms[0]["id"]

    tmp_db.resolve_anomaly(aid, "fixed", "人工复核确认")
    anoms_after = tmp_db.get_anomalies()
    assert len(anoms_after) == 0  # resolution=fixed 默认不返回

    fixed = tmp_db.get_anomalies(status_filter="fixed")
    assert len(fixed) == 1
    assert fixed[0]["note"] == "人工复核确认"


def test_get_unresolved_anomalies_joins_page_progress(tmp_db):
    """get_unresolved_anomalies 联表 page_progress 获取 char_count。"""
    tmp_db.init_page(1, char_count=300)
    v = GlyphVerdict(status="FAIL", confidence=1.0, details="test")
    tmp_db.record_anomaly(1, verdict=v)

    unresolved = tmp_db.get_unresolved_anomalies()
    assert len(unresolved) == 1
    assert unresolved[0]["page_num"] == 1
    assert unresolved[0]["char_count"] == 300  # 联表数据


# ── benchmark ──

def test_write_benchmark(tmp_db):
    """write_benchmark 写入后可查询。"""
    tmp_db.init_page(0, char_count=100, engine_label="t1")
    tmp_db.update_ocr(0, status="success", char_count=100, latency_ms=500)
    tmp_db.write_benchmark(
        book_code="test_book", engine="t1",
        total_pages=1, success_pages=1, fail_pages=0,
        total_latency_ms=500, total_elapsed_s=10.0,
    )
    rows = tmp_db._conn.execute(
        "SELECT * FROM benchmark_results"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["engine"] == "t1"
    assert rows[0]["total_pages"] == 1
    assert rows[0]["success_pages"] == 1


# ── close / vacuum ──

def test_close_prevents_further_ops(tmp_db):
    """close 后任何操作抛出 sqlite3.ProgrammingError。"""
    tmp_db.close()
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        tmp_db.init_page(0)


def test_vacuum_completes_and_db_valid(tmp_db):
    """vacuum 成功执行且数据库仍可正常读写。"""
    tmp_db.init_page(0, char_count=100)
    tmp_db.update_ocr(0, status="success", char_count=100, latency_ms=500)
    tmp_db.vacuum()
    # vacuum 后数据库仍正常工作
    row = tmp_db.get_page_progress(0)
    assert row is not None
    assert row["char_count"] == 100
    assert row["ocr_status"] == "success"


def test_e2e_expansion_table_created(tmp_db):
    """e2e_expansion 表存在（schema 已落地）。"""
    names = [r[0] for r in tmp_db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "e2e_expansion" in names


def test_save_and_get_e2e_expansion(tmp_db):
    """save_e2e_expansion 写入后 get_e2e_expansions 按 book_code 返回该记录。"""
    import json as _json
    rid = tmp_db.save_e2e_expansion(
        book_code="test_book",
        pdf="/x/foo.pdf",
        book_title="foo",
        pages_processed=40,
        pages_requested=40,
        total_divergences=100,
        high_divergences=20,
        render_warnings=[3, 7],
        batch="2026-07-22",
    )
    assert rid >= 1
    rows = tmp_db.get_e2e_expansions("test_book")
    assert len(rows) == 1
    r = rows[0]
    assert r["pdf"] == "/x/foo.pdf"
    assert r["book_title"] == "foo"
    assert r["pages_processed"] == 40
    assert r["total_divergences"] == 100
    assert r["high_divergences"] == 20
    assert _json.loads(r["render_warnings_json"]) == [3, 7]
    assert r["batch"] == "2026-07-22"
    # 保留历史：再写一条，按 run_at 升序返回 2 条
    tmp_db.save_e2e_expansion(
        book_code="test_book", pdf="/x/foo.pdf", book_title="foo",
        pages_processed=80, total_divergences=50, high_divergences=10,
    )
    assert len(tmp_db.get_e2e_expansions("test_book")) == 2
