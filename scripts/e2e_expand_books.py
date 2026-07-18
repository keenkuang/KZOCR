#!/usr/bin/env python3
"""多古籍跨引擎分歧对齐扩面验证（轻量路径）。

绕过 orchestrator 重流水线，直接：
  渲染(150dpi) → PaddleOCR + RapidOCR 双引擎 → run_cross_align（与 orchestrator 同一函数）
  → 汇总每本书的「总分歧 / 高优先级分歧」数量，验证跨引擎分歧对齐在更多古籍上的泛化性。

依赖：paddleocr、rapidocr_onnxruntime（本机已装）。
用法：
  python scripts/e2e_expand_books.py --pdf <书1.pdf> --pdf <书2.pdf> --pages 20
  python scripts/e2e_expand_books.py --list books.txt --pages 20
"""
from __future__ import annotations

import argparse
import json
import os
import time

import fitz
import numpy as np

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.types import PageInput
from kzocr.scheduler.cross_align import load_confusion_set, run_cross_align


def render_page(pdf: str, page_num: int, dpi: int = 150) -> np.ndarray:
    doc = fitz.open(pdf)
    try:
        pix = doc[page_num].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if img.shape[2] == 4:
            img = img[:, :, :3]
        return img
    finally:
        doc.close()


def count_book(pdf: str, pages: int, dpi: int, paddle, rapid, confusion_set) -> dict:
    name = os.path.basename(pdf)
    total = 0
    high = 0
    per_page = []
    t0 = time.time()
    processed = 0
    for pno in range(pages):
        try:
            img = render_page(pdf, pno, dpi)
        except Exception as exc:
            print(f"  [{name}] p{pno} 渲染失败: {exc}", flush=True)
            continue
        a = paddle.run_page(PageInput(page_num=pno, img=img)).text or ""
        b = rapid.run_page(PageInput(page_num=pno, img=img)).text or ""
        divs = run_cross_align(pno, a, b, confusion_set=confusion_set)
        n_high = sum(1 for d in divs if d.priority == "high")
        total += len(divs)
        high += n_high
        processed += 1
        per_page.append({"page": pno, "div": len(divs), "high": n_high})
        if (pno + 1) % 5 == 0:
            print(f"  [{name}] p{pno+1}/{pages} 累计分歧={total} high={high}", flush=True)
    elapsed = time.time() - t0
    print(f"[完成] {name}: 处理 {processed} 页, 总分歧={total}, 高={high}, 用时 {elapsed:.0f}s",
          flush=True)
    return {
        "book": name,
        "pdf": pdf,
        "pages_processed": processed,
        "pages_requested": pages,
        "total_divergences": total,
        "high_divergences": high,
        "elapsed_s": round(elapsed, 1),
        "per_page": per_page,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="多古籍跨引擎分歧对齐扩面验证")
    ap.add_argument("--pdf", action="append", help="古籍 PDF 路径（可重复）")
    ap.add_argument("--list", help="含多行 PDF 路径的文本文件")
    ap.add_argument("--pages", type=int, default=20, help="每本书处理页数")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--out", default="e2e_expand/summary.json", help="汇总输出 JSON")
    args = ap.parse_args()

    pdfs: list[str] = list(args.pdf or [])
    if args.list:
        with open(args.list, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    pdfs.append(line)
    if not pdfs:
        print("[ERR] 未提供任何 PDF（用 --pdf 或 --list）", flush=True)
        return 2

    print(f"[info] 加载 PaddleOCRAdapter ...", flush=True)
    paddle = PaddleOCRAdapter()
    print(f"[info] 加载 RapidOCRAdapter ...", flush=True)
    rapid = RapidOCRAdapter()
    confusion_set = load_confusion_set()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    results = []
    for pdf in pdfs:
        if not os.path.isfile(pdf):
            print(f"[ERR] 跳过不存在的 PDF: {pdf}", flush=True)
            continue
        print(f"\n=== {pdf} ===", flush=True)
        results.append(count_book(pdf, args.pages, args.dpi, paddle, rapid, confusion_set))

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    print("\n=== 扩面汇总 ===", flush=True)
    print(f"{'古籍':<28} {'页':>4} {'总分歧':>8} {'高':>6} {'分歧/页':>8}", flush=True)
    for r in results:
        rpp = r["total_divergences"] / r["pages_processed"] if r["pages_processed"] else 0
        print(f"{r['book']:<26} {r['pages_processed']:>4} {r['total_divergences']:>8} "
              f"{r['high_divergences']:>6} {rpp:>8.1f}", flush=True)
    print(f"\n汇总已写入 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
