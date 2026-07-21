"""review_manifest — 人工校对清单（v0.7 §5.6）。

遍历 BookDB 的 unresolved anomalies，生成结构化审核清单（Priority
P0/P1/P2），审核结果可经 ``feedback_apply`` 回写到底层 BookDB。
"""
from __future__ import annotations

import difflib
import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional, Tuple

from kzocr.scheduler.cross_align import add_learned_confusion
from kzocr.storage.db import BookDB


_PRIORITY_RANK = {"high": 0, "critical": 0, "P0": 0, "medium": 1, "P1": 1, "low": 2, "P2": 2}


def _esc(text: str) -> str:
    return html.escape(text or "")


def _highlight_diff(a: str, b: str) -> Tuple[str, str]:
    """对两串做字符级差异高亮，返回 (a_html, b_html)，差异处包 ``<mark>``。

    仅用 SequenceMatcher 做轻量对齐，不依赖 OCR 引擎/图像，零资源。
    """
    sm = difflib.SequenceMatcher(None, a or "", b or "", autojunk=False)
    a_parts: list[str] = []
    b_parts: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        a_seg = _esc(a[i1:i2])
        b_seg = _esc(b[j1:j2])
        if op == "equal":
            a_parts.append(a_seg)
            b_parts.append(b_seg)
        else:
            a_parts.append(f'<mark class="diff">{a_seg}</mark>')
            b_parts.append(f'<mark class="diff">{b_seg}</mark>')
    return "".join(a_parts), "".join(b_parts)


def export_divergence_html(db: BookDB, book_code: str, out_path: Optional[str] = None) -> str:
    """渲染跨引擎分歧为 HTML 报告（分歧片段高亮），返回写出路径。

    零资源：仅用 ``cross_divergence`` 已存片段（a_seg/b_seg/priority/status），
    不依赖 OCR 引擎或页图像。按优先级分组、差异片段以 ``<mark>`` 高亮，
    便于人工终校快速定位两引擎分歧处。

    Args:
        db: BookDB 实例（已连接该书）。
        book_code: 书籍编码（用于报告标题与默认文件名）。
        out_path: 输出 HTML 路径；缺省为 ``<book_code>_divergence.html``。

    Returns:
        实际写出的 HTML 文件路径。
    """
    rows = db.get_cross_divergences()
    by_pri: dict[str, list[dict]] = {}
    for r in rows:
        pri = r.get("priority") or "medium"
        by_pri.setdefault(pri, []).append(r)

    sections: list[str] = []
    for pri in sorted(by_pri, key=lambda p: _PRIORITY_RANK.get(p, 9)):
        items = by_pri[pri]
        cards: list[str] = []
        for r in items:
            a_seg = r.get("a_seg") or ""
            b_seg = r.get("b_seg") or ""
            a_html, b_html = _highlight_diff(a_seg, b_seg)
            cards.append(
                "<div class='card'>"
                f"<div class='meta'>页 {r.get('page_no')} · "
                f"{_esc(r.get('div_type') or '')} · "
                f"状态 {_esc(r.get('status') or 'pending')} · "
                f"{_esc(r.get('engine_a') or '')} ↔ {_esc(r.get('engine_b') or '')}</div>"
                "<div class='row'><span class='label'>A</span>"
                f"<span class='seg'>{a_html}</span></div>"
                "<div class='row'><span class='label'>B</span>"
                f"<span class='seg'>{b_html}</span></div>"
                "</div>"
            )
        sections.append(
            f"<section><h2>优先级 {_esc(pri)}（{len(items)} 处）</h2>"
            f"{''.join(cards)}</section>"
        )

    body = "".join(sections) or "<p>无跨引擎分歧记录。</p>"
    doc = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>分歧高亮 · {_esc(book_code)}</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;color:#222}}
 h1{{font-size:1.3rem}} h2{{font-size:1.05rem;margin-top:1.5rem}}
 .card{{border:1px solid #ddd;border-radius:8px;padding:.6rem .8rem;margin:.5rem 0}}
 .meta{{color:#666;font-size:.8rem;margin-bottom:.3rem}}
 .row{{display:flex;gap:.5rem;align-items:baseline;margin:.15rem 0}}
 .label{{font-weight:700;color:#999;width:1.2rem}}
 .seg{{white-space:pre-wrap}}
 mark.diff{{background:#ffe08a;border-radius:3px;padding:0 1px}}
</style></head>
<body>
<h1>跨引擎分歧高亮报告 · {_esc(book_code)}</h1>
<p>共 {len(rows)} 处分歧（按优先级分组，黄色为两引擎差异片段）。</p>
{body}
</body></html>"""

    path = out_path or f"{book_code}_divergence.html"
    Path(path).write_text(doc, encoding="utf-8")
    return path


def _parse_confusion_pair(details: str) -> Tuple[str, str]:
    """从 anomaly.details 提取 ``confusion;wrong=X;correct=Y`` 的 (wrong, correct)。

    ``verifier.ConfusionSetDetector`` 命中静态/学习混淆集时写入该格式（verifier.py:228），
    是人工终校回流所需 (误认字→正确字) 对的来源。无混淆信息时两路均返空串。
    """
    wrong = correct = ""
    for tok in (details or "").split(";"):
        if tok.startswith("wrong="):
            wrong = tok[len("wrong="):]
        elif tok.startswith("correct="):
            correct = tok[len("correct="):]
    return wrong, correct


@dataclass
class ReviewIssue:
    """单级问题（人工校对的最小单元）。"""
    position: int                         # 在 OCR 文本中的字符偏移
    ocr_char: str                         # OCR 识别的字符
    expected: Optional[str] = None        # 人工审核后填写的正确字符
    issue_type: Literal["glyph", "dosage", "herb", "layout"] = "glyph"
    severity: Literal["critical", "warning", "info"] = "info"


@dataclass
class ReviewPageItem:
    """单页审核条目。"""
    page_num: int
    priority: Literal["P0", "P1", "P2"]   # P0=FAIL, P1=UNKNOWN, P2=RARE/UNCERTAIN
    engine_results: dict[str, str]        # 每级引擎的产出文本
    crop_img_path: Optional[str] = None
    issues: list[ReviewIssue] = field(default_factory=list)


@dataclass
class ReviewManifest:
    """全书审核清单。"""
    book_code: str
    pages: list[ReviewPageItem]


def build_review_manifest(db: BookDB) -> ReviewManifest:
    """从 BookDB 的 unresolved anomalies 构建审核清单。

    Args:
        db: BookDB 实例（已连接该书）。

    Returns:
        ReviewManifest，按 anomaly.details 中的 glyph_status 标注优先级：
        - FAIL → P0，UNKNOWN → P1，RARE/UNCERTAIN → P2。
    """
    anomalies = db.get_unresolved_anomalies()
    page_items: list[ReviewPageItem] = []

    for anom in anomalies:
        pn = anom.get("page_num", 0)
        details = anom.get("details", "") or ""
        status = anom.get("verdict", "")

        # 优先级映射
        if status == "FAIL":
            priority = "P0"
        elif status == "UNKNOWN":
            priority = "P1"
        else:
            priority = "P2"

        # 引擎结果
        engine_results: dict[str, str] = {}
        # 从 details 中解析 detector_chain（如有），或留空
        # 主链信息在 anomaly 本体的 detector_chain 字段
        # 真实的 engine_results 可以通过 db.get_page(pn) 获取文本线索

        # issues
        issues: list[ReviewIssue] = []
        # 终校回流数据源：ConfusionSetDetector 命中混淆集时 details 写入
        # "confusion;wrong=X;correct=Y"（verifier.py:228）。解析误认字 X 作为
        # ReviewIssue.ocr_char，供人工终校后随 feedback_apply 回流进学习集。
        wrong, _suggested = _parse_confusion_pair(details)
        if wrong:
            issues.append(ReviewIssue(
                position=0,
                ocr_char=wrong,
                issue_type="glyph",
                severity="info",
            ))
        # 如果 details 包含 conf_low 标记，则添加一个 info 级别 issue
        if "conf_low" in details.lower():
            issues.append(ReviewIssue(
                position=0,
                ocr_char="",
                issue_type="glyph",
                severity="info",
            ))

        page_items.append(ReviewPageItem(
            page_num=pn,
            priority=priority,
            engine_results=engine_results,
            issues=issues,
        ))

    return ReviewManifest(
        book_code=db.book_code,
        pages=page_items,
    )


def export_review_manifest_json(
    manifest: ReviewManifest,
    out_path: Optional[str] = None,
) -> str:
    """将审核清单序列化为 JSON 文件，返回写出路径。

    便于外部校对台 / CI / 数据交换消费。结构::

        {"book_code": "...", "pages": [
            {"page_num": 1, "priority": "P0",
             "engine_results": {...}, "issues": [{...}]}
        ]}

    Args:
        manifest: ReviewManifest（由 ``build_review_manifest`` 构建）。
        out_path: 输出 JSON 路径；缺省为 ``<book_code>_review_manifest.json``。

    Returns:
        实际写出的 JSON 文件路径。
    """
    payload = {
        "book_code": manifest.book_code,
        "pages": [asdict(p) for p in manifest.pages],
    }
    path = out_path or f"{manifest.book_code}_review_manifest.json"
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def feedback_apply(manifest: ReviewManifest, db: BookDB) -> int:
    """将审核清单中人工修正的条目回写到底层 BookDB。

    遍历 manifest 中所有 page，对其中 issues 的 ``expected`` 字段有值的条目，
    调用 ``db.save_line_human_final()`` 持久化修正文本。

    Args:
        manifest: ReviewManifest（含人工修正）。
        db: BookDB 实例。

    Returns:
        写入的修正行数。
    """
    count = 0
    for page in manifest.pages:
        # 终校回流：人工修正 (误认字 ocr_char → 正确字 expected) 自动 enrich 自学习
        # 混淆集（learned_confusion.json）。仅当 ocr_char 与 expected 均非空且不同
        # （确为修正）才回流，避免误标/空值污染。阈值=首次即写（去重由
        # add_learned_confusion 保证），不做频率门控。
        for iss in page.issues:
            if iss.ocr_char and iss.expected and iss.ocr_char != iss.expected:
                add_learned_confusion(iss.ocr_char, iss.expected, source="review_manifest")
        # 回写人工终校文本到底层 BookDB（既有逻辑：修正文本写入第一行 human_final）
        if page.issues:
            first = page.issues[0]
            if first.expected:
                db.save_line_human_final(
                    page_num=page.page_num,
                    para_seq=1,
                    line_seq=1,
                    human_final=first.expected,
                )
                count += 1
    return count


def visualize_char_boxes(
    db: BookDB,
    book_code: str,
    page_num: int,
    pdf_path: str | None = None,
    out_path: str | None = None,
    dpi: int = 150,
) -> str:
    """渲染字符级 bbox 可视化图像。

    从 BookDB 读取某页的 ``char_boxes``（逐行逐字 [x1,y1,x2,y2]），
    在页图像（或空白画布）上以彩色矩形绘出每个字符框。
    不同行使用不同颜色，便于直观验证逐字定位质量。

    Args:
        db: BookDB 实例。
        book_code: 书籍编码（用于输出文件名）。
        page_num: 页码。
        pdf_path: 可选 PDF 路径。提供时渲染该页为底图，框线精确叠加。
        out_path: 输出 PNG 路径；缺省为 ``<book_code>_p<page_num>_boxes.png``。
        dpi: PDF 渲染 DPI（默认 150；仅当提供 pdf_path 时生效）。

    Returns:
        实际写出的 PNG 文件路径。

    零资源降级：无 PDF 时用空白画布（仅显示框坐标），可离线运行。
    """

    from PIL import Image, ImageDraw

    cb = db.get_page_char_boxes(page_num)
    if not cb:
        raise ValueError(f"页 {page_num} 无 char_boxes 数据")

    # 计算边界
    xs = [b[0] for line in cb for b in line if len(b) >= 4] + [b[2] for line in cb for b in line if len(b) >= 4]
    ys = [b[1] for line in cb for b in line if len(b) >= 4] + [b[3] for line in cb for b in line if len(b) >= 4]
    if not xs:
        raise ValueError(f"页 {page_num} char_boxes 数据为空")

    margin = 40
    canvas_w = max(xs) + margin * 2
    canvas_h = max(ys) + margin * 2

    img: Image.Image
    if pdf_path:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            if pix.n != 3:
                pix = fitz.Pixmap(pix, 0)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
        # 缩放框坐标
        scale_x = pix.width / max(xs) if max(xs) > 0 else 1
        scale_y = pix.height / max(ys) if max(ys) > 0 else 1
    else:
        img = Image.new("RGB", (int(canvas_w), int(canvas_h)), "white")
        scale_x = scale_y = 1

    draw = ImageDraw.Draw(img)

    # 调色板：8 种易区分的颜色（RGB）
    palette = [
        (220, 50, 50),    # 红
        (50, 130, 220),   # 蓝
        (50, 180, 80),    # 绿
        (220, 160, 40),   # 橙
        (160, 50, 200),   # 紫
        (200, 80, 140),   # 粉
        (80, 190, 190),   # 青绿
        (180, 120, 60),   # 棕
    ]

    for line_idx, line_boxes in enumerate(cb):
        if not line_boxes:
            continue
        color = palette[line_idx % len(palette)]
        # 半透明填充 + 实线边框
        fill = (*color, 40)
        for b in line_boxes:
            if len(b) < 4:
                continue
            x1, y1, x2, y2 = b[:4]
            scaled = (
                int(x1 * scale_x),
                int(y1 * scale_y),
                int(x2 * scale_x),
                int(y2 * scale_y),
            )
            draw.rectangle(scaled, outline=color, width=2)
            draw.rectangle(scaled, fill=fill)

        # 行号标注（该行第一个字符框上方）
        first = line_boxes[0]
        label = f"L{line_idx}"
        lx = int(first[0] * scale_x)
        ly = int(first[1] * scale_y) - 8
        draw.text((lx, ly), label, fill=color)

    path = out_path or f"{book_code}_p{page_num}_boxes.png"
    img.save(path)
    return path
