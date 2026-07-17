"""端到端验证生产 cv2 路径：crop_by_layout 降级时用 _find_body_boundaries 的奇偶公式，
对比 doclayout 正文框(dl.left / dl.right / dl.top / dl.bottom)统计左右与上下过裁率。

过裁判据(与 _cut_all 一致)：
  left 过裁: left > dl.left + 25
  right 过裁: right < dl.right - 25
  top 过裁: top > dl.top + 20
  bottom 过裁: bottom < dl.bottom - 20
用法: python _verify_production.py
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
    _find_body_boundaries,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

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


def main() -> None:
    cnt = Counter()
    odd_dl = []
    even_dl = []
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
        if not merged:
            continue
        top, bottom, left, right = _find_body_boundaries(img, merged, page_num=n)
        dl = doclayout_box(img)
        if dl is None:
            continue
        key = "odd" if n % 2 == 1 else "even"
        if left > dl[0] + 25:
            cnt[f"{key}:left过裁"] += 1
        if right < dl[2] - 25:
            cnt[f"{key}:right过裁"] += 1
        if top > dl[1] + 20:
            cnt[f"{key}:top过裁"] += 1
        if bottom < dl[3] - 20:
            cnt[f"{key}:bottom过裁"] += 1
        if key == "odd":
            odd_dl.append((left - dl[0], right - dl[2], top - dl[1], bottom - dl[3]))
        else:
            even_dl.append((left - dl[0], right - dl[2], top - dl[1], bottom - dl[3]))
        done += 1
        if done % 100 == 0:
            print(f"[进度] {done}", file=sys.stderr, flush=True)

    import statistics as st
    print(f"\n=== 生产 cv2 路径端到端验证 (n={done}) ===")
    for k, v in cnt.most_common():
        print(f"  {k}: {v} ({v/done*100:.1f}%)")
    for name, ds in (("奇数", odd_dl), ("偶数", even_dl)):
        if ds:
            dl_ = [d[0] for d in ds]
            dr = [d[1] for d in ds]
            dt = [d[2] for d in ds]
            db = [d[3] for d in ds]
            print(f"  {name}页 Δleft(min/med/max)={min(dl_):+d}/{int(st.median(dl_)):+d}/{max(dl_):+d} "
                  f"Δright={min(dr):+d}/{int(st.median(dr)):+d}/{max(dr):+d} "
                  f"Δtop={min(dt):+d}/{int(st.median(dt)):+d}/{max(dt):+d} "
                  f"Δbottom={min(db):+d}/{int(st.median(db)):+d}/{max(db):+d}")


if __name__ == "__main__":
    main()
