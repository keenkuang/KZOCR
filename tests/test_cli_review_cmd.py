"""kzocr/cli_review.py 命令处理器纯逻辑单测（零外部资源 / 全 mock）。

覆盖四个 cmd_review_* 命令处理器内的输出与分支（原 cli_review.py 仅 78% 覆盖，
缺口在命令处理函数本身；本文件与原 test_cli_review.py（测 review_manifest）互补）：
- ``cmd_review_manifest``：清单打印
- ``cmd_review_apply``：批量回写（有修正 / 无修正两条分支）
- ``cmd_review_html``：分歧高亮 HTML 报告生成
- ``cmd_review_boxes``：字符级 bbox 可视化
"""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock

from kzocr import cli_review


def _args(**kw: object) -> argparse.Namespace:
    ns = argparse.Namespace()
    for key, val in kw.items():
        setattr(ns, key, val)
    return ns


def _fake_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.scheduler.db_dir = "/tmp"
    return cfg


def test_cmd_review_manifest(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    manifest = MagicMock()
    manifest.book_code = "BC-001"
    page = MagicMock()
    page.page_num = 3
    page.engine_results = [1, 2]
    page.issues = [1, 2, 3]
    manifest.pages = [page]
    monkeypatch.setattr(cli_review, "build_review_manifest", lambda db: manifest)

    rc = cli_review.cmd_review_manifest(_args(book_code="BC-001"))

    assert rc == 0
    fake_db.close.assert_called_once()
    out = capsys.readouterr().out
    assert "book_code=BC-001" in out
    assert "pages=1" in out
    assert "page 3: priority=" in out


def test_cmd_review_apply_with_fixes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(cli_review, "build_review_manifest", lambda db: MagicMock())
    monkeypatch.setattr(cli_review, "feedback_apply", lambda manifest, db: 3)

    rc = cli_review.cmd_review_apply(_args(book_code=["BC-1", "BC-2"]))

    assert rc == 0
    assert fake_db.close.call_count == 2
    out = capsys.readouterr().out
    assert "[BC-1] 已回写 3 条修正" in out
    assert "合计回写 6 条修正（2 本）" in out


def test_cmd_review_apply_no_fixes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(cli_review, "build_review_manifest", lambda db: MagicMock())
    monkeypatch.setattr(cli_review, "feedback_apply", lambda manifest, db: 0)

    rc = cli_review.cmd_review_apply(_args(book_code=["BC-1"]))

    assert rc == 0
    out = capsys.readouterr().out
    assert "[BC-1] 无待回写的修正条目" in out


def test_cmd_review_html(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(
        cli_review, "export_divergence_html",
        lambda db, book_code, out_path=None: "/tmp/BC-001_divergence.html",
    )

    rc = cli_review.cmd_review_html(_args(book_code="BC-001", out=None))

    assert rc == 0
    fake_db.close.assert_called_once()
    out = capsys.readouterr().out
    assert "已生成分歧高亮报告：/tmp/BC-001_divergence.html" in out


def test_cmd_review_boxes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(
        cli_review, "visualize_char_boxes",
        lambda db, book_code, page_num, pdf_path=None, out_path=None: "/tmp/BC-001_p2_boxes.png",
    )

    rc = cli_review.cmd_review_boxes(
        _args(book_code="BC-001", page_num=2, pdf=None, out=None),
    )

    assert rc == 0
    fake_db.close.assert_called_once()
    out = capsys.readouterr().out
    assert "已生成 bbox 可视化图：/tmp/BC-001_p2_boxes.png" in out


def test_cmd_review_manifest_json(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(cli_review, "load_config", lambda: _fake_cfg())
    fake_db = MagicMock()
    monkeypatch.setattr(cli_review, "BookDB", lambda *a, **k: fake_db)
    manifest = MagicMock()
    manifest.book_code = "BC-001"
    manifest.pages = []
    monkeypatch.setattr(cli_review, "build_review_manifest", lambda db: manifest)
    json_path = tmp_path / "BC-001_review_manifest.json"

    rc = cli_review.cmd_review_manifest(
        _args(book_code="BC-001", json=True, out=str(json_path)),
    )

    assert rc == 0
    fake_db.close.assert_called_once()
    assert json_path.exists()
    out = capsys.readouterr().out
    assert f"已导出审核清单 JSON：{json_path}" in out
