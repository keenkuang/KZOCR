"""预验证新 left 公式: left = min( (x1>80 的行的最小 x1) - 50, 120 )

与 doclayout 正文左缘(dl.left)比对:
  过裁: new_left > dl.left + 25   (cv2 左界在正文左缘右边 → 切到正文)
  欠裁左(留侧眉): new_left < dl.left - 40  (cv2 左界远在正文左缘左边)
新旧 left 都统计,看新公式是否消除过裁。

用法: python _validate_left.py
"""
from __future__ import annotations

import sys
from pathlib import Path

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


def new_left(blocks):
    cands = [b[0] for b in blocks if b[0] > 80]
    if not cands:
        return 120
    return min(min(cands) - 50, 120)


def old_left(blocks):
    # 现有偶数页公式: max(60, min(x1>100)-20)
    cands = [b[0] for b in blocks if b[0] > 100]
    return max(60, min(cands) - 20) if cands else 60


def main() -> None:
    stats = {"odd": {"over": 0, "under": 0, "good": 0, "ds": []},
             "even": {"over": 0, "under": 0, "good": 0, "ds": []}}
    flagged = [41, 116, 192, 358, 386, 392, 460, 470, 472, 490, 502, 546, 796, 918]
    detail = []
    done = 0
    for n in range(22, 993):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        blocks = per_block(img, merged, w)
        nl = new_left(blocks)
        ol = old_left(blocks)
        dl = doclayout_box(img)
        if dl is None:
            continue
        key = "odd" if n % 2 == 1 else "even"
        d = nl - dl[0]
        stats[key]["ds"].append(d)
        if d > 25:
            stats[key]["over"] += 1
        elif d < -40:
            stats[key]["under"] += 1
        else:
            stats[key]["good"] += 1
        if n in flagged:
            detail.append((n, key, ol, nl, dl[0], d))
        done += 1
        if done % 200 == 0:
            print(f"[进度] {done}", file=sys.stderr, flush=True)

    import statistics as st
    for k in ("odd", "even"):
        ds = stats[k]["ds"]
        print(f"\n=== {k}页 (n={len(ds)}) ===")
        print(f"  过裁(new>dl+25): {stats[k]['over']}")
        print(f"  欠裁左(new<dl-40): {stats[k]['under']}")
        print(f"  正常(|Δ|≤40): {stats[k]['good']}")
        if ds:
            print(f"  Δ=new_left-dl.left: min={min(ds):+d} med={int(st.median(ds)):+d} max={max(ds):+d}")
    print("\n=== 原左过裁页对比 (old→new→dl.left, Δ=new-dl) ===")
    for n, key, ol, nl, dll, d in detail:
        print(f"  p{n:>4} {key} old={ol:>4} new={nl:>4} dl={dll:>4} Δ={d:+d} {'过裁' if d>25 else ('欠左' if d<-40 else 'ok')}")


if __name__ == "__main__":
    main()
