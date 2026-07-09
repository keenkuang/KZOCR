"""C2: 原子写入工具模块 — atomic_write + is_complete。

依赖：此模块是 P1 基础工具，后续所有阶段（C1/C3/C5）的持久化操作均依赖此模块。
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """原子写入文本文件：先写 .tmp 再 os.replace，防止半写文件。

    兼容 os.fsync 确保数据落盘（非仅写入缓冲区）。
    自动创建父目录。

    Args:
        path: 目标文件路径。
        content: 要写入的文本内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    # fsync 确保数据落盘
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """原子写入二进制文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(content)
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)


def is_complete(path: Path) -> bool:
    """检查文件是否完整（存在且非空）。

    用于断点续传：文件存在 + 非空 = 已完成。
    """
    return path.exists() and path.stat().st_size > 0
