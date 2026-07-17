"""构建 inpainted 块数据库（条件 inpaint 后重检测）。
每页：边裁 → 检测左缘线 → 条件 inpaint → 重检测块 → 存 raw/inp 两套块与 A/B/C/D。
存到 crop_master_inpaint.json，供后续在 inpainted 域重调 v6 用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _verify_edge15_v5 import user_odd_left_v6
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
DB_PATH = Path(__file__).parent / "crop_master.json"
OUT = Path(__file__).parent / "crop_master_inpaint.json"
BAND_W = 25
COL_THRESH = 0.12
EDGE_R2_MIN = 0.60
EDGE_MEAN_MAX = 20
MASK_EXTRA = 8
INPAINT_RADIUS = 16


def detect_left_line(bin_img: np.ndarray):
    H, W = bin_img.shape
    col_fractions = bin_img[:, :BAND_W].sum(axis=1) / 255.0 / H
    has_col = bool((col_fractions > COL_THRESH).any())
    leftmost = np.zeros(H, dtype=int)
    for y in range(H):
        nz = np.where(bin_img[y, :BAND_W] > 0)[0]
        leftmost[y] = int(nz[0]) if len(nz) > 0 else BAND_W
    valid = leftmost < BAND_W
    has_edge = False
    if valid.sum() >= H * 0.3:
        ys = np.arange(H)[valid]
        xs = leftmost[valid].astype(float)
        A_mat = np.vstack([xs, np.ones(len(xs))]).T
        try:
            slope, _ = np.linalg.lstsq(A_mat, ys, rcond=None)[0]
            pred = slope * xs + _
            ss_res = ((ys - pred) ** 2).sum()
            ss_tot = ((ys - ys.mean()) ** 2).sum()
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            has_edge = bool(r2 >= EDGE_R2_MIN and float(xs.mean()) < EDGE_MEAN_MAX)
        except Exception:
            has_edge = False
    has = has_col or has_edge
    if not has:
        return False, None
    mask = np.zeros((H, W), dtype=np.uint8)
    for y in range(H):
        ex = min(leftmost[y] + MASK_EXTRA, W)
        mask[y, :ex] = 255
    return has, mask


def blocks_from(rgb_img, cw):
    raw = lc._detect_text_lines(rgb_img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = lc._merge_nearby(filt, gap=8) if filt else []
    return lc._compute_blocks(rgb_img, merged, cw) if merged else []


def main():
    t0 = time.time()
    db = json.loads(DB_PATH.read_text())
    odd = [p for p in db if p["n"] % 2 == 1]
    out = []
    for i, p in enumerate(odd):
        n = p["n"]
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"), dtype=np.uint8)
        cropped, crop_info, _ = crop_edge_clean(img, band=15)
        crop_left = int(crop_info.get("left", 0))
        cw, ch = cropped.shape[1], cropped.shape[0]
        gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
        _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # raw 块（仅边裁）
        blocks_raw = blocks_from(cropped, cw)
        abcd_raw = list(user_odd_left_v6(blocks_raw, cw, ch)[1])

        # 条件 inpaint
        has, mask = detect_left_line(bin_img)
        if has:
            inp = cv2.inpaint(cropped, mask, INPAINT_RADIUS, flags=cv2.INPAINT_TELEA)
            blocks_inp = blocks_from(inp, cw)
        else:
            blocks_inp = blocks_raw
        abcd_inp = list(user_odd_left_v6(blocks_inp, cw, ch)[1])

        out.append({
            "n": n, "cw": cw, "ch": ch, "h": p["h"], "w": p["w"],
            "crop_left": crop_left,
            "triggered": has,
            "dl_left": p.get("dl_left"), "body_left": p.get("body_left"),
            "eyebrow": p.get("eyebrow"),
            "blocks_raw": blocks_raw, "abcd_raw": abcd_raw,
            "blocks_inp": blocks_inp, "abcd_inp": abcd_inp,
        })
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(odd)}")
    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    print(f"完成 {len(out)} 页, 用时 {time.time()-t0:.0f}s, 存 {OUT}")


if __name__ == "__main__":
    main()
