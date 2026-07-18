#!/usr/bin/env python3
"""多古籍跨引擎分歧对齐扩面验证（与 orchestrator 全路径对齐的渲染管线）。

渲染阶段复用 orchestrator 全路径的同一管线（_pdf_page_to_numpy + 版心裁切
_crop_to_body + 最长边≤2048 缩放），使双引擎分歧数字与 orchestrator 全路径
（秘方求真/验方新编 的参考数）严格可比，不再混入「全页 vs 版心裁切」的方法论差异。

双引擎比对仍走轻量直驱（绕过 orchestrator 逐页验证/落库），仅渲染与主编排对齐。
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
from kzocr.engine.run import _crop_to_body, _pdf_page_to_numpy
from kzocr.engine.types import PageInput
from kzocr.scheduler.cross_align import load_confusion_set, run_cross_align


def render_page(pdf: str, page_num: int, dpi: int = 150, max_pixels: int = 2048) -> np.ndarray:
    """渲染单页为 (H,W,3) RGB，并应用与 orchestrator 全路径一致的版心裁切 + 缩放。

    对齐目的：orchestrator 全路径用 render_pages（_pdf_page_to_numpy + _crop_to_body +
    最长边≤max_pixels 缩放）做版心裁切，去掉两引擎共错的页眉/页脚；扩面脚本此前用全页
    渲染，导致分歧绝对数偏高。此处复用同一管线，使分歧数字与 orchestrator 全路径严格可比。
    """
    doc = fitz.open(pdf)
    try:
        img = _pdf_page_to_numpy(doc[page_num], dpi=dpi)
    finally:
        doc.close()
    img = _crop_to_body(img, page_num=page_num)
    h, w = img.shape[:2]
    scale = min(max_pixels / max(h, w), 1.0)
    if scale < 1.0:
        from PIL import Image as PILImage
        pil = PILImage.fromarray(img)
        pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        img = np.array(pil)
    return img


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

    print("[info] 加载 PaddleOCRAdapter ...", flush=True)
    paddle = PaddleOCRAdapter()
    print("[info] 加载 RapidOCRAdapter ...", flush=True)
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
