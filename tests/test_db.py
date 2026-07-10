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
