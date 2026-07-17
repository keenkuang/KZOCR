"""v4b 全量计算（不渲染）：D-trim 改为 B-C>0.08w, left=min((D+C)/2+80, B-100)。
仅统计过裁(dl/正文)与侧眉裁切率，不写图片。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed, user_odd_left_v4

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")


def main() -> None:
    n_odd = 0
    dl_miss = 0
    v4_cut_dl = []
    v4_cut_body = []
    cutf_cut_body = 0
    trim_ok = 0
    trim_fail = 0
    no_eyebrow = 0
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
        lv4, tup = user_odd_left_v4(blocks, w, h)
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
            print(f"[进度] {n_odd} 奇数页  v4过裁(dl)={len(v4_cut_dl)} 过裁(正文)={len(v4_cut_body)}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"[v4b] 分支分布: {branches}")
    print(f"[v4b] 过裁(dl)={len(v4_cut_dl)}  页={v4_cut_dl}")
    print(f"[v4b] 过裁(正文/真切)={len(v4_cut_body)}  页={v4_cut_body}")
    print(f"[cut_f] 过裁(正文/真切)={cutf_cut_body}")
    print(f"[v4b] 侧眉裁掉={trim_ok}  残留={trim_fail}  无侧眉(dl)={no_eyebrow}")


if __name__ == "__main__":
    main()
