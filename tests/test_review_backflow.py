"""W7 终校反馈 → 混淆集自动回流：端到端测试。

验证：人工在 review_manifest 终校修正的 (误认字 ocr_char → 正确字 expected)
经 feedback_apply 自动回流进自学习混淆集 learned_confusion.json，且下次同形近字
被 _is_priority 判 high、分侧检测器（load_confusion_keys_split）也受益。
"""
from __future__ import annotations

import json
from pathlib import Path

from kzocr.engine.types import GlyphVerdict
from kzocr.scheduler.cross_align import (
    _is_priority,
    add_learned_confusion,
    load_confusion_keys_split,
    load_confusion_set,
)
from kzocr.scheduler.review_manifest import (
    ReviewIssue,
    ReviewManifest,
    ReviewPageItem,
    _parse_confusion_pair,
    build_review_manifest,
    feedback_apply,
)
from kzocr.storage.db import BookDB


def _new_db(tmp_path: Path) -> BookDB:
    return BookDB("w7backflow", db_dir=str(tmp_path))


def _patch_learned(monkeypatch: object, tmp_path: Path) -> Path:
    p = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", p)
    return p


# ───────────── _parse_confusion_pair ─────────────

def test_parse_confusion_pair_confusion():
    assert _parse_confusion_pair("confusion;wrong=补;correct=朴") == ("补", "朴")


def test_parse_confusion_pair_non_confusion():
    assert _parse_confusion_pair("char_count_spike;len=500") == ("", "")
    assert _parse_confusion_pair("") == ("", "")


# ───────────── build_review_manifest 数据补全 ─────────────

def test_build_populates_ocr_char_from_confusion(tmp_path: Path):
    db = _new_db(tmp_path)
    db.record_anomaly(
        1,
        GlyphVerdict(
            status="RARE",
            confidence=0.6,
            details="confusion;wrong=补;correct=朴",
            detector_name="ConfusionSetDetector",
            force_review=True,
        ),
        ["ConfusionSetDetector"],
    )
    manifest = build_review_manifest(db)
    assert len(manifest.pages) == 1
    issues = manifest.pages[0].issues
    assert any(i.ocr_char == "补" for i in issues)
    db.close()


def test_build_no_ocr_char_for_non_confusion(tmp_path: Path):
    db = _new_db(tmp_path)
    db.record_anomaly(
        2,
        GlyphVerdict(
            status="FAIL",
            confidence=1.0,
            details="char_count_spike;len=500;median=200",
            detector_name="CharCountSpike",
        ),
        ["CharCountSpike"],
    )
    manifest = build_review_manifest(db)
    assert all(i.ocr_char == "" for i in manifest.pages[0].issues)
    db.close()


# ───────────── feedback_apply 回流接线 ─────────────

def test_feedback_apply_backflow(tmp_path: Path, monkeypatch):
    p = _patch_learned(monkeypatch, tmp_path)
    db = _new_db(tmp_path)
    manifest = ReviewManifest(
        book_code="w7backflow",
        pages=[
            ReviewPageItem(
                page_num=1,
                priority="P0",
                engine_results={},
                issues=[ReviewIssue(position=0, ocr_char="补", expected="朴", issue_type="glyph")],
            )
        ],
    )
    assert feedback_apply(manifest, db) == 1

    # 1) 落盘 learned_confusion.json
    assert p.is_file()
    rows = json.loads(p.read_text(encoding="utf-8"))
    assert any(r["wrong"] == "补" and r["correct"] == "朴" for r in rows)

    # 2) 下次路由即时生效（_is_priority 命中）
    cs = load_confusion_set(reload=True)
    assert cs.get("补") == "朴"
    assert _is_priority("补", "朴", cs) is True

    # 3) 分侧检测器也受益（一致性修复）
    split = load_confusion_keys_split(reload=True)
    assert "补" in split["wrong"]
    assert "朴" in split["correct"]
    db.close()


def test_feedback_apply_no_backflow_when_same(tmp_path: Path, monkeypatch):
    p = _patch_learned(monkeypatch, tmp_path)
    db = _new_db(tmp_path)
    # ocr_char == expected：非修正，不回流（仍回写 human_final）
    manifest = ReviewManifest(
        book_code="w7backflow",
        pages=[
            ReviewPageItem(
                page_num=1,
                priority="P0",
                engine_results={},
                issues=[ReviewIssue(position=0, ocr_char="朴", expected="朴", issue_type="glyph")],
            )
        ],
    )
    assert feedback_apply(manifest, db) == 1
    assert not p.is_file()  # 无新混淆对落盘
    db.close()


def test_feedback_apply_no_backflow_when_expected_empty(tmp_path: Path, monkeypatch):
    p = _patch_learned(monkeypatch, tmp_path)
    db = _new_db(tmp_path)
    manifest = ReviewManifest(
        book_code="w7backflow",
        pages=[
            ReviewPageItem(
                page_num=1,
                priority="P0",
                engine_results={},
                issues=[ReviewIssue(position=0, ocr_char="补", expected="", issue_type="glyph")],
            )
        ],
    )
    assert feedback_apply(manifest, db) == 0
    assert not p.is_file()
    db.close()


def test_backflow_dedup(tmp_path: Path, monkeypatch):
    p = _patch_learned(monkeypatch, tmp_path)
    assert add_learned_confusion("补", "朴", source="review_manifest") is True
    assert add_learned_confusion("补", "朴", source="review_manifest") is False
    rows = json.loads(p.read_text(encoding="utf-8"))
    assert len(rows) == 1
