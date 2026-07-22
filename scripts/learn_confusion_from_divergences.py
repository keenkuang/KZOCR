"""从 e2e 落库的跨引擎分歧中挖掘形近字混淆候选集。

背景
----
模块 H 已把 e2e 扩面产生的逐条分歧落进各书库的 ``cross_divergence`` 表
（``kzocr/storage/db.py``）。这些分歧目前**没有任何代码消费**——``confusion_set``
只有静态集 + 人工确认集，不会从 DB 自动生长。本脚本把 25381 条分歧"反哺"回识别率
提升链路，是"数据 → 识别率"闭环的第一步（对应校对台增强计划方向 1 的 1-A 子阶段）。

设计要点（零运行时风险）
------------------------
- 只读各书库，不改动任何运行时共识/仲裁逻辑。
- 仅取 ``div_type=='replace'`` 且两侧均为**单字**的分歧（字符级形近混淆特征最干净）。
- 对无序字符对 ``{a,b}`` 统计总频次与覆盖书数，按频次降序输出候选报告。
- 方向判定：若静态 ``confusion_set`` 已锚定 ``wrong→correct`` 方向，则标 ``anchored=True``
  （高置信，可直接确认）；否则方向不可靠，标 ``anchored=False``，**仅进候选报告**供人工
  在校对台确认（确认路径与 Web ``POST /api/confusion`` 一致：``add_learned_confusion``
  → 惠及 ``ConfusionSetDetector`` / VL 仲裁 / P1 标级）。

本脚本**只挖掘、不自动改写运行时**：无地面真值时自动回写无锚定对有方向风险，故 1-A 仅
产出排名候选报告，把"数据 → 识别率"的闭环交给人工确认（阶段 1-B/1-C 再考虑自动并入）。
这也是零运行时风险的关键。

用法
----
    python scripts/learn_confusion_from_divergences.py                # 默认 db 目录 + 候选报告
    python scripts/learn_confusion_from_divergences.py --db-dir D --min-count 5
    python scripts/learn_confusion_from_divergences.py --candidates-out report.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kzocr.scheduler.cross_align import load_confusion_set
from kzocr.storage.db import BookDB


def iter_book_db_paths(db_dir: str | Path) -> list[Path]:
    """枚举 db 目录下所有按书分库的 ``*.db`` 文件（按名排序，结果稳定）。"""
    p = Path(db_dir)
    if not p.is_dir():
        return []
    return sorted(p.glob("*.db"))


@dataclass
class Candidate:
    """一个形近字混淆候选（无序字符对，已给出推断方向）。"""

    wrong: str
    correct: str
    total: int
    books: int
    anchored: bool
    note: str = ""


def tally_replace_pairs(db_dir: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    """统计各书库 ``cross_divergence`` 表中单字 ``replace`` 分歧的有序频次。

    返回 ``{(a_seg, b_seg): {"count": int, "books": set[str]}}``。
    仅纳入两侧均为单个字符、且 ``a != b`` 的 replace 分歧，避免词级/插入删除噪声。
    """
    tally: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "books": set()}
    )
    for path in iter_book_db_paths(db_dir):
        book_code = path.stem
        try:
            db = BookDB(book_code, db_dir=str(path.parent))
        except Exception:
            continue
        try:
            rows = db.get_cross_divergences()
        finally:
            db.close()
        for r in rows:
            if r.get("div_type") != "replace":
                continue
            a, b = (r.get("a_seg") or ""), (r.get("b_seg") or "")
            if len(a) != 1 or len(b) != 1 or not a or not b or a == b:
                continue
            entry = tally[(a, b)]
            entry["count"] += 1
            entry["books"].add(book_code)
    return tally


def build_candidates(
    tally: dict[tuple[str, str], dict[str, Any]],
    static_set: dict[str, str],
    min_count: int = 3,
) -> list[Candidate]:
    """把有序频次合并为无序字符对候选，按频次降序排序。

    - 静态集已锚定方向 → ``anchored=True``（可安全回写）。
    - 未锚定 → 取有序频次更高的一侧作为推断方向，``anchored=False``（仅进报告供人工确认）。
    """
    merged: dict[frozenset[str], dict[str, Any]] = {}
    for (a, b), entry in tally.items():
        ukey = frozenset((a, b))
        m = merged.setdefault(
            ukey, {"count": 0, "books": set(), "ordered": defaultdict(int)}
        )
        m["count"] += entry["count"]
        m["books"] |= entry["books"]
        m["ordered"][(a, b)] += entry["count"]

    candidates: list[Candidate] = []
    for ukey, m in merged.items():
        if m["count"] < min_count:
            continue
        x, y = tuple(ukey)
        anchored = False
        wrong = correct = None
        if static_set.get(x) == y:
            wrong, correct, anchored = x, y, True
        elif static_set.get(y) == x:
            wrong, correct, anchored = y, x, True
        else:
            ox = m["ordered"].get((x, y), 0)
            oy = m["ordered"].get((y, x), 0)
            wrong, correct = (x, y) if ox >= oy else (y, x)
        candidates.append(
            Candidate(wrong, correct, m["count"], len(m["books"]), anchored)
        )
    candidates.sort(key=lambda c: (-c.total, c.wrong))
    return candidates


def run(
    db_dir: str | Path,
    min_count: int = 3,
    candidates_out: str = "",
) -> dict[str, Any]:
    """执行挖掘：返回汇总并写出候选报告（只挖掘，不自动改写运行时）。"""
    tally = tally_replace_pairs(db_dir)
    static_set = load_confusion_set()
    candidates = build_candidates(tally, static_set, min_count)

    if candidates_out:
        payload = [asdict(c) for c in candidates]
        Path(candidates_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {
        "db_dir": str(db_dir),
        "ordered_pairs": len(tally),
        "candidates": len(candidates),
        "anchored": sum(1 for c in candidates if c.anchored),
    }


def _default_db_dir() -> str:
    return os.environ.get("KZOCR_DB_DIR") or os.path.join(os.getcwd(), "db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 e2e 分歧挖掘形近字混淆候选集")
    parser.add_argument(
        "--db-dir",
        default=_default_db_dir(),
        help="按书分库的 db 目录（默认 $KZOCR_DB_DIR 或 cwd/db）",
    )
    parser.add_argument(
        "--min-count", type=int, default=3, help="进入候选的最小出现频次（默认 3）"
    )
    parser.add_argument(
        "--candidates-out",
        default="",
        help="候选报告输出路径（默认 <db_dir>/candidate_confusions.json）",
    )
    args = parser.parse_args(argv)

    out_path = args.candidates_out or os.path.join(
        args.db_dir, "candidate_confusions.json"
    )
    summary = run(args.db_dir, args.min_count, out_path)
    print(f"[learn] db_dir={summary['db_dir']}")
    print(
        f"[learn] 有序单字 replace 对={summary['ordered_pairs']} "
        f"候选={summary['candidates']} 其中已锚定={summary['anchored']}"
    )
    print(f"[learn] 候选报告 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
