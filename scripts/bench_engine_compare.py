#!/usr/bin/env python3
"""引擎性能基线对比：PaddleOCR vs RapidOCR + DPI 对比。"""
import time, numpy as np, fitz, sys

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.types import PageInput


def benchmark(dpi: int, pages: int, pdf: str) -> dict:
    """在指定 DPI 下跑两引擎，返回耗时统计。"""
    doc = fitz.open(pdf)
    n = min(pages, doc.page_count)

    po_adapter = PaddleOCRAdapter()
    ro_adapter = RapidOCRAdapter()

    po_times, ro_times = [], []
    po_chars, ro_chars = [], []

    for i in range(n):
        pix = doc[i].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        pi = PageInput(page_num=i, img=img)

        t0 = time.time()
        r1 = po_adapter.run_page(pi)
        po_times.append(time.time() - t0)
        po_chars.append(len(r1.text))

        t0 = time.time()
        r2 = ro_adapter.run_page(pi)
        ro_times.append(time.time() - t0)
        ro_chars.append(len(r2.text))

    doc.close()
    return {
        "dpi": dpi,
        "pages": n,
        "paddle": {"times": po_times, "chars": po_chars,
                    "avg": sum(po_times[1:]) / max(n - 1, 1) if n > 1 else po_times[0]},
        "rapid": {"times": ro_times, "chars": ro_chars,
                   "avg": sum(ro_times[1:]) / max(n - 1, 1) if n > 1 else ro_times[0]},
    }


def show(paddle_data, rapid_data):
    align = f"{'DPI':>3} | {'引擎':>8} | {'首(s)':>6} | {'均(s)':>6} | {'字':>5} | {'总(s)':>6}"
    sep = "-" * len(align)
    print(sep)
    print(align)
    print(sep)
    for dpi in sorted(paddle_data.keys()):
        pd = paddle_data[dpi]
        rd = rapid_data[dpi]
        for label, data in [("PaddleOCR", pd), ("RapidOCR", rd)]:
            total = sum(data["times"])
            print(f"{dpi:>3} | {label:>8} | {data['times'][0]:>5.1f}s | {data['avg']:>5.1f}s | {data['chars'][0]:>4} | {total:>5.1f}s")
        print(sep)


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else "/home/keen/Documents/test_10_pages.pdf"
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    results = {}
    for dpi in [150, 72]:
        print(f"\n>>> DPI={dpi} ({pages}页) ...")
        results[dpi] = benchmark(dpi, pages, pdf)
        print(f"    PaddleOCR: 首={results[dpi]['paddle']['times'][0]:.1f}s 均={results[dpi]['paddle']['avg']:.1f}s")
        print(f"    RapidOCR:  首={results[dpi]['rapid']['times'][0]:.1f}s 均={results[dpi]['rapid']['avg']:.1f}s")

    print(f"\n===== 性能基线对比 ({pages}页, {pdf}) =====")
    show({k: v["paddle"] for k, v in results.items()},
         {k: v["rapid"] for k, v in results.items()})
