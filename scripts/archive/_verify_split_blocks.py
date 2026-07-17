"""分块逻辑改写原型 v2（2026-07-14，全局聚合修正）。

上一版 per-line 聚合踩坑：逐行算 wide 遇正文被 gap 拆成窄 run 就退化回含侧眉的 min；
skip_leftrun 逐行跳过最左窄 run，但很多行无侧眉可跳反而过裁。

本版改全局聚合：收齐所有行的 run，全局候选：
  - min_all            : 全局 min(run x1) - 15            （含侧眉）
  - wide{g}            : 全局 min(run x1 | 宽>0.3w) - 15    （跳窄侧眉）
  - exeb{g}            : 全局 min(run x1 | 非 窄(<0.1w)且远左(<0.15w)) - 15 （排除侧眉 run）
试 gap=15 / 30 两种分裂间隙。dl 真值量过裁(dl)/过裁(正文)/侧眉裁掉，与 v4b 对照。
边裁预处理(crop_edge_clean)先跑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
PAD = 15
GAPS = [15, 30]
WIDE_RATIO = 0.3
NARROW_RATIO = 0.1
FARLEFT_RATIO = 0.15


def split_runs(row_gray, trim_left, w, gap, thr=0.01):
    col_proj = np.mean(row_gray < 128, axis=0)
    dark = col_proj > thr
    dc = np.where(dark)[0]
    runs = []
    if len(dc) == 0:
        return runs
    start = prev = int(dc[0])
    for c in dc[1:]:
        if c - prev > gap:
            runs.append((start, prev))
            start = int(c)
        prev = int(c)
    runs.append((start, prev))
    return [(int(lx), int(rx)) for (lx, rx) in runs]


def main() -> None:
    n_odd = dl_miss = 0
    edge_trig = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    border_persist = []
    # 每个候选：过裁(dl), 过裁(正文), 侧眉裁掉, 残留
    stats = {k: {"dl": [], "body": [], "ok": 0, "fail": 0}
             for k in ["min_all", "wide15", "wide30", "exeb15", "exeb30"]}
    no_eb = 0

    for n in range(22, 993):
        if n % 2 == 0 or not (SRC / f"page_{n:04d}.png").exists():
            continue
        n_odd += 1
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        h, w = img.shape[:2]
        dl = lc._doclayout_rect(img)
        if dl is None:
            dl_miss += 1
            continue
        dl_left = dl[0]
        body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
        eyebrow_right = None
        try:
            res = list(lc._get_doclayout_model().predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
            se = [b["coordinate"][2] for b in boxes if b.get("label") == "aside_text"]
            if se:
                eyebrow_right = max(se)
        except Exception:
            pass

        cropped, crop, bp = crop_edge_clean(img)
        lft = crop["left"]
        for e in ("top", "bottom", "left", "right"):
            if crop[e]:
                edge_trig[e] += 1
        if bp is not None and lft:
            border_persist.append((n, bp))

        cw = cropped.shape[1]
        gray = np.mean(cropped, axis=2) if cropped.ndim == 3 else cropped
        tl = 0
        for x in range(min(12, cw)):
            if np.mean(gray[:, x] < 128) > 0.9:
                tl = x + 1
            else:
                break
        raw = lc._detect_text_lines(cropped)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []

        all_runs = {g: [] for g in GAPS}
        if merged:
            for x1, y1, x2, y2 in merged:
                row_gray = gray[y1:y2, :]
                for g in GAPS:
                    all_runs[g].extend(split_runs(row_gray, tl, cw, g))

        def cand(runs, mode):
            if not runs:
                return None
            if mode == "min_all":
                xs = [lx for (lx, rx) in runs]
            elif mode.startswith("wide"):
                xs = [lx for (lx, rx) in runs if (rx - lx + 1) > WIDE_RATIO * cw]
                if not xs:
                    xs = [lx for (lx, rx) in runs]
            elif mode.startswith("exeb"):
                xs = [lx for (lx, rx) in runs
                      if not ((rx - lx + 1) < NARROW_RATIO * cw and lx < FARLEFT_RATIO * cw)]
                if not xs:
                    xs = [lx for (lx, rx) in runs]
            return int(max(0, min(xs) - PAD))

        vals = {
            "min_all": cand(all_runs[15], "min_all"),
            "wide15": cand(all_runs[15], "wide15"),
            "wide30": cand(all_runs[30], "wide30"),
            "exeb15": cand(all_runs[15], "exeb15"),
            "exeb30": cand(all_runs[30], "exeb30"),
        }

        body_left_c = body_left - lft
        dl_left_c = dl_left - lft
        eb_c = (eyebrow_right - lft) if eyebrow_right is not None else None

        for k, v in vals.items():
            if v is None:
                continue
            if v > dl_left_c:
                stats[k]["dl"].append(n)
            if v > body_left_c:
                stats[k]["body"].append(n)
        if eb_c is not None:
            for k, v in vals.items():
                if v is None:
                    continue
                if v > eb_c:
                    stats[k]["ok"] += 1
                else:
                    stats[k]["fail"] += 1
        else:
            no_eb += 1

        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇数页  左裁={edge_trig['left']}  残留竖线={len(border_persist)}",
                  file=sys.stderr, flush=True)

    print(f"\n=== 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"[边裁] 触发={edge_trig}  残留竖线页数={len(border_persist)}")
    for k in ["min_all", "wide15", "wide30", "exeb15", "exeb30"]:
        s = stats[k]
        print(f"[{k}] 过裁(dl)={len(s['dl'])} 过裁(正文)={len(s['body'])} "
              f"侧眉裁掉={s['ok']} 残留={s['fail']}")
    print(f"[无侧眉(dl)={no_eb}]")
    print(f"\n[v4b 对照] 过裁(dl)=104 过裁(正文)=5 侧眉裁掉=455 残留=11")


if __name__ == "__main__":
    main()
