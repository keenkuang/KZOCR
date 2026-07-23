#!/usr/bin/env python3
"""真实书端到端验证（设计文档 §6 #7）。

用两个真实本地 OCR 引擎对真实扫描古籍逐页识别，喂给生产级 `run_cross_align`
做 token 级模糊比对，提取分歧（数字/剂量 + 形近黑名单 high 优先），落库并校验
Web/REST 暴露与形近字黑名单自学习。

  - 引擎A：PaddleOCR（PP-OCRv6，CPU，无需密钥）
  - 引擎B：OvisOCR2-Q4_KM（onnxruntime，CPU，无需密钥）

**全流程含版心裁切**：每页先经生产级 `_crop_to_body`（与 orchestrator.py:62 一致：
PP-DocLayoutV3 优先 + cv2 三级降级）裁掉页眉/页脚/侧眉/页码等版心外噪声，再交给
OCR 引擎识别——与生产流水线前端对齐，而非整页识别（整页会引入大量版心外噪声）。

说明：完整 `orchestrate_book` 的跨引擎比对挂在 Tier1(PaddleOCR) vs Tier3(本地 LLM)
失败路径上，而本机无 GPU / 无云端密钥，故这里直接驱动跨引擎校验栈的核心
（真实引擎 + run_cross_align + BookDB + Web/REST + 自学习），等价验证端到端能力。

依赖（本机已装）：paddleocr、rapidocr_onnxruntime；可选 paddlex（PP-DocLayoutV3，
未装则自动降级 cv2）。
用法：
  python scripts/e2e_cross_engine_realbook.py <pdf> \
      [--book-code X] [--db-dir D] [--dpi 150] \
      [--start-page 1] [--end-page N] [--no-crop]
  # 页码为 1-indexed（书页号），含端点；默认全本。--no-crop 走整页对照。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import fitz
import numpy as np

# ── 生产代码（被验证对象）──
from kzocr.scheduler.cross_align import (
    run_cross_align,
    load_confusion_set,
    reload_confusion_set,
)
from kzocr.storage.db import BookDB
from kzocr.engine.types import GlyphVerdict, PageInput
from kzocr.engine.adapters import OvisOCR2Adapter
# 生产级版心裁切（与 orchestrator.py:62 一致）
from kzocr.engine.run import _crop_to_body
from kzocr.engine.layout_crop import reset_cv2_calib

# OvisOCR2 Q4_KM GGUF (replaces RapidOCR as the Tier-2 cross engine)
_OVIS_ZFS400 = os.environ.get("KZOCR_ZFS400", "/media/keen/ZFS400")
OVIS_Q4KM_MODEL = os.environ.get(
    "KZOCR_OVIS_Q4KM_MODEL", os.path.join(_OVIS_ZFS400, "OvisOCR2-Q4_KM.gguf"))
OVIS_MMPROJ = os.environ.get(
    "KZOCR_OVISOCR2_MMPROJ", os.path.join(_OVIS_ZFS400, "mmproj-F16.gguf"))


def render_page(pdf_path: str, i: int, dpi: int = 150) -> np.ndarray:
    """真实扫描页 → numpy 图像（BGR，与 OpenCV 习惯一致）。"""
    doc = fitz.open(pdf_path)
    pix = doc[i].get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    return img


def po_text(oc, img: np.ndarray) -> str:
    """PaddleOCR 全页识别 → 拼接文本。"""
    res = oc.predict(img)
    txts: list[str] = []
    for blk in res:
        if isinstance(blk, dict):
            rt = blk.get("rec_texts") or blk.get("rec_text") or []
            txts += rt if isinstance(rt, list) else [str(rt)]
        elif isinstance(blk, (list, tuple)):  # 旧格式 [[box,(text,score)],...]
            for line in blk:
                try:
                    txts.append(line[1][0])
                except Exception:
                    pass
    return "".join(txts)


def ovis_text(ovis, img: np.ndarray, page_num: int = 0) -> str:
    """OvisOCR2-Q4_KM 全页识别 → 拼接文本。"""
    return ovis.run_page(PageInput(page_num=page_num, img=img)).text or ""


def main() -> int:
    ap = argparse.ArgumentParser(description="真实书跨引擎校验端到端验证（含版心裁切全流程）")
    ap.add_argument("pdf", help="真实扫描古籍 PDF 路径")
    ap.add_argument("--book-code", default="REALBOOK-秘方求真-570")
    ap.add_argument("--db-dir", default="e2e_db")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--start-page", type=int, default=1,
                    help="起始书页号（1-indexed，含端点）；默认 1")
    ap.add_argument("--end-page", type=int, default=None,
                    help="结束书页号（1-indexed，含端点）；默认全本末页")
    ap.add_argument("--no-crop", action="store_true",
                    help="关闭版心裁切，走整页对照（验证裁切必要性时对比用）")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"[ERR] PDF 不存在: {args.pdf}", file=sys.stderr)
        return 2

    os.makedirs(args.db_dir, exist_ok=True)
    confusion_set = load_confusion_set()  # 静态集 + 自学习集叠加
    print(f"[info] 形近字黑名单规模: {len(confusion_set)} 对（含自学习覆盖）")

    # 引擎懒加载（首次会下载/读缓存模型，约数秒）
    print("[info] 加载 PaddleOCR ...")
    from paddleocr import PaddleOCR

    oc = PaddleOCR()
    try:
        ovis = OvisOCR2Adapter(auto_spawn=True, model_path=OVIS_Q4KM_MODEL, mmproj_path=OVIS_MMPROJ)
        print("[info] 加载 OvisOCR2-Q4_KM 成功（引擎B 可用）")
    except Exception as e:  # pragma: no cover
        ovis = None
        print(f"[warn] OvisOCR2-Q4_KM 不可用，跳过双引擎比对: {e}", file=sys.stderr)
        return 3

    doc = fitz.open(args.pdf)
    n_pages = doc.page_count
    doc.close()

    # 页码范围（1-indexed 书页号 → 0-indexed PDF 索引）
    start_idx = max(0, args.start_page - 1)
    end_idx = (args.end_page - 1) if args.end_page is not None else (n_pages - 1)
    end_idx = min(end_idx, n_pages - 1)
    if start_idx > end_idx:
        print(f"[ERR] 页码范围无效：start={args.start_page} end={args.end_page}（全书 {n_pages} 页）",
              file=sys.stderr)
        return 2
    sel_pages = end_idx - start_idx + 1
    print(f"[info] 书籍 {args.book_code}：全书 {n_pages} 页，本次处理书页 {args.start_page}~{end_idx + 1}（{sel_pages} 页）")
    print(f"[info] 版心裁切：{'开启（生产级 _crop_to_body）' if not args.no_crop else '关闭（整页对照）'}")

    # 换书零人工：每次运行前重置 cv2 左界标定缓存，前几页用 PP-DocLayoutV3 真值重标定
    reset_cv2_calib()

    db = BookDB(args.book_code, db_dir=args.db_dir)
    total_div = 0
    total_high = 0
    per_page: list[dict] = []

    for i in range(start_idx, end_idx + 1):
        t0 = time.time()
        img = render_page(args.pdf, i, dpi=args.dpi)
        h0, w0 = img.shape[:2]
        if not args.no_crop:
            img = _crop_to_body(img, page_num=i)  # 生产级版心裁切
        h1, w1 = img.shape[:2]
        txt_a = po_text(oc, img)
        txt_b = ovis_text(ovis, img, i)
        dt = time.time() - t0

        divs = run_cross_align(
            i, txt_a, txt_b,
            confusion_set=confusion_set,
            engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM",
        )
        high = [d for d in divs if d.priority in ("P0", "P1", "high")]
        if divs:
            db.write_cross_divergences(i, divs, engine_a="PaddleOCR", engine_b="OvisOCR2-Q4_KM")
        if high:
            db.record_anomaly(
                i,
                GlyphVerdict(
                    status="UNKNOWN", confidence=0.4,
                    details=f"cross_divergence;high={len(high)};sample={high[0].a_seg}↔{high[0].b_seg}",
                ),
                detector_chain=["CrossAlign"],
            )
        total_div += len(divs)
        total_high += len(high)
        per_page.append({
            "page": i, "chars_a": len(txt_a), "chars_b": len(txt_b),
            "divs": len(divs), "high": len(high), "sec": round(dt, 1),
            "sample_high": f"{high[0].a_seg}↔{high[0].b_seg}" if high else "",
        })
        crop_pct = int((w0 * h0 - w1 * h1) * 100 / (w0 * h0)) if (w0 * h0) else 0
        crop_tag = f"裁{crop_pct}%" if not args.no_crop else "整页"
        print(f"  page {i}: [{crop_tag}] A={len(txt_a)}字 B={len(txt_b)}字 | 分歧 {len(divs)} (high {len(high)}) {dt:.1f}s"
              + (f"  例:{per_page[-1]['sample_high']}" if high else ""))

    print(f"[ok] 落库完成：总分歧 {total_div}，high 优先级 {total_high}")

    # ── Web/REST 暴露校验（复用生产路由，TestClient 不启服务器）──
    os.environ["KZOCR_DB_DIR"] = os.path.abspath(args.db_dir)
    from fastapi.testclient import TestClient
    from kzocr.web.app import app

    client = TestClient(app)
    r = client.get(f"/api/books/{args.book_code}/divergences?priority=high")
    assert r.status_code == 200, f"API high 失败: {r.status_code}"
    high_items = r.json()
    print(f"[ok] REST GET /api/books/{{code}}/divergences?priority=high → {len(high_items)} 条")

    r_all = client.get(f"/api/books/{args.book_code}/divergences")
    assert r_all.status_code == 200, f"API all 失败: {r_all.status_code}"
    all_items = r_all.json()
    print(f"[ok] REST GET /api/books/{{code}}/divergences（全部）→ {len(all_items)} 条")

    r2 = client.get(f"/book/{args.book_code}/divergences")
    assert r2.status_code == 200, f"页面失败: {r2.status_code}"
    print(f"[ok] HTML /book/{{code}}/divergences → 200，长度 {len(r2.text)} 字符")

    # ── 形近字自学习校验：取一条真实单字形近分歧，学为形近字 ──
    # 自学习面向 形近字，优先级不限；挑选首个单字 replace/confusion 对。
    learn_ok = False
    cand = next(
        (d for d in all_items
         if d["div_type"] in ("confusion", "replace")
         and len(d["a_seg"]) == 1 and len(d["b_seg"]) == 1
         and d["a_seg"] != d["b_seg"]),
        None,
    )
    if cand is not None:
        wrong, correct = cand["a_seg"], cand["b_seg"]
        r3 = client.post("/api/confusion", json={"wrong": wrong, "correct": correct, "source": "e2e-realbook"})
        assert r3.status_code == 200, f"self-learn 失败: {r3.status_code}"
        body = r3.json()
        reload_confusion_set()
        cs = load_confusion_set()
        learn_ok = cs.get(wrong) == correct
        print(f"[ok] 自学习 POST /api/confusion {wrong}→{correct} → {body.get('status')}; 重载后命中={learn_ok}")
    else:
        print("[skip] 未找到单字形近对，跳过自学习写入")

    print("\n=== 端到端验证汇总 ===")
    print(f"  书籍: {args.book_code}（全书 {n_pages} 页；本次 {sel_pages} 页 书页{args.start_page}~{end_idx + 1}）")
    print("  引擎: PaddleOCR(PP-OCRv6) vs OvisOCR2-Q4_KM — 均为真实本地 CPU 引擎")
    print(f"  版心裁切: {'开启（生产级 _crop_to_body）' if not args.no_crop else '关闭（整页对照）'}")
    print(f"  分歧总数: {total_div}  | high 优先级: {total_high}")
    print(f"  Web/REST 暴露: {'通过' if r.status_code == 200 and r2.status_code == 200 else '失败'}")
    print(f"  形近字自学习: {'通过' if learn_ok else '跳过/未触发'}")
    print(f"  数据库目录: {os.path.abspath(args.db_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
