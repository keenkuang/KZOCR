"""排查: 偶数页中"所有检测行 x1<=15"(无 x1>15 block)的缺口页。

输出:
  - gap 页清单与统计(n_blocks 分布, 是否有宽行 x2>0.5w)
  - 2 个样例渲染: crop_preview/diag/page_NNNN_orig.png / _blocks.png
用法: python _diag_gap.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from kzocr.engine.layout_crop import _detect_text_lines, _merge_nearby
from _preview_even_formula import per_block

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/diag")
OUT.mkdir(parents=True, exist_ok=True)


def main():
    gap = []
    gap_stats = []
    examples = []
    for n in range(22, 993):
        if n % 2 != 0:
            continue
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        if not merged:
            continue
        blocks = per_block(img, merged, w)
        if not blocks:
            continue
        has_wide_x1 = any(b[0] > 15 for b in blocks)
        if has_wide_x1:
            continue
        # 缺口页: 所有 block x1<=15
        gap.append(n)
        n_wide = sum(1 for b in blocks if b[4] > w * 0.5)
        gap_stats.append((n, len(blocks), n_wide,
                          min(b[0] for b in blocks), max(b[2] for b in blocks)))
        if len(examples) < 2:
            examples.append((n, img, blocks, h, w))

    print(f"偶数页缺口(全 block x1<=15): {len(gap)} 页")
    print(f"  样例页号: {gap[:20]}{'...' if len(gap) > 20 else ''}")
    print(f"\n  缺口页统计 (页, n_blocks, 宽行数, min_x1, max_x2):")
    for s in gap_stats[:30]:
        print(f"    p{s[0]:>4} blocks={s[1]:>3} wide={s[2]:>3} min_x1={s[3]:>4} max_x2={s[4]:>4}")
    if gap_stats:
        import statistics as st
        nbs = [s[1] for s in gap_stats]
        print(f"  n_blocks: min={min(nbs)} med={st.median(nbs)} max={max(nbs)}")
        print(f"  含宽行(x2>0.5w)的缺口页: {sum(1 for s in gap_stats if s[2] > 0)}/{len(gap_stats)}")

    for n, img, blocks, h, w in examples:
        Image.fromarray(img.copy()).save(OUT / f"page_{n:04d}_orig.png")
        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        for b in blocks:
            color = (0, 200, 0) if b[4] > w * 0.5 else (230, 140, 0)
            d.rectangle([b[0], b[1], b[2], b[3]], outline=color, width=2)
        vis.save(OUT / f"page_{n:04d}_blocks.png")
        print(f"  已渲染样例 p{n:04d} -> {OUT}")


if __name__ == "__main__":
    main()
