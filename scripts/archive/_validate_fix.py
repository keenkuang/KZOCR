"""校验修复：min-safe 标定(正文左缘内侧10px)，确认问题页不切正文、奇页裁侧眉，
并扫描前 200 页确认无过裁。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc

SRC = "/home/keen/Documents/OCR0625/mi-by-ppocrv6/images"
PAGES = {
    "偶-切正文": [24, 44, 54, 68, 74, 116, 142, 150, 164, 166, 172, 180, 182, 198],
    "奇-留侧眉": [23, 51, 71, 99, 115, 199],
    "偶-右侧眉": [90],
}


def _cv_left_for(n: int) -> tuple[int | None, int | None, float, int, bool]:
    img = np.asarray(Image.open(f"{SRC}/page_{n:04d}.png").convert("RGB"), dtype=np.uint8)
    dl = lc._doclayout_rect(img)
    dl_left = dl[0] if dl else None
    raw = lc._detect_text_lines(img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = lc._merge_nearby(filt, gap=8) if filt else []
    blocks = lc._compute_blocks(img, merged, img.shape[1])
    dt = lc._diff_term(blocks)
    parity = 1 if n % 2 == 1 else 0
    calib = lc._cv2_calib[parity]
    cv_left = dt + calib
    cut = (dl_left is not None) and (cv_left > dl_left + 15)
    return dl_left, cv_left, dt, calib, cut


def main() -> None:
    lc.reset_cv2_calib()
    warm, wn = [], []
    for i in range(lc._CV2_CALIB_SAMPLE // 2):
        for base in (22, 23):
            n = base + 16 * i
            import pathlib
            if pathlib.Path(f"{SRC}/page_{n:04d}.png").exists():
                warm.append(np.asarray(Image.open(f"{SRC}/page_{n:04d}.png").convert("RGB"), dtype=np.uint8))
                wn.append(n)
    lc.calibrate_cv2_left(warm, page_nums=wn)
    print(f"新标定 calib = {lc._cv2_calib} (BODY_MARGIN={lc._CV2_CALIB_BODY_MARGIN}) 锁定={lc._CV2_CALIB_LOCKED}\n")

    print("=== 问题页复核 ===")
    bad = 0
    for label, pages in PAGES.items():
        for n in pages:
            dl_left, cv_left, dt, calib, cut = _cv_left_for(n)
            parity = 1 if n % 2 == 1 else 0
            eyebrow_ok = (parity == 0) or (cv_left > 92)
            flag = ""
            if cut:
                flag += " CUT!"; bad += 1
            if not eyebrow_ok:
                flag += " 侧眉残留"
            print(f"{label:8s} p{n:04d} {'奇' if parity else '偶'} dl_left={dl_left} "
                  f"diff={dt:6.1f} calib={calib} cv_left={cv_left:5.0f}{flag}")
    print(f"问题页切正文数: {bad}")

    print("\n=== 前 200 页(22..221)全量扫描过裁 ===")
    cut_all = 0
    for n in range(22, 222):
        import pathlib
        if not pathlib.Path(f"{SRC}/page_{n:04d}.png").exists():
            continue
        _, _, _, _, cut = _cv_left_for(n)
        if cut:
            cut_all += 1
            if cut_all <= 20:
                print(f"  过裁: p{n:04d}")
    print(f"前 200 页过裁总数: {cut_all}")


if __name__ == "__main__":
    main()
