"""kzocr review CLI 子命令 — v0.7 §5.6。

审核清单生成与修正回写。
"""
from __future__ import annotations

import argparse

from kzocr.config import load_config
from kzocr.scheduler.review_manifest import (
    build_review_manifest,
    export_divergence_html,
    export_review_manifest_json,
    feedback_apply,
    visualize_char_boxes,
)
from kzocr.storage.db import BookDB


def cmd_review_manifest(args: argparse.Namespace) -> int:
    """``kzocr review manifest <book_code> [--json] [--out PATH]`` — 生成全书审核清单。"""
    cfg = load_config()
    db = BookDB(args.book_code, db_dir=cfg.scheduler.db_dir)
    try:
        manifest = build_review_manifest(db)
        if getattr(args, "json", False):
            out = getattr(args, "out", None)
            path = export_review_manifest_json(manifest, out_path=out)
            print(f"已导出审核清单 JSON：{path}")
            return 0
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


def cmd_review_boxes(args: argparse.Namespace) -> int:
    """``kzocr review boxes <book_code> <page_num> [--pdf PATH] [--out PATH]`` — 字符级 bbox 可视化。"""
    cfg = load_config()
    db = BookDB(args.book_code, db_dir=cfg.scheduler.db_dir)
    try:
        path = visualize_char_boxes(
            db, args.book_code, args.page_num,
            pdf_path=args.pdf,
            out_path=args.out,
        )
        print(f"已生成 bbox 可视化图：{path}")
        return 0
    finally:
        db.close()


def build_review_parser(sub: argparse._SubParsersAction) -> None:
    """在子命令解析器上注册 review 子命令组。"""
    from kzocr.cli_review import (
        cmd_review_apply,
        cmd_review_boxes,
        cmd_review_html,
        cmd_review_manifest,
    )
    pv = sub.add_parser("review", help="审核清单生成与修正回写")
    vsub = pv.add_subparsers(dest="review_cmd", required=True)
    vm = vsub.add_parser("manifest", help="生成全书审核清单")
    vm.add_argument("book_code", help="书籍编码")
    vm.add_argument("--json", action="store_true", help="导出审核清单为 JSON 文件")
    vm.add_argument("--out", default=None, help="JSON 输出路径（默认 <book_code>_review_manifest.json）")
    vm.set_defaults(review_func=cmd_review_manifest)
    va = vsub.add_parser("apply", help="回写审核修正到 BookDB（支持多本批量）")
    va.add_argument("book_code", nargs="+", help="书籍编码（可多个，批量回写）")
    va.set_defaults(review_func=cmd_review_apply)
    vh = vsub.add_parser("html", help="生成跨引擎分歧高亮 HTML 报告")
    vh.add_argument("book_code", help="书籍编码")
    vh.add_argument("--out", default=None, help="输出 HTML 路径（默认 <book_code>_divergence.html）")
    vh.set_defaults(review_func=cmd_review_html)
    vb = vsub.add_parser("boxes", help="渲染字符级 bbox 可视化图像")
    vb.add_argument("book_code", help="书籍编码")
    vb.add_argument("page_num", type=int, help="页码")
    vb.add_argument("--pdf", default=None, help="PDF 文件路径（可选，叠加框线于页图上）")
    vb.add_argument("--out", default=None, help="输出 PNG 路径（默认 <book_code>_p<page_num>_boxes.png）")
    vb.set_defaults(review_func=cmd_review_boxes)


