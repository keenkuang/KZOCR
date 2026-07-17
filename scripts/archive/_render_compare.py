"""渲染全部 971 页（mi-by-ppocrv6），每页用两种版心框叠加对比：

- 红框  = PP-DocLayoutV3 版心框（完整镜像 crop_by_doclayout 的取框逻辑）
- 蓝框  = cv2 降级路径版心框（_detect_text_lines→_merge_nearby→_find_body_boundaries）

每页输出一张 PNG 到 crop_compare/page_XXXX.png（原图下采样到长边 1400，
两色框线宽 5，左上角图例标注奇偶/两框尺寸，便于逐页对比）。

用法(限 8 核后台):
    taskset -c 0-7 env OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 \
        python _render_compare.py
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
    _compute_blocks,
    _doclayout_rect,
    reset_cv2_calib,
)
from kzocr.engine import layout_crop as lc

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_compare")
OUT.mkdir(parents=True, exist_ok=True)

MAX_DIM = 1400
DL_COLOR = (255, 40, 40)    # 红 = doclayout
CV_COLOR = (0, 170, 255)    # 青蓝 = cv2
BOX_W = 5

# 图例字体：优先 DejaVu，缺失退回默认 bitmap
_FONT = None
for _fp in (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = __import__("PIL").ImageFont.truetype(_fp, 22)
        except Exception:
            _FONT = None
        break


def cv2_rect(img: np.ndarray, n: int):
    """cv2 降级路径版心框，返回 (left, top, right, bottom) 或 None。"""
    raw = _detect_text_lines(img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = _merge_nearby(filt, gap=8) if filt else []
    if not merged:
        return None
    top, bottom, left, right = _find_body_boundaries(img, merged, page_num=n)
    return (left, top, right, bottom)


def main() -> None:
    done = 0
    dl_ok = 0
    dl_miss = 0
    cv_miss = 0

    # 渲染前先对全部页以 dl 真值求每页「安全 calib 候选」并取各 parity 全局最小值
    # 锁定：保证 971 页中 cv2 左界无一过裁正文（min-safe 只在采样页安全，故必须全量）。
    # dl 推理结果缓存复用，避免主循环重复推理。
    reset_cv2_calib()
    dl_cache: dict[int, tuple[int, int, int, int] | None] = {}
    cands: dict[int, list[int]] = {1: [], 0: []}
    for n in range(22, 993):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        dl = _doclayout_rect(img)
        dl_cache[n] = dl
        if dl is None:
            continue
        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        if not merged:
            continue
        blocks = _compute_blocks(img, merged, img.shape[1])
        if not blocks:
            continue
        dt = lc._diff_term(blocks)
        parity = 1 if n % 2 == 1 else 0
        cand = int(max(0, min(200, dl[0] + (15 - lc._CV2_CALIB_BODY_MARGIN) - dt)))
        cands[parity].append(cand)
    if cands[1]:
        lc._cv2_calib[1] = min(cands[1])
    if cands[0]:
        lc._cv2_calib[0] = min(cands[0])
    lc._CV2_CALIB_LOCKED = True
    print(f"[标定] cv2 calib = {lc._cv2_calib} 锁定={lc._CV2_CALIB_LOCKED}",
          file=sys.stderr, flush=True)

    for n in range(22, 993):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]

        dl = dl_cache.get(n)
        cv = cv2_rect(img, n)

        pil = Image.fromarray(img)
        scale = min(1.0, MAX_DIM / max(w, h))
        if scale < 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)))
        d = ImageDraw.Draw(pil)

        def s(v: float) -> int:
            return int(round(v * scale))

        if dl is not None:
            d.rectangle([s(dl[0]), s(dl[1]), s(dl[2]), s(dl[3])],
                        outline=DL_COLOR, width=BOX_W)
            dl_ok += 1
        if cv is not None:
            d.rectangle([s(cv[0]), s(cv[1]), s(cv[2]), s(cv[3])],
                        outline=CV_COLOR, width=BOX_W)
        else:
            cv_miss += 1
        if dl is None:
            dl_miss += 1

        # 图例
        parity = "奇" if n % 2 == 1 else "偶"
        dl_s = f"({dl[0]},{dl[1]},{dl[2]},{dl[3]})" if dl else "N/A"
        cv_s = f"({cv[0]},{cv[1]},{cv[2]},{cv[3]})" if cv else "N/A"
        legend = [
            f"p{n:04d} {parity}",
            f"红=doclayout {dl_s}",
            f"蓝=cv2 {cv_s}",
        ]
        y = 8
        for line in legend:
            d.text((8, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=2,
                   stroke_fill=(255, 255, 255))
            y += 28

        pil.save(OUT / f"page_{n:04d}.png")
        done += 1
        if done % 50 == 0:
            print(f"[进度] {done} 页  DL可用={dl_ok} DL缺={dl_miss} cv2缺={cv_miss}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 n={done} ===")
    print(f"  doclayout 可用={dl_ok} / 缺失={dl_miss}")
    print(f"  cv2 缺失={cv_miss}")
    print(f"  输出目录: {OUT}")


if __name__ == "__main__":
    main()
