"""kzocr/doc/zai.py + freeze.py 纯逻辑单测（零网络/零引擎，仅文件与路径逻辑）。

覆盖 _uid 格式、_resolve_db 三路优先级、_resolve_bookdb_path 构造、freeze_custom_db 冻结/缺失分支、_restrict_db_perms 权限限制。
"""

from __future__ import annotations

import os

import pytest
from pathlib import Path

from kzocr import config
from kzocr.doc.zai import (
    _resolve_bookdb_path,
    _resolve_db,
    _restrict_db_perms,
    _uid,
)
from kzocr.doc.freeze import freeze_custom_db


def test_uid_format_and_unique() -> None:
    u = _uid()
    assert u.startswith("c")
    assert len(u) == 33  # 'c' + uuid4().hex(32)
    assert _uid() != _uid()


def test_resolve_db_priority_db_over_zai() -> None:
    db = Path("/a/x.db")
    zai = Path("/b/y.db")
    assert _resolve_db(db, zai) == db


def test_resolve_db_falls_back_to_zai() -> None:
    zai = Path("/b/y.db")
    assert _resolve_db(None, zai) == zai


def test_resolve_db_default_uses_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config.config, "zai_db", str(tmp_path / "def.db"))
    resolved = _resolve_db(None, None)
    assert isinstance(resolved, Path)
    assert resolved == tmp_path / "def.db"


def test_resolve_bookdb_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DB_DIR", str(tmp_path))
    assert _resolve_bookdb_path("BK001") == tmp_path / "BK001.db"


def test_freeze_custom_db_normal(tmp_path) -> None:
    db = tmp_path / "old.db"
    db.write_text("x", encoding="utf-8")
    freeze_custom_db(db)
    mode = db.stat().st_mode & 0o777
    assert mode == 0o440
    marker = tmp_path / "old.db.frozen"
    assert marker.read_text(encoding="utf-8") == "frozen"


def test_freeze_custom_db_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        freeze_custom_db(tmp_path / "nope.db")


def test_restrict_db_perms(tmp_path) -> None:
    db = tmp_path / "z.db"
    db.write_text("x", encoding="utf-8")
    os.chmod(str(db), 0o644)
    _restrict_db_perms(db)
    mode = db.stat().st_mode & 0o777
    assert mode == 0o600
