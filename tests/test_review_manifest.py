"""review_manifest 测试。"""

from __future__ import annotations

from kzocr.scheduler.review_manifest import (
    ReviewIssue,
    ReviewManifest,
    ReviewPageItem,
    build_review_manifest,
    feedback_apply,
)


class _MockDB:
    """模拟 BookDB 用于测试 build/apply。"""
    def __init__(self, book_code: str = "bk-test", anomalies: list[dict] | None = None):
        self.book_code = book_code
        self._anomalies = anomalies or []
        self._applied: list[tuple[int, int, int, str]] = []

    def get_unresolved_anomalies(self) -> list[dict]:
        return self._anomalies

    def save_line_human_final(self, page_num: int, para_seq: int, line_seq: int, human_final: str) -> None:
        self._applied.append((page_num, para_seq, line_seq, human_final))


class TestDataClasses:
    def test_review_issue_defaults(self):
        i = ReviewIssue(position=0, ocr_char="X")
        assert i.expected is None
        assert i.issue_type == "glyph"
        assert i.severity == "info"

    def test_review_page_item(self):
        pi = ReviewPageItem(page_num=1, priority="P0", engine_results={"t1": "abc"})
        assert pi.issues == []

    def test_review_manifest(self):
        pi = ReviewPageItem(page_num=0, priority="P1", engine_results={})
        m = ReviewManifest(book_code="test", pages=[pi])
        assert len(m.pages) == 1
        assert m.book_code == "test"


class TestBuildReviewManifest:
    def test_no_anomalies(self):
        db = _MockDB()
        m = build_review_manifest(db)
        assert m.pages == []

    def test_fail_becomes_p0(self):
        db = _MockDB(anomalies=[{
            "page_num": 0, "verdict": "FAIL", "details": "glyph mismatch",
        }])
        m = build_review_manifest(db)
        assert len(m.pages) == 1
        assert m.pages[0].priority == "P0"
        assert m.pages[0].page_num == 0

    def test_unknown_becomes_p1(self):
        db = _MockDB(anomalies=[{
            "page_num": 1, "verdict": "UNKNOWN", "details": "",
        }])
        m = build_review_manifest(db)
        assert m.pages[0].priority == "P1"

    def test_rare_becomes_p2(self):
        db = _MockDB(anomalies=[{
            "page_num": 2, "verdict": "RARE", "details": "herb term",
        }])
        m = build_review_manifest(db)
        assert m.pages[0].priority == "P2"

    def test_conf_low_adds_info_issue(self):
        db = _MockDB(anomalies=[{
            "page_num": 0, "verdict": "FAIL",
            "details": "conf_low;engine_conf=0.800",
        }])
        m = build_review_manifest(db)
        assert len(m.pages[0].issues) == 1
        assert m.pages[0].issues[0].severity == "info"

    def test_multiple_anomalies(self):
        db = _MockDB(anomalies=[
            {"page_num": 0, "verdict": "FAIL", "details": ""},
            {"page_num": 1, "verdict": "UNKNOWN", "details": ""},
            {"page_num": 2, "verdict": "RARE", "details": ""},
        ])
        m = build_review_manifest(db)
        assert len(m.pages) == 3
        assert [p.priority for p in m.pages] == ["P0", "P1", "P2"]


class TestFeedbackApply:
    def test_no_expected_returns_zero(self):
        db = _MockDB()
        pi = ReviewPageItem(page_num=0, priority="P0", engine_results={},
                             issues=[ReviewIssue(position=0, ocr_char="X")])
        m = ReviewManifest(book_code="test", pages=[pi])
        assert feedback_apply(m, db) == 0

    def test_with_expected_applies_one(self):
        db = _MockDB()
        pi = ReviewPageItem(page_num=0, priority="P0", engine_results={},
                             issues=[ReviewIssue(position=0, ocr_char="X", expected="Y")])
        m = ReviewManifest(book_code="test", pages=[pi])
        assert feedback_apply(m, db) == 1
        assert db._applied == [(0, 1, 1, "Y")]

    def test_multiple_pages(self):
        db = _MockDB()
        m = ReviewManifest(book_code="test", pages=[
            ReviewPageItem(page_num=0, priority="P0", engine_results={},
                            issues=[ReviewIssue(position=0, ocr_char="A", expected="B")]),
            ReviewPageItem(page_num=1, priority="P1", engine_results={},
                            issues=[ReviewIssue(position=0, ocr_char="C", expected="D")]),
        ])
        assert feedback_apply(m, db) == 2
