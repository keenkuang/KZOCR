"""临时脚本：对 page_0031~0040 跑版心裁切，生成对比图与边界数值。"""
from __future__ import annotations

import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _find_body_boundaries,
    crop_by_layout,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/crop_preview")
OUT.mkdir(parents=True, exist_ok=True)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


rows = []
for n in range(31, 41):
    p = SRC / f"page_{n:04d}.png"
    if not p.exists():
        print(f"跳过(缺失): {p}")
        continue
    img = load_rgb(p)
    h, w = img.shape[:2]

    # 走真实管线取裁切结果
    cropped = crop_by_layout(img, padding=10, page_num=n)
    # 单独取边界数值用于标注
    lines = _detect_text_lines(img)
    if lines:
        lines = [(x1, y1, x2, y2) for x1, y1, x2, y2 in lines if y2 - y1 >= 8]
    merged = _merge_nearby(lines, gap=8) if lines else []
    top, bottom, left, right = _find_body_boundaries(img, merged, padding=10, page_num=n)

    # 原图标注保留区域(绿框)
    box = Image.fromarray(img.copy())
    d = ImageDraw.Draw(box)
    d.rectangle([left, top, right, bottom], outline=(0, 200, 0), width=4)
    if n % 2 == 1:  # 奇数页：左缘裁切线(红)
        d.line([left, 0, left, h], fill=(220, 0, 0), width=3)
    else:           # 偶数页：右缘裁切线(红)
        d.line([right, 0, right, h], fill=(220, 0, 0), width=3)

    box.save(OUT / f"page_{n:04d}_box.png")
    if cropped is not None:
        Image.fromarray(cropped).save(OUT / f"page_{n:04d}_cropped.png")

    rows.append((n, w, h, left, right, right - left, top, bottom))

print(f"{'页':>4} {'原宽':>5} {'左':>5} {'右':>5} {'裁后宽':>6} {'上':>5} {'下':>5}  奇偶")
for n, w, h, l, r, cw, t, b in rows:
    print(f"{n:>4} {w:>5} {l:>5} {r:>5} {cw:>6} {t:>5} {b:>5}   {'奇' if n%2==1 else '偶'}")

print(f"\n输出目录: {OUT}")
