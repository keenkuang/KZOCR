"""诊断：对问题页算 dl 左界 / cv2 左界 / 是否切到正文。

安全判定：dl_left = min(正文 x1) - 15，故正文真左缘 = dl_left + 15。
cv2_left > dl_left + 15 即切到正文（过裁）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _compute_blocks,
    _doclayout_rect,
)

SRC = "/home/keen/Documents/OCR0625/mi-by-ppocrv6/images"

PAGES = {
    "偶-切正文": [24, 44, 54, 68, 74, 116, 142, 150, 164, 166, 172, 180, 182, 198],
    "奇-留侧眉": [23, 51, 71, 99, 115, 199],
    "偶-右侧眉": [90],
}
SAFE = 10


def diff_term(blocks):
    all_x1 = [b[0] for b in blocks]
    body = [x for x in all_x1 if x > 15]
    m_all = sum(all_x1) / len(all_x1)
    m_body = sum(body) / len(body) if body else m_all
    return (m_body - m_all) / 2 - 15


def main() -> None:
    rows = []
    for label, pages in PAGES.items():
        for n in pages:
            p = f"{SRC}/page_{n:04d}.png"
            img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
            h, w = img.shape[:2]
            dl = _doclayout_rect(img)
            dl_left = dl[0] if dl else None
            raw = _detect_text_lines(img)
            filt = [b for b in raw if b[3] - b[1] >= 8]
            merged = _merge_nearby(filt, gap=8) if filt else []
            blocks = _compute_blocks(img, merged, w)
            dt = diff_term(blocks)
            parity = 1 if n % 2 == 1 else 0
            calib_cur = 102 if parity else 105
            calib_old = 105 if parity else 75
            cv_cur = dt + calib_cur
            cv_old = dt + calib_old
            cut_cur = (dl_left is not None) and (cv_cur > dl_left + 15)
            cut_old = (dl_left is not None) and (cv_old > dl_left + 15)
            # 安全 calib（保证 cv_left <= dl_left + SAFE）
            safe = (dl_left + SAFE - dt) if dl_left is not None else None
            rows.append((label, n, parity, dl_left, round(dt, 1),
                         round(cv_cur), cut_cur, round(cv_old), cut_old,
                         round(safe) if safe is not None else None))
            print(f"{label:8s} p{n:04d} {'奇' if parity else '偶'} "
                  f"dl_left={dl_left} diff={dt:6.1f} "
                  f"cv_cur={cv_cur:5.0f}{' CUT' if cut_cur else '   '} "
                  f"cv_old={cv_old:5.0f}{' CUT' if cut_old else '   '} "
                  f"safe_calib={safe}")

    # 按 parity 汇总安全 calib（取 min，保证所有采样页不切正文）
    print("\n=== 安全 calib（min over 诊断页，保证 cv_left<=dl_left+SAFE）===")
    for parity in (1, 0):
        vals = [r[9] for r in rows if r[2] == parity and r[9] is not None]
        if vals:
            print(f"  parity={parity} ({'奇' if parity else '偶'}): min safe_calib = {min(vals)}  "
                  f"(当前={102 if parity else 105}, 旧={105 if parity else 75})")


if __name__ == "__main__":
    main()
