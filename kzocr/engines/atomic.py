"""C2: 原子写入工具模块 — atomic_write + is_complete。

依赖：此模块是 P1 基础工具，后续所有阶段（C1/C3/C5）的持久化操作均依赖此模块。
"""
from __future__ import annotations

import os
from pathlib import Path


def _check_base(path: Path, allowed_base: Path | None) -> Path:
    """解析路径并校验是否在允许的基目录下。

    Args:
        path: 要校验的路径。
        allowed_base: 可选的允许基目录。传入后校验 path 解析后必须在其下。

    Returns:
        解析后的绝对 Path。

    Raises:
        ValueError: 当 allowed_base 传入但 path 不在其下时。
    """
    resolved = path.resolve()
    if allowed_base is not None:
        base_resolved = allowed_base.resolve()
        # 检查 resolved 是否以 base_resolved 为前缀
        if not str(resolved).startswith(str(base_resolved) + os.sep) and resolved != base_resolved:
            raise ValueError(
                f"路径穿越被拒绝: {resolved} 不在允许的基目录 {base_resolved} 下"
            )
    return resolved


def atomic_write(path: Path, content: str, allowed_base: Path | None = None) -> None:
    """原子写入文本文件：先写 .tmp 再 os.replace，防止半写文件。

    兼容 os.fsync 确保数据落盘（非仅写入缓冲区）。
    自动创建父目录。

    Args:
        path: 目标文件路径。
        content: 要写入的文本内容。
        allowed_base: 可选的允许基目录。传入后会校验 path 解析后在其下。

    Raises:
        ValueError: 当 allowed_base 传入但 path 不在其下时。
    """
    path = _check_base(path, allowed_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    # fsync 确保数据落盘
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, content: bytes, allowed_base: Path | None = None) -> None:
    """原子写入二进制文件。

    Args:
        path: 目标文件路径。
        content: 要写入的字节内容。
        allowed_base: 可选的允许基目录。传入后会校验 path 解析后在其下。

    Raises:
        ValueError: 当 allowed_base 传入但 path 不在其下时。
    """
    path = _check_base(path, allowed_base)
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
    path = path.resolve()
    return path.exists() and path.stat().st_size > 0
