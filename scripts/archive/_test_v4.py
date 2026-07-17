"""v4 公式秒级 dry-run：挑代表性奇数页，打印分支/left/A/B/C/D 与过裁对照。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed, user_odd_left_v4

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
PAGES = [23, 27, 39, 41, 199, 641, 913]


def main() -> None:
    for n in PAGES:
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            print(f"[skip] {p}")
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        dl = lc._doclayout_rect(img)
        if dl is None:
            print(f"p{n:04d}: dl 缺失")
            continue
        dl_left = dl[0]
        body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
        raw = lc._detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        blocks = lc._compute_blocks(img, merged, w) if merged else []
        if not blocks:
            print(f"p{n:04d}: 无块")
            continue
        lf, _ = user_odd_left_fixed(blocks, w, h)
        lv4, tup = user_odd_left_v4(blocks, w, h)
        A, B, C, D, br = tup
        over_dl = lv4 > dl_left
        over_body = lv4 > body_left
        print(f"p{n:04d} w={w} dl_left={dl_left} 正文左缘={body_left}")
        print(f"   A={A} B={B} C={C} D={D} 分支={br}")
        print(f"   cut_f left={lf}  v4 left={lv4}  | v4过裁(dl)={over_dl} 过裁(正文)={over_body}")
        print()


if __name__ == "__main__":
    main()
