"""临时诊断：打印每一块的精确坐标 + 把块框叠加到原图。

用法：
    python _dump_blocks.py          # 默认 page_0031
    python _dump_blocks.py 31 32    # 指定页码

输出：
    - 终端打印全部块坐标（原始全宽行框 + 合并后的精确块 lx/y1/rx/y2/bw）
    - crop_preview/page_XXXX_blocks.png：原图叠加每个块框（宽=绿/窄=橙）+ 版心红框
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _find_body_boundaries,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def per_block(
    img: np.ndarray, lines: list[tuple], w: int
) -> list[tuple[int, int, int, int, int]]:
    """对每行做垂直投影，得到真实左右边界，返回 (lx,y1,rx,y2,bw)。"""
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    out: list[tuple[int, int, int, int, int]] = []
    for (x1, y1, x2, y2) in lines:
        row = gray[y1:y2, :]
        col_proj = np.mean(row < 128, axis=0)
        lx = 0
        for cx in range(w):
            if col_proj[cx] > 0.01:
                lx = cx
                break
        rx = w
        for cx in range(w - 1, -1, -1):
            if col_proj[cx] > 0.01:
                rx = cx
                break
        bw = rx - lx if lx < rx else 0
        out.append((lx, y1, rx, y2, bw))
    return out


def dump_one(page_num: int) -> None:
    p = SRC / f"page_{page_num:04d}.png"
    if not p.exists():
        print(f"[skip] {p}")
        return
    img = load_rgb(p)
    h, w = img.shape[:2]
    print("=" * 84)
    print(
        f"PAGE {page_num:04d}  "
        f"({'奇数页·侧眉在左' if page_num % 2 == 1 else '偶数页·侧眉在右'})  "
        f"size={w}x{h}"
    )

    raw = _detect_text_lines(img)
    print(f"\n[A] 原始行框 count={len(raw)}  (x1,y1,x2,y2)")
    for i, b in enumerate(raw):
        print(f"  {i:>2} {b}")

    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = _merge_nearby(filt, gap=8) if filt else []
    wide_th = w * 0.5
    blocks = per_block(img, merged, w)
    print(
        f"\n[B] 合并后精确块 count={len(blocks)}  (lx,y1,rx,y2)  bw  "
        f"wide(bw>{wide_th:.0f})"
    )
    for i, (lx, y1, rx, y2, bw) in enumerate(blocks):
        print(
            f"  {i:>2}  lx={lx:>4} y1={y1:>4} rx={rx:>4} y2={y2:>4} "
            f"bw={bw:>4} {'W' if bw > wide_th else 'n'}"
        )

    top, bottom, left, right = _find_body_boundaries(
        img, merged, padding=10, page_num=page_num
    )
    print(
        f"\n[C] 版心 (top,bottom,left,right)=({top},{bottom},{left},{right})  "
        f"裁后={right - left}x{bottom - top}"
    )

    vis = Image.fromarray(img.copy())
    d = ImageDraw.Draw(vis)
    for (lx, y1, rx, y2, bw) in blocks:
        color = (0, 180, 0) if bw > wide_th else (255, 140, 0)
        d.rectangle([lx, y1, rx, y2], outline=color, width=2)
    d.rectangle([left, top, right, bottom], outline=(220, 0, 0), width=4)
    out_path = OUT / f"page_{page_num:04d}_blocks.png"
    vis.save(out_path)
    print(f"\n[图] 块框叠加已保存: {out_path}")


def main() -> None:
    for n in ([int(x) for x in sys.argv[1:]] or [31]):
        dump_one(n)


if __name__ == "__main__":
    main()
