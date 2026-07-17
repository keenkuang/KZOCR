"""适配器冒烟测试：验证 PaddleOCRAdapter/RapidOCRAdapter 可实际跑通。

用法：
    python scripts/smoke_adapters.py <pdf> [--pages 2]

需要真实古籍 PDF（当前无图像无法 mock），依赖 paddleocr/rapidocr_onnxruntime。
"""
from __future__ import annotations

import argparse
import time

import fitz
import numpy as np

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.types import PageInput


def render_page(pdf_path: str, i: int, dpi: int = 150) -> np.ndarray:
    doc = fitz.open(pdf_path)
    pix = doc[i].get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="古籍 PDF")
    ap.add_argument("--pages", type=int, default=2)
    args = ap.parse_args()

    print("[info] 加载 PaddleOCRAdapter ...")
    po_adapter = PaddleOCRAdapter()
    print("[info] 加载 RapidOCRAdapter ...")
    ro_adapter = RapidOCRAdapter()

    for i in range(args.pages):
        t0 = time.time()
        img = render_page(args.pdf, i)
        page = PageInput(page_num=i, img=img)

        r1 = po_adapter.run_page(page)
        dt1 = time.time() - t0
        print(f"  page {i}: PaddleOCR={len(r1.text)}字 boxes={len(r1.boxes or [])} {dt1:.1f}s")

        r2 = ro_adapter.run_page(page)
        dt2 = time.time() - t0
        print(f"  page {i}: RapidOCR={len(r2.text)}字 boxes={len(r2.boxes or [])} {dt2:.1f}s")

        if r1.boxes and r2.boxes:
            print(f"      bbox 样例: PaddleOCR={r1.boxes[0]} RapidOCR={r2.boxes[0]}")

    print("[ok] 适配器冒烟完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
