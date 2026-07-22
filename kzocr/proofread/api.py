"""交付式校对包 API — 直接读写 custom.db 供校对人员编辑 humanFinal。

所有操作绕过 kzocr/web/ 和 BookDB，直接对 custom.db（zai schema）做 CRUD。
导回系统走 ``import_proofread_package``（kzocr/doc/proofread.py）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BookBrief:
    """书籍列表摘要。"""
    book_code: str
    title: str
    page_count: int
    line_count: int
    proofread_count: int  # 已有 humanFinal 的行数
    is_mock: bool


@dataclass
class LineItem:
    """单行校对的完整信息。"""
    id: str
    page_num: int
    para_seq: int
    seq_in_para: int
    engine_texts: str    # 各引擎原文 JSON
    consensus: str       # 共识文本
    human_final: str     # 人工终校（可编辑）
    final: str           # 最终结果
    confidence: float
    heading_level: int
    audit_source: str
    disputed_sub: int
    char_level_json: str
    heading: str = ""     # 所属标题/段落上下文
    proofread_status: str = "pending"  # pending / done
    char_boxes: list = field(default_factory=list)   # [[x1,y1,x2,y2], ...]，像素空间同 crop_img
    crop_img_b64: str = ""                            # 原图裁剪 PNG 的 base64（旧包/关闭时为空）
    vl_marks: list = field(default_factory=list)      # 字符级 VL 标注：[[start,end,"vl"|"human"],...]，区间相对 consensus 文本


@dataclass
class PageGroup:
    """按页分组的行集合。"""
    page_num: int
    lines: list[LineItem] = field(default_factory=list)


def _read_line(conn: sqlite3.Connection, row: sqlite3.Row) -> LineItem:
    # 防御性读取：旧包可能缺少 char_boxes / crop_img 列（sqlite3.Row 对缺列
    # 直接用 row["x"] 会抛 KeyError），故先用 keys() 探明存在性。
    keys = row.keys()
    char_boxes: list = (
        json.loads(row["char_boxes"])
        if ("char_boxes" in keys and row["char_boxes"])
        else []
    )
    crop_img_b64: str = (
        base64.b64encode(row["crop_img"]).decode()
        if ("crop_img" in keys and row["crop_img"])
        else ""
    )
    vl_marks: list = (
        json.loads(row["vl_marks"])
        if ("vl_marks" in keys and row["vl_marks"])
        else []
    )
    return LineItem(
        id=row["id"],
        page_num=row["pageNum"],
        para_seq=row["paraSeq"],
        seq_in_para=row["seqInPara"],
        engine_texts=row["engineTexts"] or "",
        consensus=row["consensus"] or "",
        human_final=row["humanFinal"] or "",
        final=row["final"] or "",
        confidence=row["confidence"] or 0.0,
        heading_level=row["headingLevel"] or 0,
        audit_source=row["auditSource"] or "",
        disputed_sub=row["disputed"] or 0,
        char_level_json=row["charLevelJson"] or "",
        proofread_status="done" if (row["humanFinal"] and row["humanFinal"].strip()) else "pending",
        char_boxes=char_boxes,
        crop_img_b64=crop_img_b64,
        vl_marks=vl_marks,
    )


class CustomDbProofread:
    """校对包（custom.db）的读取/写入接口。

    所有方法都是无状态工具函数，不持有连接。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).resolve()
        if not self._path.exists():
            raise FileNotFoundError(f"校对包不存在：{self._path}")

    # ── 连接管理 ──────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 并发友好
        return conn

    # ── 书籍 ──────────────────────────────────────────────────

    def list_books(self) -> list[BookBrief]:
        """列出校对包中所有书籍。"""
        conn = self._connect()
        try:
            books = conn.execute(
                "SELECT bookCode, title, isMock FROM Book ORDER BY bookCode"
            ).fetchall()
            result: list[BookBrief] = []
            for b in books:
                pages = conn.execute(
                    "SELECT COUNT(DISTINCT pageNum) FROM Line WHERE bookCode=?",
                    (b["bookCode"],),
                ).fetchone()[0]
                lines = conn.execute(
                    "SELECT COUNT(*) FROM Line WHERE bookCode=?",
                    (b["bookCode"],),
                ).fetchone()[0]
                done = conn.execute(
                    "SELECT COUNT(*) FROM Line WHERE bookCode=? AND humanFinal IS NOT NULL AND humanFinal!=''",
                    (b["bookCode"],),
                ).fetchone()[0]
                result.append(BookBrief(
                    book_code=b["bookCode"],
                    title=b["title"] or b["bookCode"],
                    page_count=pages,
                    line_count=lines,
                    proofread_count=done,
                    is_mock=bool(b["isMock"]),
                ))
            return result
        finally:
            conn.close()

    # ── 行 ────────────────────────────────────────────────────

    def list_lines(self, book_code: str, *,
                   page: Optional[int] = None,
                   status: Optional[str] = None,
                   limit: int = 50, offset: int = 0) -> list[LineItem]:
        """列出某书行列表，支持按页/状态过滤。"""
        conn = self._connect()
        try:
            where = ["bookCode=?"]
            params: list = [book_code]
            if page is not None:
                where.append("pageNum=?")
                params.append(page)
            if status == "done":
                where.append("humanFinal IS NOT NULL AND humanFinal!=''")
            elif status == "pending":
                where.append("(humanFinal IS NULL OR humanFinal='')")
            sql = (
                "SELECT * FROM Line WHERE "
                + " AND ".join(where)
                + " ORDER BY pageNum, paraSeq, seqInPara"
                + f" LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            return [_read_line(conn, r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def get_line(self, book_code: str, line_id: str) -> Optional[LineItem]:
        """获取指定行详细信息。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM Line WHERE bookCode=? AND id=?",
                (book_code, line_id),
            ).fetchone()
            if not row:
                return None
            return _read_line(conn, row)
        finally:
            conn.close()

    def count_lines(self, book_code: str, *,
                    page: Optional[int] = None,
                    status: Optional[str] = None) -> int:
        """行数统计（分页用）。"""
        conn = self._connect()
        try:
            where = ["bookCode=?"]
            params: list = [book_code]
            if page is not None:
                where.append("pageNum=?")
                params.append(page)
            if status == "done":
                where.append("humanFinal IS NOT NULL AND humanFinal!=''")
            elif status == "pending":
                where.append("(humanFinal IS NULL OR humanFinal='')")
            return conn.execute(
                "SELECT COUNT(*) FROM Line WHERE " + " AND ".join(where), params
            ).fetchone()[0]
        finally:
            conn.close()

    def save_human_final(self, book_code: str, line_id: str,
                         human_final: str) -> bool:
        """保存人工终校文本。"""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE Line SET humanFinal=? WHERE bookCode=? AND id=?",
                (human_final, book_code, line_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_pages(self, book_code: str) -> list[int]:
        """获取某书所有页码（排序）。"""
        conn = self._connect()
        try:
            return [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT pageNum FROM Line WHERE bookCode=? ORDER BY pageNum",
                    (book_code,),
                ).fetchall()
            ]
        finally:
            conn.close()

    def get_page_line_count(self, book_code: str, page_num: int,
                            status: Optional[str] = None) -> int:
        """某页行数（含可选状态过滤）。"""
        return self.count_lines(book_code, page=page_num, status=status)

    def export_import(self, book_code: str, *,
                      db_dir: str = "",
                      register_postgres: bool = False) -> dict:
        """调用 import_proofread_package 将当前 humanFinal 回写 BookDB。

        返回 {"book_code", "imported_lines", "imported_proofreads"}。
        """
        from kzocr.doc import import_proofread_package as _import
        # 先冻结再导入（保护旧包不被重复修改）
        from kzocr.doc import freeze_custom_db
        try:
            freeze_custom_db(str(self._path))
        except Exception as exc:
            logger.warning("冻结失败（非阻断）: %s", exc)
        result = _import(
            db_path=self._path,
            book_code=book_code,
            db_dir=db_dir or os.environ.get("KZOCR_DB_DIR", ""),
            register_postgres=register_postgres,
        )
        return result

    def get_book_info(self, book_code: str) -> Optional[BookBrief]:
        """获取单书摘要。"""
        for b in self.list_books():
            if b.book_code == book_code:
                return b
        return None


@dataclass
class DiffToken:
    """字符级 diff 的一个片段。

    - op == "equal"   ：两侧相同文本。
    - op == "insert"  ：仅出现在目标文本 b 中的新增内容（绿色）。
    - op == "delete"  ：仅出现在源文本 a 中的被删内容（红色）。
    - op == "replace" ：位置对应的删除+插入配对，即“修改”（橙色）。
      old 为源片段，new 为目标片段；text 统一取 new（显示用）。
    """

    op: str           # "equal" | "insert" | "delete" | "replace"
    text: str         # 展示文本（insert/replace 取目标；delete 取源；equal 取原文）
    old: str = ""     # 源片段（delete/replace 取值）
    new: str = ""     # 目标片段（insert/replace 取值）


def scale_char_box(box: list, scale: float) -> dict:
    """把图像像素空间的字符框映射到显示像素空间。

    纯函数，便于单测与前端算法对齐。``box`` 为 ``[x1, y1, x2, y2]``（crop_img
    自然像素），``scale`` 为 显示宽度 / 自然宽度。返回 left/top/width/height。
    """
    if len(box) < 4:
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
    return {
        "left": x1 * scale,
        "top": y1 * scale,
        "width": (x2 - x1) * scale,
        "height": (y2 - y1) * scale,
    }


def compute_diff(a: str, b: str) -> list[DiffToken]:
    """对两段文本做字符级 LCS diff，返回带类型的 token 列表。

    纯函数、无外部依赖，便于单测与前端算法对齐。着色语义由调用方按
    op 决定（insert=绿 / delete=红 / replace=橙 / equal=默认）。
    """
    n, m = len(a), len(b)
    if n == 0 and m == 0:
        return []

    # LCS 长度表：dp[i][j] = a[i:] 与 b[j:] 的 LCS 长度
    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (
                dp[i + 1][j + 1] + 1 if a[i] == b[j]
                else max(dp[i + 1][j], dp[i][j + 1])
            )

    # 回溯得到 equal/delete/insert 原始片段
    raw: list[tuple[str, str]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            start = i
            while i < n and j < m and a[i] == b[j]:
                i += 1
                j += 1
            raw.append(("equal", a[start:i]))
        elif dp[i + 1][j] >= dp[i][j + 1]:
            raw.append(("delete", a[i]))
            i += 1
        else:
            raw.append(("insert", b[j]))
            j += 1
    while i < n:
        raw.append(("delete", a[i]))
        i += 1
    while j < m:
        raw.append(("insert", b[j]))
        j += 1

    # 将相邻的 delete 段 + insert 段（两种顺序）合并为 replace（即“修改”）。
    # 例：cat→dog 整段合并为一次 replace，而非逐字符错配。
    tokens: list[DiffToken] = []
    k = 0
    while k < len(raw):
        op, text = raw[k]
        if op == "delete":
            old_txt = text
            e = k + 1
            while e < len(raw) and raw[e][0] == "delete":
                old_txt += raw[e][1]
                e += 1
            if e < len(raw) and raw[e][0] == "insert":
                new_txt = raw[e][1]
                e += 1
                while e < len(raw) and raw[e][0] == "insert":
                    new_txt += raw[e][1]
                    e += 1
                tokens.append(DiffToken("replace", new_txt, old=old_txt, new=new_txt))
            else:
                tokens.append(DiffToken("delete", old_txt, old=old_txt))
            k = e
        elif op == "insert":
            new_txt = text
            e = k + 1
            while e < len(raw) and raw[e][0] == "insert":
                new_txt += raw[e][1]
                e += 1
            if e < len(raw) and raw[e][0] == "delete":
                old_txt = raw[e][1]
                e += 1
                while e < len(raw) and raw[e][0] == "delete":
                    old_txt += raw[e][1]
                    e += 1
                tokens.append(DiffToken("replace", new_txt, old=old_txt, new=new_txt))
            else:
                tokens.append(DiffToken("insert", new_txt, new=new_txt))
            k = e
        else:
            tokens.append(DiffToken("equal", text, old=text, new=text))
            k += 1
    return tokens
