#!/usr/bin/env python3
"""char_boxes 耗时基准：量化开启 return_word_box（字符级 bbox）的额外开销。

对比同一页在两种模式下 PaddleOCR 的端到端耗时（含解析）：
- OFF: engine.ocr(img, return_word_box=False)  行级识别
- ON : engine.ocr(img, return_word_box=True)   行级 + 逐字 bbox（生产默认）

复用 PaddleOCRAdapter 进程级单例，避免重复加载模型；两种模式共享同一
检测/识别图，开销仅来自逐字 bbox 头，因此相对开销与 DPI 近似无关——
默认 DPI=72（生产推荐）即可代表。
"""
import argparse
import json
import sys
import time

import fitz
import numpy as np

from kzocr.engine.adapters import PaddleOCRAdapter, _parse_ppocr_result


def _render(doc, i, dpi):
    pix = doc[i].get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )


def bench(pdf: str, pages: int, dpi: int) -> dict:
    doc = fitz.open(pdf)
    n = min(pages, doc.page_count)
    engine = PaddleOCRAdapter._get_engine()

    # 预热：生产默认 ON 模式触发首图构图（不计入基准）
    warm = _render(doc, 0, dpi)
    engine.ocr(warm, return_word_box=True)
    print(">>> 预热完成（ON 模式首图构图，已丢弃）")

    off_times, on_times, char_counts, line_counts = [], [], [], []
    for i in range(n):
        img = _render(doc, i, dpi)

        t0 = time.time()
        res_off = engine.ocr(img, return_word_box=False)
        _parse_ppocr_result(res_off)
        off_times.append(time.time() - t0)

        t0 = time.time()
        res_on = engine.ocr(img, return_word_box=True)
        r_on = _parse_ppocr_result(res_on)
        on_times.append(time.time() - t0)

        cb = r_on.char_boxes
        char_counts.append(sum(len(line) for line in cb) if cb else 0)
        line_counts.append(len(cb) if cb else 0)

        print(
            f"  page {i}: OFF={off_times[-1]:.1f}s  ON={on_times[-1]:.1f}s  "
            f"行={line_counts[-1]:3d} 字框={char_counts[-1]:4d}"
        )

    doc.close()
    off_avg = sum(off_times) / len(off_times)
    on_avg = sum(on_times) / len(on_times)
    return {
        "dpi": dpi,
        "pages": n,
        "pdf": pdf,
        "off_avg": off_avg,
        "on_avg": on_avg,
        "delta": on_avg - off_avg,
        "overhead_pct": (on_avg / off_avg - 1.0) * 100.0,
        "char_boxes_total": sum(char_counts),
        "lines_total": sum(line_counts),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default="/home/keen/Documents/test_10_pages.pdf")
    ap.add_argument("pages", nargs="?", type=int, default=5)
    ap.add_argument("--dpi", type=int, default=72)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    r = bench(args.pdf, args.pages, args.dpi)
    print(f"\n===== char_boxes 耗时基准 (DPI={r['dpi']}, {r['pages']}页) =====")
    print(f"  OFF (关逐字框): {r['off_avg']:.1f}s/页")
    print(f"  ON  (开逐字框): {r['on_avg']:.1f}s/页")
    print(f"  绝对增量:        {r['delta']:+.1f}s/页")
    print(f"  相对开销:        {r['overhead_pct']:+.1f}%")
    print(f"  逐字框总量:      {r['char_boxes_total']} 个 / {r['lines_total']} 行")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"  已写 {args.json}")
    sys.exit(0)
