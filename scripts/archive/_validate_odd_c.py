"""验证奇数页侧眉感知 left (c): left = 左侧窄行(侧眉)右缘 + 12, 有明确间隙才用, 否则退回 (a)。

偶数页沿用 (a): left = min((x1>80 最小 x1)-50, 120)。

与 doclayout 正文左缘 dl.left 比对:
  过裁: left > dl.left + 25
  侧眉残留: left <= side_right (仍包含侧眉右缘) —— (c) 构造上应≈0
  净切: side_right < left <= dl.left (排除侧眉且不切正文)
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import Counter

import numpy as np
from PIL import Image

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)
from _preview_even_formula import per_block

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
_MODEL = None


def doclayout_box(img):
    global _MODEL
    if _MODEL is None:
        _MODEL = _get_doclayout_model()
    if _MODEL is None:
        return None
    try:
        results = list(_MODEL.predict(img, batch_size=1))
    except Exception:
        return None
    if not results:
        return None
    try:
        boxes = _extract_doclayout_boxes(results[0])
    except Exception:
        return None
    body = [b for b in boxes if b.get("label") in _BODY_LABELS]
    if not body:
        return None
    h, w = img.shape[:2]
    xs = [b["coordinate"][0] for b in body]
    ys = [b["coordinate"][1] for b in body]
    xe = [b["coordinate"][2] for b in body]
    ye = [b["coordinate"][3] for b in body]
    return (max(0, int(min(xs)) - 15), max(0, int(min(ys)) - 15),
            min(w, int(max(xe)) + 15), min(h, int(max(ye)) + 10))


def odd_left_c(blocks, w):
    """奇数页侧眉感知 left。"""
    narrow = [b for b in blocks if b[4] <= 0.5 * w]
    narrow_left = [b for b in narrow if b[0] < 0.25 * w]   # 左侧窄行(侧眉/竖排/页码)
    body_left = min((b[0] for b in blocks if b[0] > 80), default=w)
    if narrow_left:
        side_right = max(b[2] for b in narrow_left)
        if side_right < body_left - 15:    # 侧眉明显在正文左侧(有间隙)
            return side_right + 12, side_right, "c"
    # 退回 (a)
    cands = [b[0] for b in blocks if b[0] > 80]
    fallback = min(min(cands) - 50, 120) if cands else 120
    return fallback, None, "a"


def main() -> None:
    cnt = Counter()
    ds = []
    used_c = 0
    done = 0
    for n in range(22, 993):
        if n % 2 == 0:
            continue
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        blocks = per_block(img, merged, w)
        left, side_right, mode = odd_left_c(blocks, w)
        if mode == "c":
            used_c += 1
        dl = doclayout_box(img)
        if dl is None:
            continue
        d = left - dl[0]
        ds.append(d)
        if left > dl[0] + 25:
            cnt["过裁"] += 1
        elif side_right is not None and left <= side_right:
            cnt["侧眉残留"] += 1
        elif side_right is not None and side_right < left <= dl[0]:
            cnt["净切(排除侧眉不切正文)"] += 1
        else:
            cnt["其他(含退回a)"] += 1
        done += 1
        if done % 100 == 0:
            print(f"[进度] {done}", file=sys.stderr, flush=True)

    import statistics as st
    print(f"\n=== 奇数页 (n={len(ds)}, 其中用(c)={used_c}) ===")
    for k, v in cnt.most_common():
        print(f"  {k}: {v} ({v/len(ds)*100:.1f}%)")
    if ds:
        print(f"  Δ=left-dl.left: min={min(ds):+d} med={int(st.median(ds)):+d} max={max(ds):+d}")


if __name__ == "__main__":
    main()
