"""临时诊断：把 cv2 文字行检测的中间数据结构打印出来 + 叠加可视化。

用法：
    python _dump_layout.py            # 默认 page_0031(奇) 与 page_0032(偶)
    python _dump_layout.py 31 32 33   # 指定页码

输出：
    - 终端打印每一步的数据形态（行框、合并、宽/窄判定、版心边界）
    - crop_preview/page_XXXX_dump.png：原图叠加全部检测行框(宽=绿/窄=橙)+最终版心框(红)
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
    crop_by_layout,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def per_line_boundaries(img: np.ndarray, lines, w) -> list[dict]:
    """复刻 _find_body_boundaries 里逐行的左右边界分析，供查看数据形态。"""
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    out = []
    for idx, (x1, y1, x2, y2) in enumerate(lines):
        row_gray = gray[y1:y2, :]
        col_proj = np.mean(row_gray < 128, axis=0)
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
        out.append({"idx": idx, "y1": y1, "y2": y2, "lx": lx, "rx": rx, "bw": bw})
    return out


def dump_one(page_num: int) -> None:
    p = SRC / f"page_{page_num:04d}.png"
    if not p.exists():
        print(f"[跳过] 缺失: {p}")
        return
    img = load_rgb(p)
    h, w = img.shape[:2]
    print("=" * 78)
    print(f"PAGE {page_num:04d}  ({'奇数页·侧眉在左' if page_num % 2 == 1 else '偶数页·侧眉在右'})  size={w}x{h}")

    # 1) 原始行检测
    raw = _detect_text_lines(img)
    print(f"\n[1] _detect_text_lines 原始行框 count={len(raw)}  (x1,y1,x2,y2)")
    for b in raw[:8]:
        print(f"     {b}")
    if len(raw) > 8:
        print(f"     ... 共 {len(raw)} 行，仅显示前 8")

    # 2) 高度过滤
    filt = [(x1, y1, x2, y2) for x1, y1, x2, y2 in raw if y2 - y1 >= 8]
    print(f"\n[2] 高度过滤后 (y2-y1>=8) count={len(filt)}  （原 {len(raw)}）")

    # 3) 合并相邻行
    merged = _merge_nearby(filt, gap=8) if filt else []
    print(f"\n[3] _merge_nearby(gap=8) 合并后 count={len(merged)}")
    for b in merged[:8]:
        print(f"     {b}")
    if len(merged) > 8:
        print(f"     ... 共 {len(merged)} 行，仅显示前 8")

    # 4) 逐行左右边界 + 宽/窄判定
    pl = per_line_boundaries(img, merged, w)
    wide_mask = [d["bw"] > w * 0.5 for d in pl]
    print(f"\n[4] 逐行左右边界 & 宽/窄判定 (宽行 = bw > w*0.5 = {w * 0.5:.0f}px)")
    print(f"     {'idx':>3} {'y1':>5} {'y2':>5} {'lx':>5} {'rx':>5} {'bw':>5} {'wide':>5}")
    for d, wd in zip(pl, wide_mask):
        print(f"     {d['idx']:>3} {d['y1']:>5} {d['y2']:>5} {d['lx']:>5} {d['rx']:>5} {d['bw']:>5} {str(wd):>5}")
    n_wide = sum(wide_mask)
    print(f"     宽行数={n_wide}  窄行数={len(pl) - n_wide}")

    # 5) 最终版心边界
    top, bottom, left, right = _find_body_boundaries(img, merged, padding=10, page_num=page_num)
    print(f"\n[5] _find_body_boundaries 最终版心 (top,bottom,left,right) = ({top},{bottom},{left},{right})")
    print(f"     裁后尺寸 = {right - left} x {bottom - top}  (原 {w} x {h})")

    # 可视化：原图叠加检测行框 + 最终版心框
    vis = Image.fromarray(img.copy())
    d = ImageDraw.Draw(vis)
    for (x1, y1, x2, y2), wd in zip(merged, wide_mask):
        color = (0, 180, 0) if wd else (255, 140, 0)  # 宽=绿 窄=橙
        d.rectangle([x1, y1, x2, y2], outline=color, width=2)
    d.rectangle([left, top, right, bottom], outline=(220, 0, 0), width=4)  # 版心红框
    vis.save(OUT / f"page_{page_num:04d}_dump.png")
    print(f"     已保存叠加图: {OUT / f'page_{page_num:04d}_dump.png'}")


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or [31, 32]
    for n in pages:
        dump_one(n)


if __name__ == "__main__":
    main()
