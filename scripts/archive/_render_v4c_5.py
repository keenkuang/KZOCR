"""渲染 v4c 于 5 个原 v4b 过裁页 [83,143,197,227,247]，目视确认过裁是否消除。
改自 _render_v4b_5.py：用 user_odd_left_v4c 替代 user_odd_left_v4。
输出 crop_compare/abc_overcut/v4c_5/page_XXXX.png
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed
from _verify_v4c import user_odd_left_v4c

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_compare/abc_overcut/v4c_5")
OUT.mkdir(parents=True, exist_ok=True)
PAGES = [83, 143, 197, 227, 247]

MAX_DIM = 1600
BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
CV_LINE = (255, 30, 30)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)
CUTF_LINE = (230, 200, 0)
CUT_BLOCK = (255, 0, 0)

_FONT = None
for _fp in (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = ImageFont.truetype(_fp, 20)
        except Exception:
            _FONT = None
        break


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
        lv4, tup = user_odd_left_v4c(blocks, w, h)
        A, B, C, D, br = tup

        pil = Image.fromarray(img.copy())
        scale = min(1.0, MAX_DIM / max(w, h))
        if scale < 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)))
        d = ImageDraw.Draw(pil)
        s = lambda v: int(round(v * scale))
        wide_th = w * 0.5
        n_cut = 0
        for (lx, y1, rx, y2, bw) in blocks:
            color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
            d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=1)
            if lx < lv4 < rx:
                d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=CUT_BLOCK, width=3)
                n_cut += 1
        d.line([s(lv4), 0, s(lv4), h], fill=CV_LINE, width=3)
        if lf is not None:
            d.line([s(lf), 0, s(lf), h], fill=CUTF_LINE, width=2)
        d.line([s(dl_left), 0, s(dl_left), h], fill=DL_LINE, width=2)
        d.line([s(body_left), 0, s(body_left), h], fill=BODY_LINE, width=2)
        legend = [
            f"p{n:04d} 奇  v4c左={lv4}  cut_f左={lf}  dl_left={dl_left}  正文左缘={body_left}",
            f"分支={br}  过裁(正文)={lv4-body_left}px  被切文字行={n_cut}",
            f"A,B,C,D={A},{B},{C},{D}",
            "绿/橙=块 红框=被v4c切中的文字行 红=v4c 黄=cut_f 绿=dl 蓝=正文左缘",
        ]
        y = 6
        for line in legend:
            d.text((6, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=3,
                   stroke_fill=(255, 255, 255))
            y += 26
        out = OUT / f"page_{n:04d}.png"
        pil.save(out)
        print(f"p{n:04d}: v4c左={lv4} 正文左缘={body_left} 过裁={lv4-body_left}px 被切文字行={n_cut} → {out}")


if __name__ == "__main__":
    main()
