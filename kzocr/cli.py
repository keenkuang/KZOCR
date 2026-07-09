"""KZOCR 命令行入口。

工作流（与 zai 人工校对台 + kHUB 串联）：
    kzocr pipeline <pdf>           # kimi 引擎跑 OCR → 写 zai 控制台库（供人工校对）
    kzocr export   <book_code>     # 从 zai 库导出最终校正 Markdown
    kzocr push     <md_file>       # 把文档通过 API 送入 kHUB
    kzocr smoke                    # 端到端冒烟（mock 引擎 → 适配器 → 导出 → 推送 kHUB）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from kzocr import __version__
from kzocr.config import load_config
from kzocr.engine import run as engine_run
from kzocr.adapter import push_book_to_zai
from kzocr.export_zai import export_book_markdown
from kzocr.khub import client as khub_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("kzocr")


def cmd_pipeline(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.db:
        cfg.zai_db = args.db
    else:
        # 未指定 --db 时落到本地隔离库，避免污染/误读真实 zai 控制台库
        cfg.zai_db = "kzocr.db"
    log.info("运行引擎：%s", args.pdf)
    book = engine_run.run_engine(args.pdf, book_code=args.book_code, config=cfg)
    result = push_book_to_zai(book, db_path=cfg.zai_db, skip_prisma_marker=True)
    log.info("书籍 %s 已写入 zai 库（%s 行/页/段落）", result["book_code"], result["counts"])
    print(f"BOOK_CODE={result['book_code']}")
    return 0


def _safe_out_path(out: str | None, book_code: str) -> str:
    """把导出路径限制在 exports/ 基目录下，仅取文件名，防路径穿越与裸文件名崩溃。"""
    base = Path("exports")
    base.mkdir(exist_ok=True)
    if out:
        name = os.path.basename(out) or "export.md"
        return str(base / name)
    return str(base / f"{book_code}.md")


def cmd_export(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.db:
        cfg.zai_db = args.db
    else:
        # 与 pipeline 默认库一致，避免"写入 A、读出 B 报未找到书籍"
        cfg.zai_db = "kzocr.db"
    md = export_book_markdown(args.book_code, db_path=cfg.zai_db)
    out = _safe_out_path(args.out, args.book_code)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("已导出：%s (%d 字符)", out, len(md))
    print(out)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    with open(args.file, "r", encoding="utf-8") as f:
        content = f.read()
    title = args.title or os.path.splitext(os.path.basename(args.file))[0]
    try:
        resp = khub_client.push_document(
            title=title, content=content, source="KZOCR",
            source_id=args.source_id, metadata={"exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
            base_url=args.khub_url,
        )
    except khub_client.KHUBError as e:
        log.error("推送失败：%s", e)
        return 1
    doc_id = resp.get("doc_id") if isinstance(resp, dict) else None
    log.info("已推送至 kHUB（doc_id=%s）", doc_id)  # 不记录响应正文（含敏感文本）
    print(doc_id or resp)
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.db:
        cfg.zai_db = args.db
    else:
        # 冒烟用隔离库，避免污染真实 zai 控制台库（schema 由适配器自管理）
        cfg.zai_db = "smoke.db"
    cfg.use_mock = True
    log.info("==> [1/4] mock 引擎产出")
    book = engine_run.run_engine("mock.pdf", book_code="TCM-SMOKE-001", config=cfg)
    assert book.is_mock, "smoke 应使用 mock 引擎"
    log.info("==> [2/4] 写入 zai 库")
    res = push_book_to_zai(book, db_path=cfg.zai_db, skip_prisma_marker=True)
    log.info("    写入统计：%s", res["counts"])
    log.info("==> [3/4] 从 zai 库导出最终文档")
    md = export_book_markdown(book.book_code, db_path=cfg.zai_db)
    out = "exports/smoke.md"
    os.makedirs("exports", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("    导出：%s", out)
    if not args.skip_push:
        log.info("==> [4/4] 推送 kHUB（需 kHUB 服务运行于 %s）", cfg.khub_base_url)
        try:
            resp = khub_client.push_document(
                title=book.title, content=md, source="KZOCR", source_id=book.book_code,
                metadata={"smoke": True}, base_url=args.khub_url,
            )
            log.info("    kHUB 已接收（doc_id=%s）", resp.get("doc_id") if isinstance(resp, dict) else None)
            if args.verify and resp.get("doc_id"):
                rec = khub_client.verify_in_khub(resp["doc_id"])
                log.info("    本地核验：%s", rec)
        except RuntimeError as e:
            log.warning("    推送跳过（%s）。可先启动 kHUB：cd khub-m1 && python -m khub serve", e)
    else:
        log.info("==> [4/4] 跳过推送（--skip-push）")
    log.info("冒烟测试完成 ✅")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kzocr", description="KZOCR 编排：kimi 引擎 + zai 校对台 + kHUB")
    p.add_argument("--version", action="version", version=f"kzocr {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("pipeline", help="运行引擎并写入 zai 库")
    pp.add_argument("pdf")
    pp.add_argument("--book-code")
    pp.add_argument("--db", help="zai 的 SQLite 路径（覆盖配置）")
    pp.set_defaults(func=cmd_pipeline)

    pe = sub.add_parser("export", help="从 zai 库导出最终校正 Markdown")
    pe.add_argument("book_code")
    pe.add_argument("--db")
    pe.add_argument("--out")
    pe.set_defaults(func=cmd_export)

    ppush = sub.add_parser("push", help="把文档推送进 kHUB")
    ppush.add_argument("file")
    ppush.add_argument("--title")
    ppush.add_argument("--source-id")
    ppush.add_argument("--khub-url", default=None)
    ppush.set_defaults(func=cmd_push)

    ps = sub.add_parser("smoke", help="端到端冒烟测试")
    ps.add_argument("--db")
    ps.add_argument("--khub-url", default=None)
    ps.add_argument("--skip-push", action="store_true")
    ps.add_argument("--verify", action="store_true")
    ps.set_defaults(func=cmd_smoke)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except khub_client.KHUBError as e:
        log.error("%s", e)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("执行失败：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
