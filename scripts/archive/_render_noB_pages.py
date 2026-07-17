"""渲染新 B 定义下 B=None 的页（156 页），看为何找不到 B。

在裁后图上：
  - 全部 cv2 块（绿=宽 / 橙=窄）
  - 黄色边框 = 新 B 判定区 (0.07w<x1<0.12w, 0.5h<y1<0.85h)，应为空
  - 红色边框 = 旧 B 区 (x1>0.07w, 0.15h<y1<0.85h) 内有块的左缘带，对照为何新定义落空
  - 参考线：绿=dl左缘 蓝=正文左缘 橙=侧眉右缘（均裁后坐标）
输出：crop_compare/noB_pages/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
DB = Path(__file__).parent / "crop_master.json"
OUT = Path("crop_compare/noB_pages")
OUT.mkdir(parents=True, exist_ok=True)

_FONT = None
for _fp in (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = ImageFont.truetype(_fp, 30)
        except Exception:
            _FONT = None
        break

BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)
EB_LINE = (255, 140, 0)
NEW_REGION = (255, 220, 0)
OLD_REGION = (255, 60, 60)
OLD_B_LINE = (180, 0, 180)


def new_B_none(blocks, w, h):
    bv = [x1 for (x1, y1, x2, y2, bw) in blocks
          if 0.07 * w < x1 < 0.12 * w and 0.5 * h < y1 < 0.85 * h]
    return not bv


def old_B_val(blocks, w, h):
    bv = [x1 for (x1, y1, x2, y2, bw) in blocks
          if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h]
    return min(bv) if bv else None


def main() -> None:
    db = json.loads(DB.read_text())
    cnt = 0
    for p in db:
        blocks = [tuple(b) for b in p["cv2_blocks"]]
        cw, ch = p["cw"], p["ch"]
        if blocks and not new_B_none(blocks, cw, ch):
            continue  # 新定义下有 B，跳过
        n = p["n"]
        lft = p["lft"]
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        cropped, crop, _ = crop_edge_clean(img, band=15)
        h, w = cropped.shape[:2]
        pil = Image.fromarray(cropped.copy())
        MAX_DIM = 1600
        scale = min(1.0, MAX_DIM / max(w, h))
        if scale < 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)))
        d = ImageDraw.Draw(pil)
        s = lambda v: int(round(v * scale))

        wide_th = w * 0.5
        n_new = n_old = 0
        for (lx, y1, rx, y2, bw) in blocks:
            color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
            d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=3)
            if 0.07 * w < lx < 0.12 * w and 0.5 * h < y1 < 0.85 * h:
                n_new += 1
            if lx > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
                n_old += 1

        # 新 B 区（黄框）
        d.rectangle([s(0.07 * w), s(0.5 * h), s(0.12 * w), s(0.85 * h)],
                    outline=NEW_REGION, width=4)
        # 旧 B 区左缘带（红框，仅左缘段示意）
        d.rectangle([s(0.07 * w), s(0.15 * h), s(0.12 * w), s(0.85 * h)],
                    outline=OLD_REGION, width=2)

        dl_left = p["dl_left"]
        body_left = p["body_left"]
        eb = p["eyebrow"]
        old_b = old_B_val(blocks, w, h)
        if dl_left is not None:
            d.line([s(dl_left - lft), 0, s(dl_left - lft), h], fill=DL_LINE, width=3)
        if body_left is not None:
            d.line([s(body_left - lft), 0, s(body_left - lft), h], fill=BODY_LINE, width=3)
        if eb["n_boxes"]:
            d.line([s(eb["right"] - lft), 0, s(eb["right"] - lft), h], fill=EB_LINE, width=3)
        if old_b is not None:
            d.line([s(old_b), 0, s(old_b), h], fill=OLD_B_LINE, width=3)
        # 0.5h 水平线（新B高度带下界，诊断用）
        d.line([0, s(0.5 * h), w, s(0.5 * h)], fill=(255, 0, 255), width=5)
        d.text((8, s(0.5 * h) - 34), "0.5h", fill=(255, 0, 255),
               font=_FONT, stroke_width=3, stroke_fill=(255, 255, 255))

        lines = [
            f"p{n:04d} 奇  B=None(新定义)  w={cw} h={ch} lft={lft}  块数={len(blocks)}",
            f"新B区内块={n_new}  旧B区内块={n_old}  旧B左界={old_b}",
            f"dl_left={dl_left}  正文左缘={body_left}  侧眉右缘={eb['right']}",
            "黄框=新B区 红框=旧B区 紫=旧B左界 品红线=0.5h 绿=dl 蓝=正文 橙=侧眉",
        ]
        lh = 40
        y = int(pil.height - lh * len(lines) - 8)
        for line in lines:
            d.text((8, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=4,
                   stroke_fill=(255, 255, 255))
            y += lh
        pil.save(OUT / f"page_{n:04d}.png")
        cnt += 1
        if cnt % 50 == 0:
            print(f"[渲染] {cnt} 页", file=sys.stderr, flush=True)

    print(f"[完成] B=None 页渲染 {cnt} 张 → {OUT}")


if __name__ == "__main__":
    main()
