"""构建版心裁剪主数据库（一次跑完，之后公式实验秒级复用）。

每奇数页存：
  n, h, w
  lft, crop          : 边裁量（band=15）
  cw, ch             : 裁后图尺寸（块坐标基于此）
  dl_left, body_left : dl 真值（原图坐标）
  eyebrow            : dl 检测到的 aside_text 框全集 + 几何（左/右/宽/高覆盖）
  cv2_blocks         : 裁后图上全部 cv2 文字块 [x1,y1,x2,y2,bw]
  ABCD               : 标准定义下预计算的 A/B/C/D（便于快速复核）
dl 真值 + cv2 检测是瓶颈（~40min），存盘后任意公式只需读块重算。

输出：crop_master.json（单文件主数据库）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
BAND = 15
OUT = Path(__file__).parent / "crop_master.json"


def compute_abcd(blocks, w, h):
    A = B = C = D = None
    A_vals, B_vals, C_vals, D_vals = [], [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if y1 < 0.06 * h and y2 < 0.15 * h:
            if x1 > 0.05 * w:
                A_vals.append(x1)
            elif x1 < 0.05 * w:
                D_vals.append(x1)
        if 0.07 * w < x1 < 0.12 * w and 0.5 * h < y1 < 0.85 * h:
            B_vals.append(x1)
    if not B_vals:
        for (x1, y1, x2, y2, bw) in blocks:
            if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
                B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    if B_vals:
        B = min(B_vals)
    if C_vals:
        C = sum(C_vals) / len(C_vals)
    if A_vals:
        A = min(A_vals)
    if D_vals:
        D = min(D_vals)
    return A, B, C, D


def main() -> None:
    db = []
    n_odd = dl_miss = 0
    for n in range(22, 993):
        if n % 2 == 0 or not (SRC / f"page_{n:04d}.png").exists():
            continue
        n_odd += 1
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        h, w = img.shape[:2]

        # --- dl 真值 ---
        dl = lc._doclayout_rect(img)
        dl_left = body_left = None
        eyebrow = {"boxes": [], "left": None, "right": None, "width": None,
                   "top": None, "bot": None, "height_cov": None,
                   "n_boxes": 0, "box_w_min": None, "box_w_mean": None,
                   "box_w_max": None}
        if dl is None:
            dl_miss += 1
        else:
            dl_left = dl[0]
            body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
            try:
                res = list(lc._get_doclayout_model().predict(img, batch_size=1))
                boxes = lc._extract_doclayout_boxes(res[0])
                aside = [b for b in boxes if b.get("label") == "aside_text"]
                if aside:
                    coords = [list(b["coordinate"]) for b in aside]
                    xs1 = [c[0] for c in coords]
                    xs2 = [c[2] for c in coords]
                    ys1 = [c[1] for c in coords]
                    ys2 = [c[3] for c in coords]
                    ws = [x2 - x1 for x1, x2 in zip(xs1, xs2)]
                    eyebrow = {
                        "boxes": coords,
                        "left": min(xs1), "right": max(xs2),
                        "width": max(xs2) - min(xs1),
                        "top": min(ys1), "bot": max(ys2),
                        "height_cov": (max(ys2) - min(ys1)) / h,
                        "n_boxes": len(coords),
                        "box_w_min": min(ws), "box_w_mean": round(sum(ws) / len(ws), 1),
                        "box_w_max": max(ws),
                    }
            except Exception:
                pass

        # --- 边裁 + cv2 块 ---
        cropped, crop, _ = crop_edge_clean(img, band=BAND)
        lft = crop["left"]
        cw, ch = cropped.shape[1], cropped.shape[0]
        raw = lc._detect_text_lines(cropped)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        blocks = lc._compute_blocks(cropped, merged, cw) if merged else []
        A, B, C, D = compute_abcd(blocks, cw, ch)

        db.append({
            "n": n, "h": h, "w": w,
            "lft": lft, "crop": crop, "cw": cw, "ch": ch,
            "dl_left": dl_left, "body_left": body_left,
            "eyebrow": eyebrow,
            "cv2_blocks": [[round(x1, 1), round(y1, 1), round(x2, 1),
                            round(y2, 1), round(bw, 1)]
                           for (x1, y1, x2, y2, bw) in blocks],
            "ABCD": [A, B,
                     (None if C is None else round(C, 1)), D],
        })
        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇  有侧眉页={sum(1 for p in db if p['eyebrow']['n_boxes'])}",
                  file=sys.stderr, flush=True)

    OUT.write_text(json.dumps(db, ensure_ascii=False))
    with_eb = sum(1 for p in db if p["eyebrow"]["n_boxes"])
    tot_blocks = sum(len(p["cv2_blocks"]) for p in db)
    print(f"\n=== 主数据库完成 奇数页={n_odd}  dl缺失={dl_miss}  "
          f"有侧眉页={with_eb}  总块数={tot_blocks} ===")
    print(f"输出: {OUT}  ({OUT.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
