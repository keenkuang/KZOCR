"""atomic.py 测试 — C2 原子写入 + 断点续传。"""
from __future__ import annotations

from pathlib import Path

import pytest

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

    def test_path_traversal_rejected(self, tmp_path: Path):
        """路径穿越：尝试写基目录外的路径应抛 ValueError。"""
        traversal = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="路径穿越"):
            atomic_write(traversal, "data", allowed_base=tmp_path)

    def test_path_traversal_rejected_bytes(self, tmp_path: Path):
        """路径穿越（二进制）：尝试写基目录外的路径应抛 ValueError。"""
        traversal = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="路径穿越"):
            atomic_write_bytes(traversal, b"data", allowed_base=tmp_path)

    def test_normal_path_allowed(self, tmp_path: Path):
        """合法路径：基目录内的路径不受影响。"""
        f = tmp_path / "allowed.txt"
        atomic_write(f, "hello", allowed_base=tmp_path)
        assert f.read_text() == "hello"

    def test_normal_path_allowed_bytes(self, tmp_path: Path):
        """合法路径（二进制）：基目录内的路径不受影响。"""
        f = tmp_path / "allowed.bin"
        atomic_write_bytes(f, b"\x00\x01", allowed_base=tmp_path)
        assert f.read_bytes() == b"\x00\x01"
