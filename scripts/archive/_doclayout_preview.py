"""临时原型：在样本古籍页上跑 PP-DocLayoutV3，输出按类别着色的检测预览图。

用法：
    python _doclayout_preview.py 31          # 单页（验证 API）
    python _doclayout_preview.py 31 32 33    # 多页
    python _doclayout_preview.py             # 默认 31..40

输出 crop_preview/page_XXXX_doclayout.png：
    绿=正文/竖排/标题(版心组成)  橙=侧栏(aside)  红=页眉/页脚/页码  蓝=其他(table/image/公式)
    品红粗框 = body = union(text+vertical_text+doc_title+paragraph_title)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from paddlex import create_model

PAD_LR_T = 15  # 左/右/上 padding
PAD_B = 10     # 下 padding

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)

# 版心组成类别 / 侧栏 / 页眉页脚页码 / 其他
BODY = {"text", "vertical_text", "doc_title", "paragraph_title"}
ASIDE = {"aside_text"}
MARGIN = {"header", "header_image", "footer", "footer_image", "number"}
COLOR = {
    "body": (0, 180, 0),
    "aside": (255, 140, 0),
    "margin": (220, 0, 0),
    "other": (0, 120, 220),
}
LABEL_COLOR = {}
for _l in BODY:
    LABEL_COLOR[_l] = COLOR["body"]
for _l in ASIDE:
    LABEL_COLOR[_l] = COLOR["aside"]
for _l in MARGIN:
    LABEL_COLOR[_l] = COLOR["margin"]


def extract_boxes(res) -> list[dict]:
    """兼容 paddlex 不同版本的结果结构，抽出 boxes 列表。"""
    raw = res.json if hasattr(res, "json") else res
    if isinstance(raw, dict):
        if "boxes" in raw:
            return raw["boxes"]
        if "res" in raw and isinstance(raw["res"], dict) and "boxes" in raw["res"]:
            return raw["res"]["boxes"]
    if isinstance(raw, list):
        return raw
    raise RuntimeError(f"无法识别的预测结果结构: {type(raw)} -> {str(raw)[:200]}")


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or list(range(31, 41))
    print("加载 PP-DocLayoutV3 模型（首次会自动下载推理权重）...")
    model = create_model(model_name="PP-DocLayoutV3")

    for n in pages:
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            print(f"[跳过] 缺失: {p}")
            continue
        print(f"\n=== PAGE {n:04d} ===")
        t0 = time.perf_counter()
        results = list(model.predict(str(p), batch_size=1))
        boxes = extract_boxes(results[0])
        detect_s = time.perf_counter() - t0
        print(f"  检测框数={len(boxes)}  推理耗时={detect_s:.2f}s")
        # 统计各类别数量
        from collections import Counter
        cnt = Counter(b["label"] for b in boxes)
        print("  类别分布:", dict(cnt))

        # 计算 body 并集框
        body_boxes = [b for b in boxes if b["label"] in BODY]
        if body_boxes:
            xs = [b["coordinate"][0] for b in body_boxes]
            ys = [b["coordinate"][1] for b in body_boxes]
            xe = [b["coordinate"][2] for b in body_boxes]
            ye = [b["coordinate"][3] for b in body_boxes]
            body = (min(xs), min(ys), max(xe), max(ye))
        else:
            body = None
        if body:
            w, h = (Image.open(p).size)
            padded = (
                max(0, int(body[0]) - PAD_LR_T),
                max(0, int(body[1]) - PAD_LR_T),
                min(w, int(body[2]) + PAD_LR_T),
                min(h, int(body[3]) + PAD_B),
            )
            print(f"  body 版心框 = ({body[0]:.0f},{body[1]:.0f},{body[2]:.0f},{body[3]:.0f})")
            print(f"  +padding后  = ({padded[0]},{padded[1]},{padded[2]},{padded[3]})  "
                  f"尺寸 {padded[2]-padded[0]}x{padded[3]-padded[1]}")

        # 画预览图
        img = Image.open(p).convert("RGB")
        d = ImageDraw.Draw(img)
        for b in boxes:
            x1, y1, x2, y2 = [int(v) for v in b["coordinate"]]
            col = LABEL_COLOR.get(b["label"], COLOR["other"])
            d.rectangle([x1, y1, x2, y2], outline=col, width=2)
        if body:
            d.rectangle([padded[0], padded[1], padded[2], padded[3]],
                        outline=(220, 0, 220), width=5)
        out = OUT / f"page_{n:04d}_doclayout.png"
        img.save(out)
        print(f"  已保存预览图: {out}")
        if body:
            crop = img.crop((padded[0], padded[1], padded[2], padded[3]))
            crop.save(OUT / f"page_{n:04d}_doclayout_crop.png")
            print(f"  已保存裁切图: {OUT / f'page_{n:04d}_doclayout_crop.png'}")


if __name__ == "__main__":
    main()
