"""review_manifest 分歧高亮 HTML 导出 + 差异高亮单测（零资源）。"""

from kzocr.scheduler.cross_align import Divergence
from kzocr.scheduler.review_manifest import _highlight_diff, export_divergence_html
from kzocr.storage.db import BookDB


def test_highlight_diff_marks_difference() -> None:
    a_html, b_html = _highlight_diff("黄苓", "黄芩")
    assert '<mark class="diff">' in a_html
    assert '<mark class="diff">' in b_html
    # 公共前缀「黄」不应被高亮
    assert "黄" in a_html.split("<mark")[0]


def test_highlight_diff_escapes_html() -> None:
    a_html, _ = _highlight_diff("<scr>&", "x")
    assert "&lt;scr&gt;&amp;" in a_html
    # 差异片段被 <mark> 包裹
    assert '<mark class="diff">' in a_html


def test_highlight_diff_identical_no_mark() -> None:
    a_html, b_html = _highlight_diff("附子", "附子")
    assert "<mark" not in a_html
    assert "<mark" not in b_html


def test_export_divergence_html_groups_and_highlights(tmp_path) -> None:
    db = BookDB("bk1", db_dir=str(tmp_path))
    try:
        db.write_cross_divergences(
            0,
            [
                Divergence(
                    page_no=0, div_type="replace", a_seg="黄苓", b_seg="黄芩",
                    priority="high", status="pending",
                    engine_a="PaddleOCR", engine_b="RapidOCR",
                ),
                Divergence(
                    page_no=1, div_type="replace", a_seg="附子", b_seg="附子",
                    priority="low", status="arbitrated",
                    engine_a="PaddleOCR", engine_b="RapidOCR",
                ),
            ],
            engine_a="PaddleOCR", engine_b="RapidOCR",
        )
        out = str(tmp_path / "report.html")
        path = export_divergence_html(db, "bk1", out_path=out)
        content = open(path, encoding="utf-8").read()

        # 分歧片段与差异高亮（文字被 mark 拆散，分别检查字符）
        assert "黄" in content
        # 苓/B 侧 diff（a_seg=黄苓→黄相同、苓diff；b_seg=黄芩→黄相同、芩diff）
        assert '<mark class="diff">苓</mark>' in content
        assert '<mark class="diff">芩</mark>' in content
        # 相同片段不应高亮（附子==附子，整段 equal 无 mark）
        assert "附子" in content
        assert '<mark class="diff">附子</mark>' not in content
        # 按优先级分组，high 排在 low 之前
        assert "优先级 high" in content and "优先级 low" in content
        assert content.index("优先级 high") < content.index("优先级 low")
        # 报告标题含 book_code
        assert "bk1" in content
    finally:
        db.close()


def test_export_divergence_html_empty(tmp_path) -> None:
    db = BookDB("bk_empty", db_dir=str(tmp_path))
    try:
        out = str(tmp_path / "empty.html")
        path = export_divergence_html(db, "bk_empty", out_path=out)
        content = open(path, encoding="utf-8").read()
        assert "无跨引擎分歧记录" in content
    finally:
        db.close()
