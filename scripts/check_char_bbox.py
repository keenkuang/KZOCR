#!/usr/bin/env python3
"""检查两引擎的 char bbox 能力。"""
import fitz
import numpy as np

from paddleocr import PaddleOCR
from rapidocr_onnxruntime import RapidOCR

doc = fitz.open("/home/keen/Documents/test_10_pages.pdf")
pix = doc[0].get_pixmap(dpi=150)
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
doc.close()

# ── PaddleOCR ──
for try_return_word in [False, True]:
    ocr = PaddleOCR(return_word_box=try_return_word)
    res = ocr.predict(img)
    rb = res[0].get("rec_boxes", None)
    first_box = rb[0] if rb is not None and len(rb) > 0 else None
    is_char = isinstance(first_box, (list, np.ndarray)) and len(first_box) > 0 and isinstance(first_box[0], (list, np.ndarray))
    n_boxes = len(first_box) if is_char else 0
    print(f"PaddleOCR return_word_box={try_return_word}: line_boxes={len(rb)} char_level={is_char} first_line_boxes={n_boxes}")

# ── RapidOCR ──
ro = RapidOCR()
out, _ = ro(img)
print(f"RapidOCR: lines={len(out)}")
if len(out) > 0:
    item = out[0]
    print(f"  item[0] len={len(item)}")
    for j in range(len(item)):
        v = item[j]
        if isinstance(v, np.ndarray):
            print(f"  item[{j}]: ndarray shape={v.shape}")
        else:
            print(f"  item[{j}]: {type(v).__name__}={str(v)[:60]}")
    # Check if any item has char-level bbox
    for i in range(min(3, len(out))):
        if len(out[i]) >= 4:
            print(f"  line {i} has 4+ elements — checking...")
            for j in range(2, len(out[i])):
                v = out[i][j]
                if isinstance(v, np.ndarray) and len(v.shape) >= 2:
                    print(f"    item[{j}]: ndarray shape={v.shape} — possible char bbox")

