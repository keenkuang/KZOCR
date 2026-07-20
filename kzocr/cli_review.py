"""kzocr review CLI 子命令 — v0.7 §5.6。

审核清单生成与修正回写。
"""
from __future__ import annotations

import argparse

from kzocr.config import load_config
from kzocr.scheduler.review_manifest import (
    build_review_manifest,
    export_divergence_html,
    feedback_apply,
)
from kzocr.storage.db import BookDB


def cmd_review_manifest(args: argparse.Namespace) -> int:
    """``kzocr review manifest <book_code>`` — 生成全书审核清单。"""
    cfg = load_config()
    db = BookDB(args.book_code, db_dir=cfg.scheduler.db_dir)
    try:
        manifest = build_review_manifest(db)
        print(f"book_code={manifest.book_code}")
        print(f"pages={len(manifest.pages)}")
        for page in manifest.pages:
            print(f"  page {page.page_num}: priority={page.priority}, "
                  f"engines={len(page.engine_results)}, issues={len(page.issues)}")
        return 0
    finally:
        db.close()


def cmd_review_apply(args: argparse.Namespace) -> int:
    """``kzocr review apply <book_code> [<book_code> ...]`` — 批量回写审核修正到 BookDB。"""
    cfg = load_config()
    total = 0
    for code in args.book_code:
        db = BookDB(code, db_dir=cfg.scheduler.db_dir)
        try:
            manifest = build_review_manifest(db)
            count = feedback_apply(manifest, db)
            if count == 0:
                print(f"[{code}] 无待回写的修正条目（请通过 manifest 确认后手动填写 expected）")
            else:
                print(f"[{code}] 已回写 {count} 条修正")
            total += count
        finally:
            db.close()
    print(f"合计回写 {total} 条修正（{len(args.book_code)} 本）")
    return 0


def cmd_review_html(args: argparse.Namespace) -> int:
    """``kzocr review html <book_code> [--out PATH]`` — 生成分歧高亮 HTML 报告。"""
    cfg = load_config()
    db = BookDB(args.book_code, db_dir=cfg.scheduler.db_dir)
    try:
        path = export_divergence_html(db, args.book_code, out_path=args.out)
        print(f"已生成分歧高亮报告：{path}")
        return 0
    finally:
        db.close()


def build_review_parser(sub: argparse._SubParsersAction) -> None:
    """在子命令解析器上注册 review 子命令组。"""
    from kzocr.cli_review import (
        cmd_review_apply,
        cmd_review_html,
        cmd_review_manifest,
    )
    pv = sub.add_parser("review", help="审核清单生成与修正回写")
    vsub = pv.add_subparsers(dest="review_cmd", required=True)
    vm = vsub.add_parser("manifest", help="生成全书审核清单")
    vm.add_argument("book_code", help="书籍编码")
    vm.set_defaults(review_func=cmd_review_manifest)
    va = vsub.add_parser("apply", help="回写审核修正到 BookDB（支持多本批量）")
    va.add_argument("book_code", nargs="+", help="书籍编码（可多个，批量回写）")
    va.set_defaults(review_func=cmd_review_apply)
    vh = vsub.add_parser("html", help="生成跨引擎分歧高亮 HTML 报告")
    vh.add_argument("book_code", help="书籍编码")
    vh.add_argument("--out", default=None, help="输出 HTML 路径（默认 <book_code>_divergence.html）")
    vh.set_defaults(review_func=cmd_review_html)
