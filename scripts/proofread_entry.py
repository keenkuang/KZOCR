#!/usr/bin/env python3
"""KZOCR delivered proofread station - desktop entry point.

PyInstaller packaging entry: starts the FastAPI proofread station, opens browser.

Usage:
    ./proofread_app                  # scan current dir for *.db packages
    ./proofread_app --db a.db b.db
    ./proofread_app --books-dir /path/to/packages
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_TITLE = "KZOCR proofread station"


def _wait_for_server(url: str, timeout: float = 20.0) -> bool:
    """Poll the port until the server is ready; return whether it succeeded."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _show_splash(message: str):
    """Minimal startup splash window (tkinter, stdlib). None if no display."""
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


def _scan_dir(db_dir: Path) -> list[str]:
    """Scan a directory for *.db packages; validate each, skip invalid ones."""
    found = []
    for f in sorted(db_dir.glob("*.db")):
        if not f.is_file():
            continue
        try:
            from kzocr.doc import validate_proofread_package
            validate_proofread_package(f.resolve())
            found.append(str(f.resolve()))
        except Exception as exc:
            print(f"Warning: skip invalid package {f}: {exc}", file=sys.stderr)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(prog="KZOCR proofread station")
    ap.add_argument("--db", nargs="*", default=[],
                    help="One or more proofread packages (custom.db)")
    ap.add_argument("--books-dir", default="",
                    help="Directory to scan for *.db packages")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9090)
    args = ap.parse_args()

    db_list = [str(Path(d).resolve()) for d in args.db]
    if args.books_dir:
        db_list += _scan_dir(Path(args.books_dir))
    # Fallback: scan the current working directory for *.db packages.
    if not db_list:
        db_list = _scan_dir(Path.cwd())

    if not db_list:
        print("Error: no valid proofread package found. Use --db or --books-dir.",
              file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"Data sources ({len(db_list)}):")
    for d in db_list:
        print(f"  - {d}")
    print(f"Starting... {url}")

    splash = _show_splash(f"Starting proofread station...\n{url}")
    if splash is None:
        print("Starting proofread station, please wait...")

    def run_server() -> None:
        import uvicorn
        from kzocr.proofread.app import app_factory

        try:
            app = app_factory(*db_list)
        except Exception as exc:
            print(f"Failed to build app: {exc}", file=sys.stderr)
            return
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    if _wait_for_server(url):
        if splash is not None:
            splash.destroy()
        webbrowser.open(url)
    else:
        if splash is not None:
            splash.destroy()
        print(f"Warning: server not ready in time, open {url} manually",
              file=sys.stderr)

    server_thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
