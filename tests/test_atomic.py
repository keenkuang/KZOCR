"""atomic.py 测试 — C2 原子写入 + 断点续传。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from kzocr.engines.atomic import atomic_write, atomic_write_bytes, is_complete


class TestAtomicWrite:
    def test_atomic_write_file_created(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        atomic_write(f, "hello world")
        assert f.read_text() == "hello world"

    def test_atomic_write_binary(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        data = b"\x00\x01\x02\xff"
        atomic_write_bytes(f, data)
        assert f.read_bytes() == data

    def test_is_complete_true(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        assert is_complete(f) is True

    def test_is_complete_false_empty(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.touch()
        assert is_complete(f) is False

    def test_is_complete_false_missing(self):
        f = Path("/tmp/nonexistent_file_xyz123.txt")
        assert is_complete(f) is False

    def test_atomic_write_creates_parent_dirs(self, tmp_path: Path):
        f = tmp_path / "a" / "b" / "c" / "deep.txt"
        atomic_write(f, "nested")
        assert f.read_text() == "nested"

    def test_atomic_write_tmp_not_left_behind(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        atomic_write(f, "data")
        tmp = tmp_path / "test.txt.tmp"
        assert not tmp.exists()
