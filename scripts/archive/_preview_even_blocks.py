"""临时脚本：偶数页 cv2 文字块(block)可视化。

对 31-40 的偶数页（侧眉在右），用与生产 cv2 路径一致的检测：
  _detect_text_lines → 过滤过矮行(>=8) → _merge_nearby(gap=8)
逐块做垂直投影求真实左右边界，按 w*0.5 分宽/窄行，绘制：
  - 绿框 = 宽行(参与 right 边界，版心正文)
  - 橙框 = 窄行(右侧侧眉/竖排，被宽行过滤排除)
  - 青框 = 最终 cv2 保留区；红竖线 = 右侧裁切线
并在每个块旁标注 bw(行宽) 与 wide 判定，方便调参。

输出：crop_preview/even_cv2/blocks/page_XXXX_blocks.png
用法：python _preview_even_blocks.py [页号...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _find_body_boundaries,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/even_cv2/blocks")
OUT.mkdir(parents=True, exist_ok=True)

WIDE_RATIO = 0.5   # 与 _find_body_boundaries 一致：行宽 > w*0.5 视为宽行


def per_block(img: np.ndarray, lines: list, w: int) -> list[dict]:
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    out = []
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
        out.append({"x1": lx, "y1": y1, "x2": rx, "y2": y2,
                    "bw": bw, "wide": bw > w * WIDE_RATIO})
    return out


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or [p for p in range(31, 41) if p % 2 == 0]
    for n in pages:
        if n % 2 == 1:
            continue
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            print(f"[skip] 缺失: {p}")
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]

        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        blocks = per_block(img, merged, w)
        top, bottom, left, right = _find_body_boundaries(img, merged, 10, page_num=n)

        wide = [b for b in blocks if b["wide"]]
        narrow = [b for b in blocks if not b["wide"]]
        print("=" * 78)
        print(f"PAGE {n:04d}  size={w}x{h}  块数={len(blocks)} 宽行={len(wide)} 窄行={len(narrow)}")
        print(f"  宽行 rx_max+20 = {max((b['x2'] for b in wide), default=0)+20}  → right={right}")
        print(f"  窄行 x_min 列表 = {[b['x1'] for b in narrow][:8]}")

        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        for b in blocks:
            color = (0, 200, 0) if b["wide"] else (230, 140, 0)
            d.rectangle([b["x1"], b["y1"], b["x2"], b["y2"]], outline=color, width=2)
            if font is not None:
                d.text((b["x1"], max(0, b["y1"] - 12)),
                       f"bw={b['bw']}{'*' if b['wide'] else ''}", fill=color, font=font)
        # 最终版心 + 裁切线
        d.rectangle([left, top, right, bottom], outline=(0, 200, 220), width=4)
        d.line([right, 0, right, h], fill=(220, 0, 0), width=3)
        outp = OUT / f"page_{n:04d}_blocks.png"
        vis.save(outp)
        print(f"  已保存: {outp}  (绿=宽行 橙=窄行 青=版心 红=右裁切线；*表示宽行)")

    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()
