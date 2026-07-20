"""review_manifest — 人工校对清单（v0.7 §5.6）。

遍历 BookDB 的 unresolved anomalies，生成结构化审核清单（Priority
P0/P1/P2），审核结果可经 ``feedback_apply`` 回写到底层 BookDB。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple

from kzocr.scheduler.cross_align import add_learned_confusion
from kzocr.storage.db import BookDB


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
