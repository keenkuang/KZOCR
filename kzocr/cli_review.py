"""校对工单 CLI：查看/确认/修正 E3 验证异常。"""

from __future__ import annotations

import argparse
import logging
import os

from kzocr.storage.db import BookDB

_logger = logging.getLogger(__name__)


def _get_db(book_code: str) -> BookDB:
    db_dir = os.environ.get("KZOCR_DB_DIR", "db")
    return BookDB(book_code, db_dir=db_dir)


def cmd_review_list(args: argparse.Namespace) -> int:
    """列出待处理异常。"""
    db = _get_db(args.book_code)
    anomalies = db.get_unresolved_anomalies(args.book_code, limit=args.limit)
    if not anomalies:
        print(f"✔ 书 {args.book_code} 无待处理异常")
        return 0
    print(f"📋 书 {args.book_code} 待处理异常（{len(anomalies)} 条）:")
    print(f"{'ID':>4} {'页号':>4} {'裁决':>10} {'检测器':>20} {'详情':>30}")
    print("-" * 80)
    for a in anomalies:
        det = (a.get("detector_chain") or "")[:18]
        dets = (a.get("details") or "")[:28]
        print(f"{a['id']:>4} {a['page_num']:>4} {a['verdict_status']:>10} {det:>20} {dets:>30}")
    print("\n使用: kzocr review show <book_code> <id> 查看详情")
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    """显示异常详情。"""
    db = _get_db(args.book_code)
    anomalies = db.get_unresolved_anomalies(args.book_code, limit=200)
    target = [a for a in anomalies if a["id"] == args.id]
    if not target:
        print(f"异常 #{args.id} 不存在或已处理")
        return 1
    a = target[0]
    print(f"异常 #{a['id']}")
    print(f"  页号:     {a['page_num']}")
    print(f"  裁决:     {a['verdict_status']}")
    print(f"  检测器:   {a.get('detector_chain', '')}")
    print(f"  详情:     {a.get('details', '')}")
    print(f"  引擎:     {a.get('engine_label', '')}")
    print(f"  字符数:   {a.get('char_count', 0)}")
    print(f"  创建时间: {a.get('created_at', '')}")
    print()
    print("使用以下命令标记:")
    print(f"  kzocr review resolve {args.book_code} {a['id']} --status confirmed")
    print(f"  kzocr review resolve {args.book_code} {a['id']} --status fixed")
    print(f"  kzocr review resolve {args.book_code} {a['id']} --status wontfix")
    return 0


def cmd_review_resolve(args: argparse.Namespace) -> int:
    """标记异常决议。"""
    db = _get_db(args.book_code)
    db.resolve_anomaly(args.id, resolution=args.status, note=args.note or "")
    print(f"✔ 异常 #{args.id} 已标记为 {args.status}")
    return 0


def build_review_parser(sub) -> None:
    """注册 review 子命令。"""
    p = sub.add_parser("review", help="校对工单管理")
    rsub = p.add_subparsers(dest="review_cmd", required=True)

    pl = rsub.add_parser("list", help="列出待处理异常")
    pl.add_argument("book_code")
    pl.add_argument("--limit", type=int, default=50)
    pl.set_defaults(func=cmd_review_list)

    ps = rsub.add_parser("show", help="显示异常详情")
    ps.add_argument("book_code")
    ps.add_argument("id", type=int)
    ps.set_defaults(func=cmd_review_show)

    pr = rsub.add_parser("resolve", help="标记异常决议")
    pr.add_argument("book_code")
    pr.add_argument("id", type=int)
    pr.add_argument("--status", required=True, choices=["confirmed", "fixed", "wontfix"])
    pr.add_argument("--note", default="")
    pr.set_defaults(func=cmd_review_resolve)
