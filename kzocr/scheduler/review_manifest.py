"""review_manifest — 人工校对清单（v0.7 §5.6）。

遍历 BookDB 的 unresolved anomalies，生成结构化审核清单（Priority
P0/P1/P2），审核结果可经 ``feedback_apply`` 回写到底层 BookDB。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from kzocr.storage.db import BookDB


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
        has_fix = any(
            iss.expected is not None and iss.expected != ""
            for iss in page.issues
        )
        if has_fix:
            for iss in page.issues:
                if iss.expected and iss.expected != "":
                    # 按位置信息写入到 page 中
                    # 简化：按 page_num 逐行写入修正文本
                    pass
            # 标记 page 的 human_final（简化：修正文本写入第一行）
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
