"""用户公式多候选对比 PP-DocLayoutV3：哪个候选的偏移最恒定(校准一个常数即可)。

候选(左, 奇数页, 侧眉在左)：
  body_l = mean(x1 | x1>15)        全体左均值 all_l = mean(x1 全部)
  diff_l = body_l - all_l
  L_orig   = diff_l/2 - 15          (用户原版)
  L_body15 = body_l - 15            (固定余量, 锚定正文左缘)
  L_all15  = all_l  - 15            (= body_l - 15 - diff_l, 用差值自适应: 差值大→更靠外留余量)
  L_adp2   = body_l - (15 + 0.5*diff_l)

候选(右, 偶数页, 侧眉在右)：
  body_r = mean(x2 | x2<w-15)       全体右均值 all_r = mean(x2 全部)
  diff_r = all_r - body_r           (>=0, 侧眉把全体均值右拉量)
  R_orig   = diff_r/2
  R_body15 = body_r + 15
  R_all15  = all_r  + 15            (= body_r + 15 + diff_r, 自适应: 差值大→更靠外留余量)
  R_adp2   = body_r + (15 + 0.5*diff_r)

报告: 每个候选相对 dl.left(奇)/dl.right(偶) 的偏移分布(min/med/max/std)。
       std 最小者 = 偏移最恒定 = 校准一个常数即可, 且换书自动适配。
用法: python _measure_user_formula.py
"""
from __future__ import annotations

import os
import sys
import statistics as st
from pathlib import Path
from collections import defaultdict

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


def left_candidates(blocks):
    all_l = [b[0] for b in blocks]
    if not all_l:
        return {}
    body_l = [x for x in all_l if x > 15]
    bl = sum(body_l) / len(body_l) if body_l else sum(all_l) / len(all_l)
    al = sum(all_l) / len(all_l)
    diff = bl - al
    return {
        "L_orig": diff / 2 - 15,
        "L_body15": bl - 15,
        "L_all15": al - 15,
        "L_adp2": bl - (15 + 0.5 * diff),
    }


def right_candidates(blocks, w):
    all_r = [b[2] for b in blocks]
    if not all_r:
        return {}
    body_r = [x for x in all_r if x < w - 15]
    br = sum(body_r) / len(body_r) if body_r else sum(all_r) / len(all_r)
    ar = sum(all_r) / len(all_r)
    diff = ar - br
    return {
        "R_orig": diff / 2,
        "R_body15": br + 15,
        "R_all15": ar + 15,
        "R_adp2": br + (15 + 0.5 * diff),
    }


def _dist(name, vals):
    if not vals:
        print(f"  {name}: 无数据")
        return
    print(f"  {name}: min={min(vals):+.1f} med={st.median(vals):+.1f} "
          f"max={max(vals):+.1f} std={st.pstdev(vals):.1f} n={len(vals)}")


def main() -> None:
    odd_off = defaultdict(list)   # 奇: 各左候选 - dl.left
    even_off = defaultdict(list)  # 偶: 各右候选 - dl.right
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
        blocks = per_block(img, merged, w)
        is_odd = (n % 2 == 1)
        dl = doclayout_box(img)
        if dl is None:
            continue
        if is_odd:
            cands = left_candidates(blocks)
            for k, v in cands.items():
                odd_off[k].append(v - dl[0])
        else:
            cands = right_candidates(blocks, w)
            for k, v in cands.items():
                even_off[k].append(v - dl[2])
        done += 1
        if done % 200 == 0:
            print(f"[进度] {done}", file=sys.stderr, flush=True)

    print(f"\n=== 偏移 std 对比 (doclayout 真值, n={done}) ===")
    print(f"\n奇数页 左候选 - dl.left  (越小越恒定, 校准一个常数即可):")
    for k in ("L_orig", "L_body15", "L_all15", "L_adp2"):
        _dist(k, odd_off[k])
    print(f"\n偶数页 右候选 - dl.right:")
    for k in ("R_orig", "R_body15", "R_all15", "R_adp2"):
        _dist(k, even_off[k])


if __name__ == "__main__":
    main()
