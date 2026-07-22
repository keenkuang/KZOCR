"""从 e2e 落库的 canonical/error 模型中挖掘形近字混淆对，反哺回 learned_confusion.json。

背景
----
stage 2/3 已把跨引擎分歧派生为字级 ``error_record`` 落进各书库（``kzocr/storage/db.py``）。
这些记录中 ``error_type='replace'`` 且两侧均为单字符的条目，是最干净的形近字混淆特征。
本脚本把这些候选聚合后回写到 ``kzocr/resources/learned_confusion.json``（经由
``cross_align.add_learned_confusion_batch``），即时惠及 ``ConfusionSetDetector`` / VL 仲裁 /
P1 标级，闭合「数据 → 识别率」反哺链路。

与 ``learn_confusion_from_divergences.py`` 的区别
------------------------------------------------
旧脚本只从 ``cross_divergence`` 挖单字 replace 候选、**不消费 canonical 模型、不回写**。
本脚本消费更严格的 ``error_record`` 模型（引擎 vs consensus/human 之差，方向可靠），并真正
回写 learned_confusion。

零运行时风险
------------
默认 **dry-run**：仅打印/写出待回写候选，不触碰 ``learned_confusion.json``。加 ``--apply``
才真正回写。回写方向由 canonical 模型保证（wrong=引擎误识、correct=consensus/human），
安全；但保留人工复核环节符合零风险原则。

用法
----
    python scripts/feedback_canonical_errors.py --db-dir e2e_db            # 默认 dry-run
    python scripts/feedback_canonical_errors.py --db-dir e2e_db --apply    # 真正回写
    python scripts/feedback_canonical_errors.py --db-dir e2e_db --min-count 3 --json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from kzocr.scheduler.cross_align import (
    add_learned_confusion_batch,
    load_confusion_set,
)
from kzocr.storage.db import BookDB


def iter_book_db_paths(db_dir: str | Path) -> list[Path]:
    """枚举 db 目录下所有按书分库的 ``*.db`` 文件（按名排序，结果稳定）。"""
    p = Path(db_dir)
    if not p.is_dir():
        return []
    return sorted(p.glob("*.db"))


def collect_candidates(
    db_dir: str | Path,
    min_count: int = 5,
) -> tuple[dict[tuple[str, str], dict[str, Any]], int, int]:
    """遍历各书库 ``get_confusion_candidates`` 并合并为全局候选。

    返回 ``(merged, scanned, with_errors)``：
    - ``merged``: ``{(wrong, correct): {"count": int, "books": set[str]}}``；
    - ``scanned``: 扫描到的 ``*.db`` 文件数；
    - ``with_errors``: 其中含 ``error_record`` 候选（即贡献了混淆对）的库数。
    """
    merged: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "books": set()}
    )
    scanned = 0
    with_errors = 0
    for path in iter_book_db_paths(db_dir):
        scanned += 1
        book_code = path.stem
        try:
            db = BookDB(book_code, db_dir=str(path.parent))
        except Exception:
            continue
        try:
            cands = db.get_confusion_candidates(min_count=min_count)
            if cands:
                with_errors += 1
            for c in cands:
                key = (c["wrong"], c["correct"])
                merged[key]["count"] += c["count"]
                merged[key]["books"].add(book_code)
        finally:
            db.close()
    return merged, scanned, with_errors


def build_report(
    merged: dict[tuple[str, str], dict[str, Any]],
    static_set: dict[str, str],
) -> list[dict[str, Any]]:
    """把合并候选转成排序报告，并标 ``anchored``（静态集已锚定方向则高置信）。

    每条候选额外带 ``book_list``：该混淆对出现过的按书分库 code 列表（按书聚合视图）。
    """
    report: list[dict[str, Any]] = []
    for (wrong, correct), info in merged.items():
        anchored = static_set.get(wrong) == correct
        report.append({
            "wrong": wrong,
            "correct": correct,
            "count": info["count"],
            "books": len(info["books"]),
            "book_list": sorted(info["books"]),
            "anchored": anchored,
        })
    report.sort(key=lambda r: (-r["count"], r["wrong"]))
    return report


def _print_human(summary: dict[str, Any], top_n: int) -> None:
    """人类可读汇总：先打印统计块，再打印 top-N 混淆对表格。"""
    verb = "回写" if summary["applied"] else "待回写(dry-run)"
    print(f"[feedback] db_dir={summary['db_dir']} min_count={summary['min_count']}")
    print(
        f"[feedback] 扫描库={summary['books_scanned']} "
        f"有候选库={summary['books_with_candidates']} "
        f"候选={summary['candidates']} 已锚定={summary['anchored']} "
        f"动作={verb} 实际新增={summary['added']}"
    )
    report = summary["report"]
    if not report:
        print("  (无达阈候选)")
        return
    shown = report[:top_n] if top_n and top_n > 0 else report
    print(
        f"\n  Top {len(shown)} 混淆对 "
        f"(wrong -> correct | count | 跨书数 | book_list | anchored):"
    )
    for r in shown:
        bl = ",".join(r["book_list"])
        if len(bl) > 64:
            bl = bl[:61] + "..."
        flag = " [anchored]" if r["anchored"] else ""
        print(
            f"    {r['wrong']} -> {r['correct']} | {r['count']} | "
            f"{r['books']} | {bl}{flag}"
        )


def run(
    db_dir: str | Path,
    min_count: int = 5,
    apply: bool = False,
    candidates_out: str = "",
    as_json: bool = False,
    top_n: int = 20,
) -> dict[str, Any]:
    """执行挖掘与（可选）回写，返回汇总。"""
    merged, scanned, with_errors = collect_candidates(db_dir, min_count)
    static_set = load_confusion_set()
    report = build_report(merged, static_set)

    if candidates_out:
        Path(candidates_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    added = 0
    if apply:
        pairs = [
            {"wrong": r["wrong"], "correct": r["correct"], "count": r["count"]}
            for r in report
        ]
        added = add_learned_confusion_batch(
            pairs, source="canonical_stage3", min_freq=min_count
        )

    summary = {
        "db_dir": str(db_dir),
        "min_count": min_count,
        "books_scanned": scanned,
        "books_with_candidates": with_errors,
        "candidates": len(report),
        "anchored": sum(1 for r in report if r["anchored"]),
        "applied": apply,
        "added": added,
        "report": report,
    }
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human(summary, top_n)
    return summary


def _default_db_dir() -> str:
    return os.environ.get("KZOCR_DB_DIR") or os.path.join(os.getcwd(), "db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 canonical/error 模型反哺形近字混淆集")
    parser.add_argument(
        "--db-dir",
        default=_default_db_dir(),
        help="按书分库的 db 目录（默认 $KZOCR_DB_DIR 或 cwd/db）",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=int(os.environ.get("KZOCR_CONFUSION_MIN_FREQ", "5")),
        help="进入候选/回写的最小出现频次（默认 5，读 KZOCR_CONFUSION_MIN_FREQ）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正回写 learned_confusion.json（默认仅 dry-run 打印待回写项）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印待回写候选、不落盘（默认值；显式写出以明确意图）",
    )
    parser.add_argument(
        "--candidates-out",
        default="",
        help="候选报告输出路径（默认不写）",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="控制台表格展示的混淆对条数（默认 20；0 表示全部）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出汇总（机器可读）",
    )
    args = parser.parse_args(argv)

    run(
        args.db_dir,
        min_count=args.min_count,
        apply=args.apply,
        candidates_out=args.candidates_out,
        as_json=args.json,
        top_n=args.top_n,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
