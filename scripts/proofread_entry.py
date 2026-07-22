#!/usr/bin/env python3
"""KZOCR 交付式校对台 · 桌面应用入口。

PyInstaller 打包入口：启动 FastAPI 校对工作台，默认打开浏览器。

用法：
    ./proofread_app                  # 默认在当前目录查找 custom.db
    ./proofread_app --db path/to/custom.db
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(prog="KZOCR 校对台")
    ap.add_argument("--db", default="",
                    help="校对包路径（custom.db）。缺省时在当前目录查找 custom.db")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9090)
    args = ap.parse_args()

    # 定位 custom.db
    db_path = args.db or _find_custom_db()
    if not db_path:
        print("错误：未找到 custom.db。请指定 --db <路径>", file=sys.stderr)
        return 1
    db_arg = os.path.abspath(db_path)

    print(f"数据源：{db_arg}")
    print(f"启动中... http://{args.host}:{args.port}")
    webbrowser.open(f"http://{args.host}:{args.port}")

    # 启动 uvicorn
    import uvicorn
    from kzocr.proofread.app import app_factory
    from kzocr.doc import validate_proofread_package

    try:
        validate_proofread_package(Path(db_arg))
    except Exception as exc:
        print(f"校对包校验失败：{exc}", file=sys.stderr)
        return 1

    app = app_factory(db_arg)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _find_custom_db() -> str:
    """在当前目录查找 custom.db。"""
    for f in Path.cwd().iterdir():
        if f.name == "custom.db" and f.is_file():
            return str(f)
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
