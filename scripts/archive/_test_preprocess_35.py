"""page 35 单页实测：图像预处理（连通域过滤去细线/噪点）对 B 与 v6 左界的影响。
不写库，只打印前后对比。"""
from __future__ import annotations

import json
import sys
import numpy as np
import cv2
from PIL import Image

from kzocr.engine import layout_crop as lc
from _verify_edge15_v5 import user_odd_left_v6
from _verify_edge_clean import crop_edge_clean

N = 35
SRC = Path_ = None  # placeholder
from pathlib import Path
SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")


def preprocess_cc(gray):
    """连通域过滤：删细竖线(宽<3且高>10)和小噪点(面积<25)。返回(白底灰度, 删除数, 删竖线数, 删噪点数)。"""
    _, bin = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    nb, labs, stats, cents = cv2.connectedComponentsWithStats(bin, 8)
    mask = np.ones(bin.shape, dtype=bool)
    removed = n_vline = n_dot = 0
    for i in range(1, nb):
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        if w < 3 and h > 10:
            mask[labs == i] = False; removed += 1; n_vline += 1
        elif area < 25:
            mask[labs == i] = False; removed += 1; n_dot += 1
    bin_clean = bin * mask
    return cv2.bitwise_not(bin_clean), removed, n_vline, n_dot


def preprocess_morph_open(gray, se=(1, 25), iters=1):
    """形态学开运算：用细长结构元吞噬「长而细」的线。
    横向 SE(1,25) → 删竖线；纵向 SE(25,1) → 删横线。返回(白底灰度, 删除像素数)。"""
    _, bin = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, se)
    opened = cv2.morphologyEx(bin, cv2.MORPH_OPEN, kernel, iterations=iters)
    removed_px = int(np.sum(bin != opened))
    bin_clean = opened
    return cv2.bitwise_not(bin_clean), removed_px


def pipeline(img, method="cc"):
    cropped, crop, _ = crop_edge_clean(img, band=15)
    lft = crop["left"]
    cw, ch = cropped.shape[1], cropped.shape[0]
    gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
    if method == "cc":
        clean_gray, n_removed, n_v, n_d = preprocess_cc(gray)
        extra = f"删竖线={n_v} 删噪点={n_d}"
    elif method == "open_v":
        clean_gray, n_removed = preprocess_morph_open(gray, se=(1, 25))
        extra = f"结构元=横(1x25)"
    elif method == "open_h":
        clean_gray, n_removed = preprocess_morph_open(gray, se=(25, 1))
        extra = f"结构元=纵(25x1)"
    else:
        raise ValueError(method)
    clean_rgb = cv2.cvtColor(clean_gray, cv2.COLOR_GRAY2RGB)
    raw = lc._detect_text_lines(clean_rgb)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = lc._merge_nearby(filt, gap=8) if filt else []
    blocks = lc._compute_blocks(clean_rgb, merged, cw) if merged else []
    return blocks, cw, ch, lft, n_removed, extra


def main() -> None:
    img = np.asarray(Image.open(SRC / f"page_{N:04d}.png").convert("RGB"),
                     dtype=np.uint8)
    # 当前（库内，无预处理）
    db = json.loads((Path(__file__).parent / "crop_master.json").read_text())
    p = [x for x in db if x["n"] == N][0]
    cur_blocks = [tuple(b) for b in p["cv2_blocks"]]
    cur_lv, cur_tup = user_odd_left_v6(cur_blocks, p["cw"], p["ch"])

    print(f"=== page {N} 预处理前后对比（边裁 band=15）===")
    print(f"当前(库, 无预处理) 块数={len(cur_blocks)}  B={cur_tup[1]}  v6左={cur_lv}  分支={cur_tup[4]}")

    for method in ("cc", "open_v", "open_h"):
        blocks, cw, ch, lft, n_removed, extra = pipeline(img, method)
        lv, tup = user_odd_left_v6(blocks, cw, ch)
        A, B, C, D, branch = tup
        print(f"\n--- {method} 预处理 ---  删除={n_removed} ({extra})")
        print(f"  块数={len(blocks)}  B={B}  C={C}  v6左={lv}  分支={branch}")
        left10 = sorted(blocks, key=lambda b: b[0])[:5]
        if left10:
            s = "  ".join(f"x1={x1:4d}/w={bw:4d}" for (x1, y1, x2, y2, bw) in left10)
            print(f"  最左5块: {s}")


if __name__ == "__main__":
    main()
