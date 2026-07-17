"""临时诊断：同页 cv2 版心框 vs PP-DocLayoutV3 版心框 并排对比。

用法：
    python _compare_layout.py 31
    python _compare_layout.py 31 32

输出：
    - 终端打印两种版心坐标与裁后尺寸
    - crop_preview/page_XXXX_compare.png：原图叠加 红=cv2 / 青=PP-DocLayoutV3 版心框
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
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def doclayout_crop_box(img: np.ndarray) -> tuple[int, int, int, int] | None:
    model = _get_doclayout_model()
    if model is None:
        return None
    results = list(model.predict(img, batch_size=1))
    if not results:
        return None
    boxes = _extract_doclayout_boxes(results[0])
    body = [b for b in boxes if b.get("label") in _BODY_LABELS]
    if not body:
        return None
    h, w = img.shape[:2]
    xs = [b["coordinate"][0] for b in body]
    ys = [b["coordinate"][1] for b in body]
    xe = [b["coordinate"][2] for b in body]
    ye = [b["coordinate"][3] for b in body]
    left = max(0, int(min(xs)) - 15)
    top = max(0, int(min(ys)) - 15)
    right = min(w, int(max(xe)) + 15)
    bottom = min(h, int(max(ye)) + 10)
    return (left, top, right, bottom)


def compare_one(page_num: int) -> None:
    p = SRC / f"page_{page_num:04d}.png"
    if not p.exists():
        print(f"[skip] {p}")
        return
    img = load_rgb(p)
    h, w = img.shape[:2]
    print("=" * 84)
    print(f"PAGE {page_num:04d}  size={w}x{h}  cv2 vs PP-DocLayoutV3 版心对比")

    raw = _detect_text_lines(img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = _merge_nearby(filt, gap=8) if filt else []
    cv2_box = _find_body_boundaries(img, merged, padding=10, page_num=page_num)
    dl_box = doclayout_crop_box(img)
    cv2_top, cv2_bottom, cv2_left, cv2_right = cv2_box

    print(f"\n  cv2 版心      (top,bottom,left,right) = {cv2_box}")
    print(f"  cv2 裁后尺寸  = {cv2_bottom - cv2_top} x {cv2_right - cv2_left}")
    if dl_box is None:
        print("  doclayout     = 不可用（paddlex 未安装/模型加载失败）")
    else:
        print(f"  doclayout 版心(top,bottom,left,right) = {dl_box}")
        print(f"  doclayout 裁后= {dl_box[3] - dl_box[1]} x {dl_box[2] - dl_box[0]}")

    vis = Image.fromarray(img.copy())
    d = ImageDraw.Draw(vis)
    d.rectangle([cv2_left, cv2_top, cv2_right, cv2_bottom], outline=(220, 0, 0), width=4)  # 红 = cv2
    if dl_box is not None:
        dl, dt, dr, db = dl_box
        d.rectangle([dl, dt, dr, db], outline=(0, 200, 220), width=4)  # 青 = doclayout
    out_path = OUT / f"page_{page_num:04d}_compare.png"
    vis.save(out_path)
    print(f"\n[图] 对比叠加已保存: {out_path}  (红=cv2 / 青=PP-DocLayoutV3)")


def main() -> None:
    for n in ([int(x) for x in sys.argv[1:]] or [31]):
        compare_one(n)


if __name__ == "__main__":
    main()
