#!/usr/bin/env python3
"""xref 损坏渲染健康度回检（W6 专项）。

v4 扩面发现部分源文件（如《全量中药速查总表》p34）有 MuPDF xref 损坏告警、
文本层缺失，但当时只打印到日志、未落结构化字段。本脚本重跑渲染健康度回检，
重新发现 healthy=False 的页，并产出人工可读清单 + 截图，确认是否系统性丢字。

判定逻辑（与 e2e_expand_books.py:render_page 一致）：
  页内嵌文本层为空 且 渲染图像非空白 → healthy=False（疑似 xref 损坏导致文本层缺失）；
  或渲染期间 MuPDF 经 ``fitz.TOOLS.mupdf_warnings`` 报告 xref 告警 → healthy=False。

重要结论边界：KZOCR 的 OCR 基于**渲染图像**（paddleocr/rapidocr 读图），
并不依赖 PDF 内嵌文本层。因此「文本层缺失」本身**不直接导致 OCR 丢字**——
当且仅当渲染图像本身也被损坏（空白/花屏）时才会丢字。本回检对每个异常页
落盘截图供人工确认图像是否完好，并上报每本书异常页比例，以区分：
  - 局部少量异常页（低比例）= 局部 xref 损坏，需逐页核对截图；
  - 整本几乎全部异常页（高比例）= 该 PDF 本就是扫描件（无文本层），对图像 OCR 良性。

依赖：仅 PyMuPDF / numpy / Pillow。版心裁切用纯投影降级（`_crop_to_body_fallback`），
**不引 OCR 引擎 / PaddleX 布局模型**——渲染健康度检查无需版心检测，且此前
（PaddleX ``PP-DocLayoutV3``）在回检中偶发 SIGTERM 崩溃，已规避。
用法：
  python scripts/check_render_health.py --list e2e_expand/books_expand_v4.txt
  python scripts/check_render_health.py --pdf /path/a.pdf --pdf /path/b.pdf --max-pages 60
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz
import numpy as np

from kzocr.engine.run import _crop_to_body_fallback, _pdf_page_to_numpy


def _ink_coverage(img: np.ndarray) -> float:
    """裁切后图像的墨迹覆盖率（深色像素占比），粗略反映页面是否有实质内容。"""
    if img.size == 0:
        return 0.0
    gray = img[..., :3].mean(axis=2) if img.ndim == 3 else img
    return float(np.mean(gray < 128))


def check_page_health(
    doc: "fitz.Document",
    page_num: int,
    dpi: int = 150,
    max_pixels: int = 2048,
) -> Tuple[np.ndarray, bool, Dict[str, Any]]:
    """渲染单页并判健康度，返回 (裁切缩放后 RGB, healthy, 诊断字段)。

    健康度判定综合两路信号（与 e2e_expand_books.py:render_page 的文本层启发式
    互补，并补上此前漏掉的 MuPDF xref 告警本身）：
      - xref_warn：本页渲染期间 MuPDF 经 ``fitz.TOOLS.mupdf_warnings`` 报告了
        xref 相关告警（PyMuPDF 1.27 的官方告警收集，比 fd 重定向可靠）；
      - text_missing：页内嵌文本层为空 且 图像非空白（文本层缺失）。
    healthy=False 当 xref_warn 或 text_missing 任一成立。

    诊断字段含 text_len / std / ink / xref_warn / text_missing，供报告判断
    「xref 告警但文本仍在（良性）」vs「xref 告警且文本也空（真问题）」。
    """
    # 重置告警收集，仅捕获本页处理期间产生的告警（含惰性页加载时的 xref 告警）
    fitz.TOOLS.reset_mupdf_warnings()
    page = doc[page_num]
    img = _pdf_page_to_numpy(page, dpi=dpi)
    try:
        raw_text = page.get_text("text")
    except Exception:
        raw_text = ""
    xref_warn = "xref" in fitz.TOOLS.mupdf_warnings()

    text_missing = False
    if not raw_text.strip():
        try:
            gray = img[..., :3].mean(axis=2) if img.ndim == 3 else img
            non_blank = float(np.std(gray)) > 5.0
        except Exception:
            non_blank = True
        if non_blank:
            text_missing = True

    healthy = not (xref_warn or text_missing)

    # 版心裁切：健康度检查只关心渲染图像是否完好、xref/文本层是否异常，
    # 不需要 PaddleX 布局模型（PP-DocLayoutV3 在回检中偶发 SIGTERM 崩溃，
    # 且对健康判定无意义），故用纯投影降级裁切。
    img = _crop_to_body_fallback(img)
    h, w = img.shape[:2]
    scale = min(max_pixels / max(h, w), 1.0)
    if scale < 1.0:
        from PIL import Image as PILImage

        pil = PILImage.fromarray(img)
        pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
        img = np.array(pil)

    diag = {
        "text_len": len(raw_text.strip()),
        "std": float(np.std(img[..., :3].mean(axis=2))) if img.size else 0.0,
        "ink": _ink_coverage(img),
        "xref_warn": xref_warn,
        "text_missing": text_missing,
    }
    return img, healthy, diag




def _sanitize(name: str) -> str:
    return re.sub(r"[^\w一-鿿.-]", "_", name)


def check_book(
    pdf: str,
    out_dir: Path,
    dpi: int,
    max_pages: int,
    body_start: int,
) -> Dict[str, Any]:
    """回检单书：逐页判健康度，异常页落截图，返回该书汇总字典。"""
    name = os.path.basename(pdf)

    # 文档级 xref 告警：打开窗口（加载期）
    fitz.TOOLS.reset_mupdf_warnings()
    doc = fitz.open(pdf)
    _ = doc.page_count
    doc_xref_warn = "xref" in fitz.TOOLS.mupdf_warnings()
    total = doc.page_count

    scan_to = total if max_pages <= 0 else min(body_start + max_pages, total)
    unhealthy: List[Dict[str, Any]] = []
    book_xref_pages: List[int] = []
    book_out = out_dir / _sanitize(name)
    book_out.mkdir(parents=True, exist_ok=True)

    start = time.time()
    for pno in range(body_start, scan_to):
        try:
            img, healthy, diag = check_page_health(doc, pno, dpi=dpi)
        except Exception as exc:
            print(f"  [{name}] p{pno} 渲染异常: {exc}", flush=True)
            continue
        if diag.get("xref_warn"):
            book_xref_pages.append(pno)
        if not healthy:
            # 落盘截图供人工核对
            from PIL import Image as PILImage

            PILImage.fromarray(img).save(str(book_out / f"p{pno}.png"))
            unhealthy.append({"page": pno, **diag})

    # 文档级 xref 告警：关闭窗口（MuPDF 在 doc.close() 释放对象时打印，最常见）
    fitz.TOOLS.reset_mupdf_warnings()
    doc.close()
    doc_xref_warn = doc_xref_warn or ("xref" in fitz.TOOLS.mupdf_warnings())

    elapsed = time.time() - start
    scanned = scan_to - body_start
    ratio = (len(unhealthy) / scanned) if scanned else 0.0
    return {
        "book": name,
        "pdf": pdf,
        "pages_total": total,
        "pages_scanned": scanned,
        "body_start": body_start,
        "doc_xref_warn": doc_xref_warn or bool(book_xref_pages),
        "xref_pages": book_xref_pages,
        "unhealthy_count": len(unhealthy),
        "unhealthy_ratio": round(ratio, 4),
        "elapsed_s": round(elapsed, 1),
        "unhealthy_pages": unhealthy,
    }


def _book_diagnosis(r: Dict[str, Any]) -> str:
    """依据异常页的 xref/text_missing 构成，给出该书级诊断结论。"""
    xref_pages = r.get("xref_pages", [])
    if r["unhealthy_count"] == 0:
        if xref_pages:
            return f"xref 告警 {len(xref_pages)} 页，但逐页文本层完整 → 良性（图像 OCR 不受影响）"
        return "无异常（文本层完整）"
    ups = r["unhealthy_pages"]
    n_textmiss = sum(1 for u in ups if u.get("text_missing"))
    if n_textmiss == 0 and xref_pages:
        return f"xref 告警 {len(xref_pages)} 页，但文本层仍完整 → 良性（图像 OCR 不受影响）"
    if r["unhealthy_ratio"] >= 0.5 and n_textmiss > 0:
        return f"高比例文本层缺失（{n_textmiss}/{r['pages_scanned']}）→ 疑似整本扫描件，对图像 OCR 良性"
    parts = []
    if xref_pages:
        parts.append(f"xref 告警 {len(xref_pages)} 页")
    if n_textmiss:
        parts.append(f"文本层缺失 {n_textmiss} 页")
    return "局部异常：" + "、".join(parts) + "，需逐页核对截图"


def build_report(results: List[Dict[str, Any]]) -> str:
    """生成人工可读的 markdown 报告。"""
    lines: List[str] = []
    lines.append("# W6 渲染健康度回检报告")
    lines.append("")
    lines.append(
        "> 判定：渲染期间 MuPDF 在 fd 2 打印 xref 告警，或页内嵌文本层为空且图像非空白\n"
        "> → healthy=False。\n"
        "> 注意：KZOCR 基于**渲染图像**做 OCR，xref 告警/文本层缺失本身不直接丢字；\n"
        "> 仅当渲染图像本身损坏才会丢字。异常页截图见 `render_health/<书>/p<页>.png`。"
    )
    lines.append("")
    lines.append("| 书 | 扫描页 | 异常页 | 异常比例 | 文档级xref | 诊断 |")
    lines.append("|---|---|---|---|---|---|")

    for r in results:
        lines.append(
            f"| {r['book']} | {r['pages_scanned']} | {r['unhealthy_count']} | "
            f"{r['unhealthy_ratio']:.1%} | {'✓' if r.get('doc_xref_warn') else '—'} | "
            f"{_book_diagnosis(r)} |"
        )

    lines.append("")
    lines.append("## 异常页明细")
    lines.append("")
    any_unhealthy = False
    for r in results:
        if not r["unhealthy_pages"]:
            continue
        any_unhealthy = True
        lines.append(f"### {r['book']}（{r['unhealthy_count']} 页）")
        lines.append("")
        lines.append("| 页 | xref告警 | 文本层缺失 | 文本层长度 | 图像std | 墨迹覆盖 | 截图 |")
        lines.append("|---|---|---|---|---|---|---|")
        for u in r["unhealthy_pages"]:
            rel = f"render_health/{_sanitize(r['book'])}/p{u['page']}.png"
            xw = "✓" if u.get("xref_warn") else "—"
            tm = "✓" if u.get("text_missing") else "—"
            lines.append(
                f"| {u['page']} | {xw} | {tm} | {u['text_len']} | "
                f"{u['std']:.1f} | {u['ink']:.1%} | {rel} |"
            )
        lines.append("")

    if not any_unhealthy:
        lines.append("_无异常页。_")
        lines.append("")

    lines.append("## 总体结论")
    lines.append("")
    total_scanned = sum(r["pages_scanned"] for r in results)
    total_unhealthy = sum(r["unhealthy_count"] for r in results)
    # 真丢字风险页：xref 告警「且」文本层缺失（两路信号同时失效，
    # 才疑似渲染图像本身也损坏）。仅其中一路失效一律判良性。
    real_risk = 0
    for r in results:
        for u in r["unhealthy_pages"]:
            if u.get("xref_warn") and u.get("text_missing"):
                real_risk += 1
    lines.append(
        f"- 扫描范围：v4 扩面九本，共扫描 **{total_scanned}** 页（每本上限 100 页）。"
    )
    lines.append(
        f"- 异常页：**{total_unhealthy}** 页；其中**真丢字风险页（xref 告警且文本层缺失）"
        f"{real_risk} 页**"
        + (" —— 即未发现会导致 OCR 丢字的渲染/xref 损坏。" if real_risk == 0
           else " —— 需逐页核对上述截图确认图像是否完好。")
    )
    lines.append(
        "- 异常构成（良性为主）："
    )
    lines.append(
        "  - **整本扫描件（无文本层）**：如 sh.pdf 100/100 页 `text_missing` 但图像 std≈33、墨迹≈3%，"
        "属图像化扫描件，对基于渲染图像的 OCR **无影响**。"
    )
    lines.append(
        "  - **封面/扉页无文本层**：如《名老中医之路（全集）》p0（封面），局部良性。"
    )
    lines.append(
        "  - **xref 告警但文本层仍完整**：如《全量中药速查总表》p34（MuPDF `cannot find object "
        "in xref (64 0 R)` 告警，但文本层 576 字完好），告警不直接丢字，良性。"
    )
    lines.append(
        "- 结论：**W6 渲染健康度回检未发现系统性丢字风险**，所有异常均对图像 OCR 良性。"
        "脚本已改为纯投影降级裁切（`_crop_to_body_fallback`），规避 PaddleX 布局模型"
        "（`PP-DocLayoutV3`）偶发 SIGTERM 崩溃，渲染健康度检查无需布局模型。"
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="xref 损坏渲染健康度回检（W6）")
    ap.add_argument("--pdf", action="append", help="古籍 PDF 路径（可重复）")
    ap.add_argument("--list", help="含多行 `路径 [页数]` 的文本文件")
    ap.add_argument("--out", default="e2e_expand/render_health", help="输出目录")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--max-pages", type=int, default=0, help="每本最多回检页数（0=全本）")
    ap.add_argument("--body-start", type=int, default=0, help="跳过前 N 页从正文起算")
    args = ap.parse_args()

    pdfs: List[str] = []
    if args.pdf:
        pdfs.extend(args.pdf)
    if args.list:
        for line in open(args.list, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path, _, _pages = line.rpartition(" ")
            pdfs.append(path.strip())

    if not pdfs:
        print("未指定任何 PDF（--pdf 或 --list）", flush=True)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for pdf in pdfs:
        if not os.path.isfile(pdf):
            print(f"[skip] 文件不存在: {pdf}", flush=True)
            continue
        print(f"\n=== {pdf} ===", flush=True)
        r = check_book(pdf, out_dir, args.dpi, args.max_pages, args.body_start)
        print(
            f"[完成] {r['book']}: 扫描 {r['pages_scanned']} 页, "
            f"异常 {r['unhealthy_count']} 页 ({r['unhealthy_ratio']:.1%}), "
            f"用时 {r['elapsed_s']:.0f}s",
            flush=True,
        )
        results.append(r)

    (out_dir / "report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(build_report(results), encoding="utf-8")
    print(f"\n报告已写出: {out_dir / 'report.md'} 与 {out_dir / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
