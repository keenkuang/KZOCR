"""条件式 inpaint 预处理全量验证（485 奇数页）。
仅对「左缘检测到连续线/边缘线」的页执行 inpaint，跳过无线的页。

判据（任一满足即触发）：
  ① 左带(x<25) 任一列暗像素占比>12%（真·竖细线）
  ② 每行最左非白列拟合直线 R²>0.6 且均值<20（文字边缘形成的伪线）

输出：触发率 / 分支跳变率 / 过裁(正文) / 侧眉欠裁 / v6-left 分布变化。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from kzocr.engine import layout_crop as lc
from _verify_edge15_v5 import user_odd_left_v6
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
DB_PATH = Path(__file__).parent / "crop_master.json"
OUT_DIR = Path("crop_compare/inpaint_conditional")
BAND_W = 25          # 判据① 检测带宽
COL_THRESH = 0.12     # 判据① 列暗像素占比阈值
EDGE_R2_MIN = 0.60    # 判据② 边缘拟合 R² 下限
EDGE_MEAN_MAX = 20    # 判据② 边缘平均 x 上限
MASK_EXTRA = 8        # mask 在左边缘外多扩 px
INPAINT_RADIUS = 16


def detect_left_line(bin_img: np.ndarray) -> tuple[bool, np.ndarray | None]:
    """检测左带是否有连续线/边缘线。返回(has_line, mask)。"""
    H, W = bin_img.shape
    # --- 判据①：高占比列（真·细线） ---
    col_fractions = bin_img[:, :BAND_W].sum(axis=1) / 255.0 / H
    has_col = bool((col_fractions > COL_THRESH).any())

    # --- 判据②：边缘拟合（文字块左缘） ---
    leftmost = np.zeros(H, dtype=int)
    for y in range(H):
        nz = np.where(bin_img[y, :BAND_W] > 0)[0]
        leftmost[y] = int(nz[0]) if len(nz) > 0 else BAND_W
    # 只考虑确实有内容的行（leftmost<BAND_W 的行）
    valid = leftmost < BAND_W
    if valid.sum() < H * 0.3:
        has_edge = False
    else:
        ys = np.arange(H)[valid]
        xs = leftmost[valid].astype(float)
        A_mat = np.vstack([xs, np.ones(len(xs))]).T
        try:
            slope, _ = np.linalg.lstsq(A_mat, ys, rcond=None)[0]
            pred = slope * xs + _
            ss_res = ((ys - pred) ** 2).sum()
            ss_tot = ((ys - ys.mean()) ** 2).sum()
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            has_edge = bool(r2 >= EDGE_R2_MIN and float(xs.mean()) < EDGE_MEAN_MAX)
        except Exception:
            has_edge = False

    has = has_col or has_edge

    if not has:
        return False, None

    # 生成 mask：每行从左边到 leftmost+MASK_EXTRA 标为要修复
    mask = np.zeros((H, W), dtype=np.uint8)
    for y in range(H):
        ex = min(leftmost[y] + MASK_EXTRA, W)
        mask[y, :ex] = 255
    return has, mask


def run_one(page: dict) -> dict | None:
    """单页：检测→条件 inpaint→重算 v6。返回对比字典或 None（未触发）。"""
    n = page["n"]
    img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"), dtype=np.uint8)
    cropped, crop_info, _ = crop_edge_clean(img, band=15)
    cw, ch = cropped.shape[1], cropped.shape[0]
    gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    has, mask = detect_left_line(bin_img)
    if not has:
        return None

    # inpaint
    inpainted = cv2.inpaint(cropped, mask, INPAINT_RADIUS, flags=cv2.INPAINT_TELEA)

    # 重检测
    raw = lc._detect_text_lines(inpainted)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = lc._merge_nearby(filt, gap=8) if filt else []
    blocks = lc._compute_blocks(inpainted, merged, cw) if merged else []

    cur_lv, cur_tup = user_odd_left_v6(
        [tuple(b) for b in page["cv2_blocks"]], page["cw"], page["ch"]
    )
    new_lv, new_tup = user_odd_left_v6(blocks, cw, ch)

    body_l = page.get("body_left")
    dl_l = page.get("dl_left")

    return {
        "n": n,
        "cur_lv": cur_lv, "cur_tup": list(cur_tup),
        "new_lv": new_lv, "new_tup": list(new_tup),
        "body_left": body_l, "dl_left": dl_l,
        "blocks_after": len(blocks),
    }


def main() -> None:
    t0 = time.time()
    db = json.loads(DB_PATH.read_text())
    odd_pages = [p for p in db if p["n"] % 2 == 1]
    print(f"共 {len(odd_pages)} 奇数页，开始条件式 inpaint 全量验证...")
    print(f"判据: ①列>{COL_THRESH*100:.0f}%  ②边缘R²>{EDGE_R2_MIN} 且均值<{EDGE_MEAN_MAX}")

    triggered = []
    skipped = []
    results = []

    for i, page in enumerate(odd_pages):
        ret = run_one(page)
        if ret is None:
            skipped.append(page["n"])
        else:
            triggered.append(page["n"])
            results.append(ret)
        if (i + 1) % 50 == 0:
            print(f"  进度 {i+1}/{len(odd_pages)}  触发={len(triggered)}  跳过={len(skipped)}")

    dt = time.time() - t0
    print(f"\n=== 全量完成 ({dt:.1f}s) ===")
    print(f"总页数={len(odd_pages)}  触发={len(triggered)}({len(triggered)/len(odd_pages):.1%})  跳过={len(skipped)}")

    if not results:
        print("无触发页，无需统计。")
        return

    # ---- 统计（全部加 None 保护） ----
    def over_cnt(key_lv, key_ref):
        c = 0
        for r in results:
            lv, ref = r.get(key_lv), r.get(key_ref)
            if lv is not None and ref is not None and lv > ref:
                c += 1
        return c

    over_cur = over_cnt("cur_lv", "body_left")
    over_new = over_cnt("new_lv", "body_left")
    dl_over_cur = over_cnt("cur_lv", "dl_left")
    dl_over_new = over_cnt("new_lv", "dl_left")

    branch_jump = sum(1 for r in results
                      if r["cur_tup"][4] != r["new_tup"][4])

    lv_diffs = [r["new_lv"] - r["cur_lv"]
                for r in results
                if r["new_lv"] is not None and r["cur_lv"] is not None]

    print(f"\n--- 触发页效果 ---")
    print(f"过裁(正文/真切): {over_cur}→{over_new} ({over_new-over_cur:+d})")
    print(f"过裁(dl宽松):   {dl_over_cur}→{dl_over_new} ({dl_over_new-dl_over_cur:+d})")
    print(f"分支跳变数:      {branch_jump}/{len(results)}({branch_jump/len(results):.1%})")
    if lv_diffs:
        d = np.array(lv_diffs)
        print(f"v6-left 变化:     均值={d.mean():+.1f}  中位={np.median(d):+.0f}  "
              f"范围=[{d.min():+d},{d.max():+d}]")
        print(f"  v6左增加(右移/更安全)={sum(1 for d in lv_diffs if d>0)}  "
              f"减少(左移/更激进)={sum(1 for d in lv_diffs if d<0)}  不变={sum(1 for d in lv_diffs if d==0)}")

    # 跳过分支详情
    if branch_jump > 0:
        print(f"\n--- 分支跳变详情(TOP15) ---")
        jumps = [(r["n"], r["cur_tup"][4], r["new_tup"][4],
                  r["cur_lv"], r["new_lv"])
                 for r in results
                 if r["cur_tup"][4] != r["new_tup"][4]
                 and r["cur_lv"] is not None and r["new_lv"] is not None]
        jumps.sort(key=lambda x: abs(x[4] - x[3]), reverse=True)
        for n, cb, nb, clv, nlv in jumps[:15]:
            print(f"  p{n:4d}: {cb}→{nb}  v6左 {clv}→{nlv} ({nlv-clv:+d})")

    # 存结果
    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "conditional_results.json"
    json.dump({"triggered": triggered, "results": results,
               "summary": {"total": len(odd_pages), "n_triggered": len(triggered),
                           "n_skipped": len(skipped),
                           "over_body_cur": over_cur, "over_body_new": over_new,
                           "over_dl_cur": dl_over_cur, "over_dl_new": dl_over_new,
                           "branch_jump": branch_jump}},
              open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n结果已存: {out_path}")


if __name__ == "__main__":
    main()
