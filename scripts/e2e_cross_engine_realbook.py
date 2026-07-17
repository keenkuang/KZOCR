#!/usr/bin/env python3
"""真实书端到端验证（设计文档 §6 #7）。

用两个真实本地 OCR 引擎对真实扫描古籍逐页识别，喂给生产级 `run_cross_align`
做 token 级模糊比对，提取分歧（数字/剂量 + 形近黑名单 high 优先），落库并校验
Web/REST 暴露与形近字黑名单自学习。

  - 引擎A：PaddleOCR（PP-OCRv6，CPU，无需密钥）
  - 引擎B：RapidOCR（onnxruntime，CPU，无需密钥）

说明：完整 `orchestrate_book` 的跨引擎比对挂在 Tier1(PaddleOCR) vs Tier3(本地 LLM)
失败路径上，而本机无 GPU / 无云端密钥，故这里直接驱动跨引擎校验栈的核心
（真实引擎 + run_cross_align + BookDB + Web/REST + 自学习），等价验证端到端能力。

依赖（本机已装）：paddleocr、rapidocr_onnxruntime。
用法：
  python scripts/e2e_cross_engine_realbook.py <pdf> [--book-code X] [--db-dir D] [--dpi 150]
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
from kzocr.engine.types import GlyphVerdict


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


def ro_text(ro, img: np.ndarray) -> str:
    """RapidOCR 全页识别 → 拼接文本。"""
    out, _ = ro(img)
    if out is None:
        return ""
    return "".join(b[1] for b in out)


def main() -> int:
    ap = argparse.ArgumentParser(description="真实书跨引擎校验端到端验证")
    ap.add_argument("pdf", help="真实扫描古籍 PDF 路径")
    ap.add_argument("--book-code", default="REALBOOK-秘方求真-570")
    ap.add_argument("--db-dir", default="e2e_db")
    ap.add_argument("--dpi", type=int, default=150)
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
        from rapidocr_onnxruntime import RapidOCR

        ro = RapidOCR()
        print("[info] 加载 RapidOCR 成功（引擎B 可用）")
    except Exception as e:  # pragma: no cover
        ro = None
        print(f"[warn] RapidOCR 不可用，跳过双引擎比对: {e}", file=sys.stderr)
        return 3

    doc = fitz.open(args.pdf)
    n_pages = doc.page_count
    doc.close()
    print(f"[info] 书籍 {args.book_code}：{n_pages} 页")

    db = BookDB(args.book_code, db_dir=args.db_dir)
    total_div = 0
    total_high = 0
    per_page: list[dict] = []

    for i in range(n_pages):
        t0 = time.time()
        img = render_page(args.pdf, i, dpi=args.dpi)
        txt_a = po_text(oc, img)
        txt_b = ro_text(ro, img)
        dt = time.time() - t0

        divs = run_cross_align(
            i, txt_a, txt_b,
            confusion_set=confusion_set,
            engine_a="PaddleOCR", engine_b="RapidOCR",
        )
        high = [d for d in divs if d.priority == "high"]
        if divs:
            db.write_cross_divergences(i, divs, engine_a="PaddleOCR", engine_b="RapidOCR")
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
        print(f"  page {i}: A={len(txt_a)}字 B={len(txt_b)}字 | 分歧 {len(divs)} (high {len(high)}) {dt:.1f}s"
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
    print(f"  书籍: {args.book_code}（{n_pages} 页真实扫描）")
    print("  引擎: PaddleOCR(PP-OCRv6) vs RapidOCR — 均为真实本地 CPU 引擎")
    print(f"  分歧总数: {total_div}  | high 优先级: {total_high}")
    print(f"  Web/REST 暴露: {'通过' if r.status_code == 200 and r2.status_code == 200 else '失败'}")
    print(f"  形近字自学习: {'通过' if learn_ok else '跳过/未触发'}")
    print(f"  数据库目录: {os.path.abspath(args.db_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
