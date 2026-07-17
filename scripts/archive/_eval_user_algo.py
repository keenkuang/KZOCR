"""评估用户给的「奇数页左侧眉」算法：用 dl 真值验证安全性与裁切效果。

块来自 _compute_blocks（逐行块，未合并），含 (lx,y1,rx,y2,bw)。
A: x1>0.05w 且 y1<0.06h 且 y2<0.15h  → min(x1)
B: x1>0.07w 且 0.15h<y1<0.85h       → mean(x1)
C: 0.02w<x1<0.06w 且 0.15h<y1<0.85h → mean(x1)
left = max(min(A,B)-40, C+60)  [A 不存在时 left=max(B-40, C+60)]
"""
from __future__ import annotations

import statistics
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc

SRC = "/home/keen/Documents/OCR0625/mi-by-ppocrv6/images"


def user_odd_left(blocks, w, h):
    A = B = C = None
    A_vals, B_vals, C_vals = [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if x1 > 0.05 * w and y1 < 0.06 * h and y2 < 0.15 * h:
            A_vals.append(x1)
        if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
            B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    if B_vals:
        B = statistics.mean(B_vals)
    if C_vals:
        C = statistics.mean(C_vals)
    if A_vals:
        A = min(A_vals)
    if B is None:
        return None, (A, B, C)
    c_term = C + 60 if C is not None else float("-inf")
    if A is not None:
        left = max(min(A, B) - 40, c_term)
    else:
        left = max(B - 40, c_term)
    return int(left), (A, B, C)


def user_odd_left_fixed(blocks, w, h):
    """修正版：B 用 min(x1)（正文最左缘）而非 mean；A 用原始逐行块。"""
    A = B = C = None
    A_vals, B_vals, C_vals = [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if x1 > 0.05 * w and y1 < 0.06 * h and y2 < 0.15 * h:
            A_vals.append(x1)
        if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
            B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    if B_vals:
        B = min(B_vals)  # 修正：最左正文缘
    if C_vals:
        C = statistics.mean(C_vals)
    if A_vals:
        A = min(A_vals)
    if B is None:
        return None, (A, B, C)
    c_term = C + 60 if C is not None else float("-inf")
    if A is not None:
        left = max(min(A, B) - 40, c_term)
    else:
        left = max(B - 40, c_term)
    return int(left), (A, B, C)


def user_odd_left_v2(blocks, w, h):
    """用户新公式原样：B=mean，C+50，封顶 B-15。"""
    A = B = C = None
    A_vals, B_vals, C_vals = [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if x1 > 0.05 * w and y1 < 0.06 * h and y2 < 0.15 * h:
            A_vals.append(x1)
        if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
            B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    if B_vals:
        B = statistics.mean(B_vals)
    if C_vals:
        C = statistics.mean(C_vals)
    if A_vals:
        A = min(A_vals)
    if B is None:
        return None, (A, B, C)
    c_term = C + 50 if C is not None else float("-inf")
    inner = max(min(A, B) - 40, c_term) if A is not None else max(B - 40, c_term)
    left = min(inner, B - 15)
    return int(left), (A, B, C)


def user_odd_left_v3(blocks, w, h):
    """B=min 版：用户结构，B 改 min，C+50，封顶 B_min-15。"""
    A = B = C = None
    A_vals, B_vals, C_vals = [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if x1 > 0.05 * w and y1 < 0.06 * h and y2 < 0.15 * h:
            A_vals.append(x1)
        if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
            B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    if B_vals:
        B = min(B_vals)
    if C_vals:
        C = statistics.mean(C_vals)
    if A_vals:
        A = min(A_vals)
    if B is None:
        return None, (A, B, C)
    c_term = C + 50 if C is not None else float("-inf")
    inner = max(min(A, B) - 40, c_term) if A is not None else max(B - 40, c_term)
    left = min(inner, B - 15)
    return int(left), (A, B, C)


def user_odd_left_v4(blocks, w, h):
    """用户修订版 v4：
    - 顶部区(y1<0.06h,y2<0.15h) 拆 A(x1>0.05w)=min 与 D(x1<0.05w)=min
    - B=min(x1)  for x1>0.07w, 0.15h<y1<0.85h   (正文最左缘)
    - C=mean(x1) for 0.02w<x1<0.06w, 0.15h<y1<0.85h  (左侧眉)
    - A 存在:                       left = max(min(A,B)-40, C+60)
    - D 存在 且 B-C>0.1w (侧眉清晰分离): left = C+100   (裁侧眉)
    - 否则兜底:                     left = max(B-15, C+50)
    """
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
        C = statistics.mean(C_vals)
    if A_vals:
        A = min(A_vals)
    if D_vals:
        D = min(D_vals)
    if B is None:
        return None, (A, B, C, D)
    c60 = C + 60 if C is not None else float("-inf")
    if A is not None:
        left = max(min(A, B) - 40, c60)
        branch = "A"
    elif D is not None and C is not None and (B - C) > 0.08 * w:
        left = min((D + C) / 2 + 80, B - 100)
        branch = "D-trim"
    else:
        left = max(B - 15, C + 50 if C is not None else float("-inf"))
        branch = "fallback"
    return int(left), (A, B, C, D, branch)


def main() -> None:
    n_odd = 0
    computable = 0
    cut = 0          # 原算法 left > 正文左缘 (过裁)
    cut_f = 0        # 修正版(B=min无封顶) left > 正文左缘
    cut_v2 = 0       # v2(用户原样 B=mean, C+50, B-15封顶)
    cut_v3 = 0       # v3(B=min, C+50, B_min-15封顶)
    trim_ok = 0
    trim_ok_f = 0
    trim_ok_v3 = 0
    trim_fail = 0
    trim_fail_v3 = 0
    no_eyebrow = 0
    cut_f_pages, cut_v2_pages, cut_v3_pages = [], [], []
    for n in range(22, 993):
        p = Path(f"{SRC}/page_{n:04d}.png")
        if not p.exists():
            continue
        if n % 2 == 0:
            continue  # 仅奇数页
        n_odd += 1
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        model = lc._get_doclayout_model()
        if model is None:
            continue
        try:
            res = list(model.predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
        except Exception:
            continue
        body_boxes = [b for b in boxes if b.get("label") in lc._BODY_LABELS]
        if not body_boxes:
            continue
        xs = [b["coordinate"][0] for b in body_boxes]
        dl_left = max(0, int(min(xs)) - lc._DOC_LAYOUT_PAD_LR_T)
        body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
        eyebrow_right = None
        se = [b["coordinate"][2] for b in boxes if b.get("label") == "aside_text"]
        if se:
            eyebrow_right = max(se)
        raw = lc._detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8)
        blocks = lc._compute_blocks(img, merged, w)
        if not blocks:
            continue
        left, _ = user_odd_left(blocks, w, h)
        left_f, _ = user_odd_left_fixed(blocks, w, h)
        left_v2, _ = user_odd_left_v2(blocks, w, h)
        left_v3, _ = user_odd_left_v3(blocks, w, h)
        if left is None or left_f is None or left_v2 is None or left_v3 is None:
            continue
        computable += 1
        if left > body_left:
            cut += 1
        if left_f > body_left:
            cut_f += 1
            cut_f_pages.append(n)
        if left_v2 > body_left:
            cut_v2 += 1
            cut_v2_pages.append(n)
        if left_v3 > body_left:
            cut_v3 += 1
            cut_v3_pages.append(n)
        if eyebrow_right is not None:
            if left > eyebrow_right:
                trim_ok += 1
            else:
                trim_fail += 1
            if left_v3 > eyebrow_right:
                trim_ok_v3 += 1
            else:
                trim_fail_v3 += 1
        else:
            no_eyebrow += 1

    print(f"奇数页总数(抽样): {n_odd}  可计算: {computable}")
    print(f"[原算法 B=mean]        过裁={cut}")
    print(f"[修正版 B=min 无封顶]   过裁={cut_f}  页={cut_f_pages}")
    print(f"[v2 用户原样 B=mean]    过裁={cut_v2}  页={cut_v2_pages}")
    print(f"[v3 B=min+封顶B-15]    过裁={cut_v3}  页={cut_v3_pages}  侧眉裁掉={trim_ok_v3} 残留={trim_fail_v3} 无侧眉={no_eyebrow}")


if __name__ == "__main__":
    main()
