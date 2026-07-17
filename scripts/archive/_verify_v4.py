"""全量验证 v4 奇数页左侧眉公式，并与 cut_f 对比；渲染 v4 过裁页 BLOCK 图。

判据：
  - 过裁(dl)   : v4_left > dl_left
  - 过裁(正文) : v4_left > body_left (= dl_left + 15，真正切进文字)
  - 侧眉裁掉   : v4_left > eyebrow_right (aside_text 最大 x2)
输出：crop_compare/abc_overcut/v4/page_XXXX.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed, user_odd_left_v4

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_compare/abc_overcut/v4")
OUT.mkdir(parents=True, exist_ok=True)

MAX_DIM = 1600
BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
CV_LINE = (255, 30, 30)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)

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


def render(page_num, img, blocks, left_val, dl_left, abc):
    h, w = img.shape[:2]
    pil = Image.fromarray(img.copy())
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(pil)
    s = lambda v: int(round(v * scale))
    wide_th = w * 0.5
    for (lx, y1, rx, y2, bw) in blocks:
        color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
        d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=1)
    d.line([s(left_val), 0, s(left_val), h], fill=CV_LINE, width=3)
    d.line([s(dl_left), 0, s(dl_left), h], fill=DL_LINE, width=3)
    d.line([s(dl_left + 15), 0, s(dl_left + 15), h], fill=BODY_LINE, width=2)
    body_left = dl_left + 15
    over = left_val - dl_left
    over_body = left_val - body_left
    legend = [
        f"p{page_num:04d} 奇  v4左={left_val}  dl_left={dl_left}  正文左缘={body_left}",
        f"过裁(dl)={over}px  过裁(正文)={over_body}px  {abc[-1]}",
        f"A,B,C,D={abc[:4]}",
        "绿框=块 红=cv2左 绿线=dl左 蓝线=正文左缘",
    ]
    y = 6
    for line in legend:
        d.text((6, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=3,
               stroke_fill=(255, 255, 255))
        y += 26
    out = OUT / f"page_{page_num:04d}.png"
    pil.save(out)
    return out


def main() -> None:
    n_odd = 0
    dl_miss = 0
    v4_cut_dl = []
    v4_cut_body = []
    v4_paths = []
    cutf_cut_body = 0
    trim_ok = 0
    trim_fail = 0
    no_eyebrow = 0
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
        lv4, abc = user_odd_left_v4(blocks, w, h)
        if lf is None or lv4 is None:
            continue
        if lf > body_left:
            cutf_cut_body += 1
        if lv4 > dl_left:
            v4_cut_dl.append(n)
            v4_paths.append(render(n, img, blocks, lv4, dl_left, abc))
        if lv4 > body_left:
            v4_cut_body.append(n)
        if eyebrow_right is not None:
            if lv4 > eyebrow_right:
                trim_ok += 1
            else:
                trim_fail += 1
        else:
            no_eyebrow += 1
        if len(v4_cut_dl) % 50 == 0:
            print(f"[进度] 扫{n_odd}奇数页  v4过裁(dl)={len(v4_cut_dl)} 过裁(正文)={len(v4_cut_body)}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"\n[v4] 过裁(dl)={len(v4_cut_dl)}  页={v4_cut_dl}")
    print(f"[v4] 过裁(正文/真切)={len(v4_cut_body)}  页={v4_cut_body}")
    print(f"[cut_f] 过裁(正文/真切)={cutf_cut_body}")
    print(f"[v4] 侧眉裁掉={trim_ok}  残留={trim_fail}  无侧眉(dl)={no_eyebrow}")
    print(f"\n输出: {OUT}  ({len(v4_paths)} 张)")


if __name__ == "__main__":
    main()
