"""快检内侧(无侧眉)边的 diff 是否≈0, 验证"公式在内侧退化为常数"的论断。

内侧定义(用户约定): 奇数页侧眉在左 → 内侧=右; 偶数页侧眉在右 → 内侧=左。
  - 偶数页左(diff_l = mean(x1|x1>15) - mean(x1 全部))：内侧, 应≈0
  - 奇数页右(diff_r = mean(x2 全部) - mean(x2|x2<w-15))：内侧, 应≈0
纯 cv2, 不加载 doclayout, 快。
"""
from __future__ import annotations

import statistics as st
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine.layout_crop import _detect_text_lines, _merge_nearby
from _preview_even_formula import per_block

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")


def main():
    dl_, dr_ = [], []
    for n in range(22, 993):
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
        all_l = [b[0] for b in blocks]
        body_l = [x for x in all_l if x > 15]
        all_r = [b[2] for b in blocks]
        body_r = [x for x in all_r if x < w - 15]
        if n % 2 == 0:  # 偶数: 内侧=左
            if all_l and body_l:
                dl_.append(sum(body_l)/len(body_l) - sum(all_l)/len(all_l))
        else:            # 奇数: 内侧=右
            if all_r and body_r:
                dr_.append(sum(all_r)/len(all_r) - sum(body_r)/len(body_r))


    def desc(name, v):
        if not v:
            print(f"  {name}: 无"); return
        print(f"  {name}: min={min(v):+.1f} med={st.median(v):+.1f} max={max(v):+.1f} std={st.pstdev(v):.1f} n={len(v)}")

    print("=== 内侧(无侧眉)边 diff 分布 ===")
    desc("偶数页 内侧左 diff_l", dl_)
    desc("奇数页 内侧右 diff_r", dr_)


if __name__ == "__main__":
    main()
