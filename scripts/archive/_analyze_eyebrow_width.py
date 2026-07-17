"""统计侧眉（aside_text）宽度与左右缘，来自 PP-DocLayoutV3 dl 真值。

对每奇数页：跑 dl → 取所有 label=aside_text 的框，记录每个框的
  x1, x2, 宽度 w=x2-x1, 高度 h=y2-y1。
汇总：
  - 全量 aside 框宽度分布（min/mean/max、分位数）
  - 每页侧眉左缘=min(x1)、右缘=max(x2)、该页宽度=右缘-左缘
  - 侧眉高度覆盖（max y2 - min y1）占页高比例
输出 per_page json 供复用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")


def main() -> None:
    widths = []
    per_page = []
    n_odd = 0
    for n in range(22, 993):
        if n % 2 == 0 or not (SRC / f"page_{n:04d}.png").exists():
            continue
        n_odd += 1
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        h, w = img.shape[:2]
        try:
            res = list(lc._get_doclayout_model().predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
        except Exception:
            boxes = []
        aside = [b for b in boxes if b.get("label") == "aside_text"]
        if not aside:
            per_page.append({"n": n, "has_eyebrow": False})
            continue
        xs1 = [b["coordinate"][0] for b in aside]
        xs2 = [b["coordinate"][2] for b in aside]
        ys1 = [b["coordinate"][1] for b in aside]
        ys2 = [b["coordinate"][3] for b in aside]
        ws = [x2 - x1 for x1, x2 in zip(xs1, xs2)]
        widths.extend(ws)
        left = min(xs1)
        right = max(xs2)
        top = min(ys1)
        bot = max(ys2)
        per_page.append({
            "n": n, "has_eyebrow": True,
            "left": left, "right": right, "width": right - left,
            "top": top, "bot": bot,
            "height_cov": (bot - top) / h,
            "n_boxes": len(aside),
            "box_w_min": min(ws), "box_w_max": max(ws),
            "box_w_mean": round(sum(ws) / len(ws), 1),
        })
        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇  有侧眉页={sum(1 for p in per_page if p['has_eyebrow'])}",
                  file=sys.stderr, flush=True)

    (Path(__file__).parent / "eyebrow_width_per_page.json").write_text(
        json.dumps(per_page, ensure_ascii=False))

    if widths:
        widths_arr = np.array(widths)
        with_eyebrow = [p for p in per_page if p["has_eyebrow"]]
        page_widths = [p["width"] for p in with_eyebrow]
        print(f"\n=== 侧眉宽度统计（dl aside_text，{n_odd} 奇页）===")
        print(f"有侧眉页: {len(with_eyebrow)}/{n_odd}")
        print(f"\n[单框宽度 x2-x1]  n={len(widths)}")
        print(f"  min={widths_arr.min():.0f}  mean={widths_arr.mean():.1f}  "
              f"median={np.median(widths_arr):.1f}  max={widths_arr.max():.0f}")
        for q in (5, 25, 50, 75, 95):
            print(f"  p{q}={np.percentile(widths_arr, q):.0f}")
        print(f"\n[整列侧眉宽度 = 右缘-左缘]  n={len(page_widths)}")
        pw = np.array(page_widths)
        print(f"  min={pw.min():.0f}  mean={pw.mean():.1f}  "
              f"median={np.median(pw):.1f}  max={pw.max():.0f}")
        print(f"\n[侧眉左缘 min(x1)]  range=[{min(p['left'] for p in with_eyebrow)}, "
              f"{max(p['left'] for p in with_eyebrow)}]")
        print(f"[侧眉右缘 max(x2)]  range=[{min(p['right'] for p in with_eyebrow)}, "
              f"{max(p['right'] for p in with_eyebrow)}]")
        hcov = np.array([p["height_cov"] for p in with_eyebrow])
        print(f"[侧眉高度覆盖占页高]  min={hcov.min():.2f}  "
              f"mean={hcov.mean():.2f}  max={hcov.max():.2f}")


if __name__ == "__main__":
    main()
