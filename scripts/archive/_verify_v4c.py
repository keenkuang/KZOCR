"""v4c 全量验证：fallback 无 C 时 B-margin 从 15→50，消除 5 页过裁。

v4b 根因：5 过裁页全部 fallback 且 C=None → 退化为 B-15；
cv2 检测的正文左缘 B 比 dl 偏右 30–47px（检测遗漏）。
修复：fallback 中 C=None 时 left = max(B-50, D+60 if D else 0)；C 存在时不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")


def user_odd_left_v4c(blocks, w, h):
    """v4c: fallback 无 C 时 B-margin 从 15→50."""
    A = B = C = D = None
    A_vals, B_vals, C_vals, D_vals = [], [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if y1 < 0.06 * h and y2 < 0.15 * h:
            if x1 > 0.05 * w:
                A_vals.append(x1)
            elif x1 < 0.05 * w:
                D_vals.append(x1)
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

    if B is None:
        return None, (A, B, C, D)

    c60 = (C + 60) if C is not None else float("-inf")
    d_term = (D + 60) if D is not None else 0

    if A is not None:
        left = max(min(A, B) - 40, c60); branch = "A"
    elif D is not None and C is not None and (B - C) > 0.08 * w:
        left = min((D + C) / 2 + 80, B - 100); branch = "D-trim"
    elif C is not None:
        left = max(B - 15, C + 50); branch = "fallback_C"
    else:
        # C=None：无侧眉检测到，保守 B-margin=50（覆盖 cv2/dl ~40px 分歧）
        left = max(B - 50, d_term); branch = "fallback_noC"

    return int(left), (A, B, C, D, branch)


def main() -> None:
    n_odd = dl_miss = 0
    v4_cut_dl = []
    v4_cut_body = []
    cutf_cut_body = 0
    trim_ok = trim_fail = no_eyebrow = 0
    branches = {}

    for n in range(22, 993):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists() or n % 2 == 0:
            continue
        n_odd += 1
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        dl = lc._doclayout_rect(img)
        if dl is None:
            dl_miss += 1
            continue
        dl_left = dl[0]
        body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
        eyebrow_right = None
        try:
            res = list(lc._get_doclayout_model().predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
            se = [b["coordinate"][2] for b in boxes if b.get("label") == "aside_text"]
            if se:
                eyebrow_right = max(se)
        except Exception:
            pass
        raw = lc._detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        if not merged:
            continue
        blocks = lc._compute_blocks(img, merged, w)
        if not blocks:
            continue

        lf, _ = user_odd_left_fixed(blocks, w, h)
        lv4, tup = user_odd_left_v4c(blocks, w, h)
        if lf is None or lv4 is None:
            continue
        branches[tup[-1]] = branches.get(tup[-1], 0) + 1
        if lf > body_left:
            cutf_cut_body += 1
        if lv4 > dl_left:
            v4_cut_dl.append(n)
        if lv4 > body_left:
            v4_cut_body.append(n)
        if eyebrow_right is not None:
            if lv4 > eyebrow_right:
                trim_ok += 1
            else:
                trim_fail += 1
        else:
            no_eyebrow += 1
        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇数页  v4c过裁(dl)={len(v4_cut_dl)} 过裁(正文)={len(v4_cut_body)}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"[v4c] 分支分布: {branches}")
    print(f"[v4c] 过裁(dl)={len(v4_cut_dl)}  页={v4_cut_dl}")
    print(f"[v4c] 过裁(正文/真切)={len(v4_cut_body)}  页={v4_cut_body}")
    print(f"[cut_f] 过裁(正文/真切)={cutf_cut_body}")
    print(f"[v4c] 侧眉裁掉={trim_ok}  残留={trim_fail}  无侧眉(dl)={no_eyebrow}")
    print(f"\n[v4b 对照] 过裁(dl)=104 过裁(正文)=5 侧眉裁掉=455 残留=11")
    print(f"[cut_f对照] 过裁(正文)=1 侧眉裁掉=447 残留=19")


if __name__ == "__main__":
    main()
