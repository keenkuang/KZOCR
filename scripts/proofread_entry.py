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
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_TITLE = "KZOCR 校对台"


def _wait_for_server(url: str, timeout: float = 20.0) -> bool:
    """轮询端口直到服务就绪，返回是否成功。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _show_splash(message: str):
    """极简启动提示窗口（tkinter，标准库）。无显示环境时返回 None。"""
    try:
        import tkinter as tk
    except Exception:
        return None
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.title(APP_TITLE)
    root.geometry("320x120")
    root.resizable(False, False)
    label = tk.Label(root, text=message, font=("Arial", 11), padx=12, pady=12)
    label.pack(expand=True)
    root.update()
    return root


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

    url = f"http://{args.host}:{args.port}"
    print(f"数据源：{db_arg}")
    print(f"启动中... {url}")

    # 启动提示（降级：无 GUI 时仅控制台提示）
    splash = _show_splash(f"正在启动校对台…\n{url}")
    if splash is None:
        print("正在启动校对台，请稍候…")

    def run_server() -> None:
        import uvicorn
        from kzocr.proofread.app import app_factory
        from kzocr.doc import validate_proofread_package

        try:
            validate_proofread_package(Path(db_arg))
        except Exception as exc:
            print(f"校对包校验失败：{exc}", file=sys.stderr)
            return
        app = app_factory(db_arg)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 等服务就绪后再开浏览器并销毁启动窗口，避免「点了没反应」
    if _wait_for_server(url):
        if splash is not None:
            splash.destroy()
        webbrowser.open(url)
    else:
        if splash is not None:
            splash.destroy()
        print(f"警告：服务未及时就绪，请手动访问 {url}", file=sys.stderr)

    server_thread.join()
    return 0


def _find_custom_db() -> str:
    """在当前目录查找 custom.db。"""
    for f in Path.cwd().iterdir():
        if f.name == "custom.db" and f.is_file():
            return str(f)
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
