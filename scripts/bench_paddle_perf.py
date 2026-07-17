#!/usr/bin/env python3
"""PaddleOCR 多页连续性能基准测试。"""
import time, numpy as np, fitz, sys

from kzocr.engine.adapters import PaddleOCRAdapter
from kzocr.engine.types import PageInput

adapter = PaddleOCRAdapter()
doc = fitz.open(sys.argv[1])
n = min(int(sys.argv[2]) if len(sys.argv) > 2 else 5, doc.page_count)
times = []

for i in range(n):
    t0 = time.time()
    pix = doc[i].get_pixmap(dpi=150)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    r = adapter.run_page(PageInput(page_num=i, img=img))
    elapsed = time.time() - t0
    times.append(elapsed)
    tag = " [模型加载]" if i == 0 and elapsed > 30 else ""
    print(f"  page {i}: {len(r.text):4d}字  bbox={len(r.boxes or []):3d}  {elapsed:.1f}s{tag}")

doc.close()
avg = sum(times[1:]) / max(len(times) - 1, 1)
print(f"\n--- 汇总 ({n}页) ---")
print(f"  首次加载 + 首页: {times[0]:.1f}s")
print(f"  后续 {n-1} 页均值: {avg:.1f}s/页")
print(f"  总耗时: {sum(times):.1f}s")
