"""渲染 A/B/C 奇数页左侧眉算法的「过裁页」为 CV2 BLOCK 图。

对每页奇数页：
  1. 用 PP-DocLayoutV3 求 dl_left（= pp-doclayoutv3 左界，含 -15 padding）
  2. 用 cv2 路径求块(_compute_blocks)，套用 A/B/C 三个版本求 cv2_left
  3. 过裁判据（用户定义）：cv2_left > dl_left
  4. 过裁页渲染 BLOCK 图：块框 + cv2 左界红线 + dl 左界绿线 + 正文左缘(dl_left+15)蓝虚线 + 数值标注

三个版本：
  cut_f : B=min 无封顶            → 你上一轮看到的「9 页过裁」
  v2    : 你提的 B=mean, C+50, 封顶 B-15
  v3    : B=min, C+50, 封顶 B_min-15（我的修正建议）

用法(限 8 核后台):
    taskset -c 0-7 env OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 \
        python _render_abc_overcut.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _eval_user_algo import user_odd_left_fixed, user_odd_left_v2, user_odd_left_v3

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_compare/abc_overcut")
for sub in ("cutf", "v2", "v3"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)

MAX_DIM = 1600
BLOCK_WIDE_COLOR = (0, 170, 60)     # 绿 = 宽块
BLOCK_NARROW_COLOR = (255, 140, 0)  # 橙 = 窄块
CV_LINE = (255, 30, 30)             # 红 = cv2 左界
DL_LINE = (20, 200, 20)             # 绿 = dl 左界
BODY_LINE = (30, 120, 255)          # 蓝 = 正文左缘(dl_left+15)

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


def render_one(
    img: np.ndarray,
    page_num: int,
    blocks: list[tuple],
    left_val: int,
    dl_left: int,
    sub: str,
    tag: str,
    ABC,
) -> Path:
    h, w = img.shape[:2]
    pil = Image.fromarray(img.copy())
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(pil)

    def s(v: float) -> int:
        return int(round(v * scale))

    wide_th = w * 0.5
    for (lx, y1, rx, y2, bw) in blocks:
        color = BLOCK_WIDE_COLOR if bw > wide_th else BLOCK_NARROW_COLOR
        d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=1)
    # 左界线
    d.line([s(left_val), 0, s(left_val), h], fill=CV_LINE, width=3)
    d.line([s(dl_left), 0, s(dl_left), h], fill=DL_LINE, width=3)
    d.line([s(dl_left + 15), 0, s(dl_left + 15), h], fill=BODY_LINE, width=2)

    body_left = dl_left + 15
    over = left_val - dl_left
    over_body = left_val - body_left
    legend = [
        f"p{page_num:04d} 奇  左={left_val}  dl_left={dl_left}  正文左缘={body_left}",
        f"过裁(dl)={over}px  过裁(正文)={over_body}px  {tag}",
        f"A,B,C={ABC}",
        "绿框=块 红=cv2左 绿线=dl左 蓝线=正文左缘",
    ]
    y = 6
    for line in legend:
        d.text((6, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=3,
               stroke_fill=(255, 255, 255))
        y += 26

    out_path = OUT / sub / f"page_{page_num:04d}.png"
    pil.save(out_path)
    return out_path


def main() -> None:
    cutf_pages, v2_pages, v3_pages = [], [], []
    cutf_paths, v2_paths, v3_paths = [], [], []
    n_odd = 0
    dl_miss = 0

    for n in range(22, 993):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        if n % 2 == 0:
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

        raw = lc._detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        if not merged:
            continue
        blocks = lc._compute_blocks(img, merged, w)
        if not blocks:
            continue

        left_f, abc_f = user_odd_left_fixed(blocks, w, h)
        left_v2, abc_v2 = user_odd_left_v2(blocks, w, h)
        left_v3, abc_v3 = user_odd_left_v3(blocks, w, h)
        if left_f is None or left_v2 is None or left_v3 is None:
            continue

        # 过裁判据：cv2_left > dl_left（用户定义）
        if left_f > dl_left:
            cutf_pages.append(n)
            cutf_paths.append(render_one(img, n, blocks, left_f, dl_left, "cutf", "cut_f(B=min无封顶)", abc_f))
        if left_v2 > dl_left:
            v2_pages.append(n)
            v2_paths.append(render_one(img, n, blocks, left_v2, dl_left, "v2", "v2(用户B=mean)", abc_v2))
        if left_v3 > dl_left:
            v3_pages.append(n)
            v3_paths.append(render_one(img, n, blocks, left_v3, dl_left, "v3", "v3(B=min+封顶)", abc_v3))

        if (len(cutf_pages) + len(v2_pages) + len(v3_pages)) % 50 == 0:
            print(f"[进度] 已扫 {n_odd} 奇数页  "
                  f"cut_f过裁={len(cutf_pages)} v2过裁={len(v2_pages)} v3过裁={len(v3_pages)}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 扫描奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"\n[cut_f B=min无封顶] 过裁={len(cutf_pages)} 页={cutf_pages}")
    print(f"[v2 用户B=mean]     过裁={len(v2_pages)} 页={v2_pages}")
    print(f"[v3 B=min+封顶]     过裁={len(v3_pages)} 页={v3_pages}")
    print(f"\n输出目录: {OUT}")
    print(f"  cutf → {OUT/'cutf'}  ({len(cutf_paths)} 张)")
    print(f"  v2   → {OUT/'v2'}    ({len(v2_paths)} 张)")
    print(f"  v3   → {OUT/'v3'}    ({len(v3_paths)} 张)")


if __name__ == "__main__":
    main()
