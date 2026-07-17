"""临时脚本：聚焦偶数页 cv2 版心裁剪，打印边界数值并生成标注图。

直接调用 _find_body_boundaries（隔离 cv2 路径，不受 PP-DocLayoutV3 短路影响），
对偶数页（侧眉在右）输出：
  - 终端：当前 cv2 边界 (t,b,l,r) + 裁后尺寸，以及 doclayout 真值对照与偏差；
  - 图：crop_preview/even_cv2/page_XXXX_cv2.png
        绿框=当前 cv2 保留区 / 红竖线=右侧裁切线 / 青框=doclayout 真值；
  - 裁后图：crop_preview/even_cv2/page_XXXX_cropped.png（含 _post_trim_borders 后处理）。

可调参数集中在顶部（与 layout_crop._find_body_boundaries 对应）：
  PADDING      = 行检测 padding
  RIGHT_PAD    = 偶数页右边界外扩量（+20）
  WIDE_RATIO   = 宽行判定阈值（行宽 > w*0.5）

用法：python _preview_even_cv2.py [页号...]   （默认 32 34 36 38 40）
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
    _post_trim_borders,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/even_cv2")
OUT.mkdir(parents=True, exist_ok=True)

PADDING = 10
RIGHT_PAD = 20          # 对应 _find_body_boundaries else 分支的 +20
WIDE_RATIO = 0.5        # 对应 w * 0.5 宽行阈值


def doclayout_box(img: np.ndarray):
    model = _get_doclayout_model()
    if model is None:
        return None
    try:
        results = list(model.predict(img, batch_size=1))
    except Exception:
        return None
    if not results:
        return None
    try:
        boxes = _extract_doclayout_boxes(results[0])
    except Exception:
        return None
    body = [b for b in boxes if b.get("label") in _BODY_LABELS]
    if not body:
        return None
    h, w = img.shape[:2]
    xs = [b["coordinate"][0] for b in body]
    ys = [b["coordinate"][1] for b in body]
    xe = [b["coordinate"][2] for b in body]
    ye = [b["coordinate"][3] for b in body]
    return (
        max(0, int(min(xs)) - 15),
        max(0, int(min(ys)) - 15),
        min(w, int(max(xe)) + 15),
        min(h, int(max(ye)) + 10),
    )


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or [32, 34, 36, 38, 40]
    rows = []
    for n in pages:
        if n % 2 == 1:
            print(f"[skip] 页 {n} 为奇数页（本脚本只看偶数页）")
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
        # 当前生产 cv2 偶数页路径
        top, bottom, left, right = _find_body_boundaries(img, merged, PADDING, page_num=n)
        dl = doclayout_box(img)

        print("=" * 78)
        print(f"PAGE {n:04d}  size={w}x{h}  侧眉在右(偶数页)")
        print(f"  cv2   (t,b,l,r)=({top},{bottom},{left},{right})  裁后={right-left}x{bottom-top}")
        if dl:
            print(f"  docl  (l,t,r,b)={dl}  裁后={dl[2]-dl[0]}x{dl[3]-dl[1]}")
            print(f"  偏差   l={left-dl[0]:+d} r={right-dl[2]:+d} t={top-dl[1]:+d} b={bottom-dl[3]:+d}")
        rows.append((n, w, h, left, right, right - left, top, bottom))

        # 标注图
        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        d.rectangle([left, top, right, bottom], outline=(0, 200, 0), width=4)   # cv2 保留区
        d.line([right, 0, right, h], fill=(220, 0, 0), width=3)                  # 右侧裁切线
        if dl:
            d.rectangle([dl[0], dl[1], dl[2], dl[3]], outline=(0, 200, 220), width=4)  # doclayout
        vis.save(OUT / f"page_{n:04d}_cv2.png")

        # 裁后图（含后处理）
        cropped = img[top:bottom, left:right].copy()
        cropped = _post_trim_borders(cropped)
        Image.fromarray(cropped).save(OUT / f"page_{n:04d}_cropped.png")

    print("\n" + "-" * 78)
    print(f"{'页':>4} {'原宽':>5} {'左':>5} {'右':>5} {'裁后宽':>6} {'上':>5} {'下':>5}")
    for n, w, h, l, r, cw, t, b in rows:
        print(f"{n:>4} {w:>5} {l:>5} {r:>5} {cw:>6} {t:>5} {b:>5}")
    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()
