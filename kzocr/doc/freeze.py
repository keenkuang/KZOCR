"""custom.db 冻结：设只读权限 + 写 .frozen 标记。

提取自 ``kzocr/adapter/to_zai_prisma.py`` — 文档模块重构 v0.23.0。
"""
from __future__ import annotations

import os
from pathlib import Path


def freeze_custom_db(db_path: Path) -> None:
    """冻结旧 custom.db：设只读权限(0440) + 写 .frozen 标记，落实「旧库冻结只读」。"""
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"待冻结库不存在：{p}")
    try:
        os.chmod(str(p), 0o440)
    except OSError:
        pass
    marker = Path(str(p) + ".frozen")
    marker.write_text("frozen", encoding="utf-8")
