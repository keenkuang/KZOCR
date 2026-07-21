#!/usr/bin/env python3
"""多古籍跨引擎分歧对齐扩面验证（与 orchestrator 全路径对齐的渲染管线）。

渲染阶段复用 orchestrator 全路径的同一管线（_pdf_page_to_numpy + 版心裁切
_crop_to_body + 最长边≤2048 缩放），使双引擎分歧数字与 orchestrator 全路径
（秘方求真/验方新编 的参考数）严格可比，不再混入「全页 vs 版心裁切」的方法论差异。

双引擎比对仍走轻量直驱（绕过 orchestrator 逐页验证/落库），仅渲染与主编排对齐。
依赖：paddleocr、rapidocr_onnxruntime（本机已装）。
用法：
  python scripts/e2e_expand_books.py --pdf <书1.pdf> --pdf <书2.pdf> --pages 20
  python scripts/e2e_expand_books.py --list books.txt --pages 20
"""
from __future__ import annotations

import argparse
import json
import os
import time

import fitz
import numpy as np

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.run import _crop_to_body, _pdf_page_to_numpy
from kzocr.engine.types import PageInput
from kzocr.scheduler.cross_align import load_confusion_set, run_cross_align


def render_page(pdf: str, page_num: int, dpi: int = 150, max_pixels: int = 2048) -> tuple[np.ndarray, bool]:
    """渲染单页为 (H,W,3) RGB，并应用与 orchestrator 全路径一致的版心裁切 + 缩放。

    对齐目的：orchestrator 全路径用 render_pages（_pdf_page_to_numpy + _crop_to_body +
    最长边≤max_pixels 缩放）做版心裁切，去掉两引擎共错的页眉/页脚；扩面脚本此前用全页
    渲染，导致分歧绝对数偏高。此处复用同一管线，使分歧数字与 orchestrator 全路径严格可比。

    Returns:
        (img, healthy)：img 为裁切缩放后的 RGB；healthy 为渲染健康度。
        healthy=False 表示 fitz 文本层提取异常（xref 损坏等导致文本层缺失）且图像非空白，
        疑似该页渲染丢字——调用方应做渲染回检（避免损坏页静默丢字，见 v4 扩面发现）。
    """
    doc = fitz.open(pdf)
    try:
        img = _pdf_page_to_numpy(doc[page_num], dpi=dpi)
        # 渲染健康度回检：提取嵌入文本层。若抛出异常或为空、且图像非空白，
        # 疑似 xref 损坏导致页面文本层缺失（v4 扩面中 全量中药速查总表 p30 报
        # "cannot find object in xref" 但脚本静默继续），标记 healthy=False 供核查。
        healthy = True
        try:
            raw_text = doc[page_num].get_text("text")
        except Exception:
            raw_text = ""
        if not raw_text.strip():
            try:
                gray = img[..., :3].mean(axis=2) if img.ndim == 3 else img
                non_blank = float(np.std(gray)) > 5.0
            except Exception:
                non_blank = True
            if non_blank:
                healthy = False
    finally:
        doc.close()
    img = _crop_to_body(img, page_num=page_num)
    h, w = img.shape[:2]
    scale = min(max_pixels / max(h, w), 1.0)
    if scale < 1.0:
        from PIL import Image as PILImage
        pil = PILImage.fromarray(img)
        pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        img = np.array(pil)
    return img, healthy


def count_book(pdf: str, pages: int, dpi: int, paddle, rapid, confusion_set,
               existing: dict | None = None, body_start: int = 0) -> dict:
    """处理单书前 `pages` 页的跨引擎分歧。

    `existing` 为非空时进入**增量合并**模式：从 `summary.json` 已有记录中读取已处理页号，
    只计算尚未覆盖的页（0..pages-1 中缺失者），并把累计分歧/高分歧与逐页明细合并回同一条记录。
    这样推进扩面时不会重复 OCR 已跑过的页、也不丢失历史结果。

    `body_start`：跳过前 `body_start` 页（封面/目录/凡例等非正文区）再从正文起算，
    避免「从 p0 起采样」系统性低估全书分歧率（v4 扩面发现：附子 p0–11 几乎全 0 分歧）。
    """
    name = os.path.basename(pdf)
    if existing:
        done = {d["page"] for d in existing.get("per_page", [])}
        total = existing.get("total_divergences", 0)
        high = existing.get("high_divergences", 0)
        per_page = list(existing.get("per_page", []))
    else:
        done = set()
        total = 0
        high = 0
        per_page = []
    t0 = time.time()
    processed_new = 0
    render_warnings: list[int] = []
    for pno in range(body_start, pages):
        if pno in done:
            continue
        try:
            img, healthy = render_page(pdf, pno, dpi)
        except Exception as exc:
            print(f"  [{name}] p{pno} 渲染失败: {exc}", flush=True)
            continue
        if not healthy:
            render_warnings.append(pno)
        a = paddle.run_page(PageInput(page_num=pno, img=img)).text or ""
        b = rapid.run_page(PageInput(page_num=pno, img=img)).text or ""
        divs = run_cross_align(pno, a, b, confusion_set=confusion_set)
        # 优先级语义：core 用 P0/P1/normal（见 cross_align._is_priority），
        # orchestrator 将 P0/P1/high 一并归入「高优先」分歧队列。此处与之对齐，
        # 否则 P0(剂量数字)/P1(形近字) 永不被统计，per_page[].high 恒为 0。
        n_high = sum(1 for d in divs if d.priority in ("P0", "P1", "high"))
        total += len(divs)
        high += n_high
        processed_new += 1
        per_page.append({"page": pno, "div": len(divs), "high": n_high})
        if (pno + 1) % 5 == 0:
            print(f"  [{name}] p{pno+1}/{pages} 累计分歧={total} high={high} (本次新增 {processed_new})",
                  flush=True)
    elapsed = time.time() - t0
    if render_warnings:
        print(f"[warn] {name}: {len(render_warnings)} 页渲染健康度异常（疑似丢字），页码={render_warnings}",
              flush=True)
    print(f"[完成] {name}: 本次新增 {processed_new} 页, 累计 {len(per_page)} 页, "
          f"总分歧={total}, 高={high}, 用时 {elapsed:.0f}s", flush=True)
    return {
        "book": name,
        "pdf": pdf,
        "pages_processed": len(per_page),
        "pages_requested": pages,
        "total_divergences": total,
        "high_divergences": high,
        "elapsed_s": round(elapsed, 1),
        "render_warnings": render_warnings,
        "per_page": per_page,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="多古籍跨引擎分歧对齐扩面验证")
    ap.add_argument("--pdf", action="append", help="古籍 PDF 路径（可重复），后可跟空格+页数")
    ap.add_argument("--list", help="含多行 PDF 路径的文本文件，每行 `路径` 或 `路径 页数`")
    ap.add_argument("--pages", type=int, default=20, help="默认每本书处理页数（--list 行内可覆盖）")
    ap.add_argument("--body-start", type=int, default=0,
                    help="跳过前 N 页（封面/目录/凡例等非正文区）再从正文起算采样，"
                         "避免从 p0 起采样系统性低估分歧率；默认 0")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--out", default="e2e_expand/summary.json", help="汇总输出 JSON")
    ap.add_argument("--merge", action="store_true",
                    help="增量合并：复用 --out 已有记录，仅计算未覆盖的页")
    args = ap.parse_args()

    # 解析 (pdf, pages) 目标列表；--list 行内可用 `路径 页数` 覆盖默认页数
    targets: list[tuple[str, int]] = []
    raw: list[str] = list(args.pdf or [])
    if args.list:
        with open(args.list, encoding="utf-8") as fh:
            raw.extend(fh.read().splitlines())
    for line in raw:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 路径可能含空格（如「名老中医之路 (全三册).pdf 20」），
        # 约定：行末若为整数则视为页数，其前的全部内容视为路径。
        parts = line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            pgs = int(parts[-1])
            path = " ".join(parts[:-1])
        else:
            pgs = args.pages
            path = line
        targets.append((path, pgs))
    if not targets:
        print("[ERR] 未提供任何 PDF（用 --pdf 或 --list）", flush=True)
        return 2

    # 载入已有汇总用于增量合并
    existing_map: dict[str, dict] = {}
    if args.merge and os.path.isfile(args.out):
        try:
            with open(args.out, encoding="utf-8") as fh:
                for rec in json.load(fh):
                    existing_map[rec["pdf"]] = rec
            print(f"[info] 载入已有汇总 {args.out}（{len(existing_map)} 本）用于增量合并",
                  flush=True)
        except Exception as exc:
            print(f"[warn] 载入已有汇总失败，将全量重跑: {exc}", flush=True)

    print("[info] 加载 PaddleOCRAdapter ...", flush=True)
    paddle = PaddleOCRAdapter()
    print("[info] 加载 RapidOCRAdapter ...", flush=True)
    rapid = RapidOCRAdapter()
    confusion_set = load_confusion_set()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    # 以已有记录为底，覆盖本次处理的书，保证未重跑的书不丢
    results_by_pdf = {pdf: rec for pdf, rec in existing_map.items()}
    for pdf, pages in targets:
        if not os.path.isfile(pdf):
            print(f"[ERR] 跳过不存在的 PDF: {pdf}", flush=True)
            continue
        print(f"\n=== {pdf}（目标 {pages} 页，body_start={args.body_start}）===", flush=True)
        rec = count_book(pdf, pages, args.dpi, paddle, rapid, confusion_set,
                         existing=existing_map.get(pdf), body_start=args.body_start)
        results_by_pdf[pdf] = rec
        # 每本书结束后立即落盘作为检查点：长作业防崩溃丢进度，
        # 中途退出后可用 --merge 从检查点续跑（已完成的书会被跳过）。
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(list(results_by_pdf.values()), fh, ensure_ascii=False, indent=2)
        existing_map[pdf] = rec  # 检查点后也作为后续书的合并基线
        print(f"[checkpoint] 已落盘 {len(results_by_pdf)} 本 -> {args.out}", flush=True)

    results = list(results_by_pdf.values())
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    print("\n=== 扩面汇总 ===", flush=True)
    print(f"{'古籍':<28} {'页':>4} {'总分歧':>8} {'高':>6} {'分歧/页':>8}", flush=True)
    for r in results:
        rpp = r["total_divergences"] / r["pages_processed"] if r["pages_processed"] else 0
        print(f"{r['book']:<26} {r['pages_processed']:>4} {r['total_divergences']:>8} "
              f"{r['high_divergences']:>6} {rpp:>8.1f}", flush=True)
    print(f"\n汇总已写入 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
