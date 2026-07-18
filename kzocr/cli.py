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
    log.info("运行引擎：%s（v0.7 编排调度已启用）", args.pdf)
    if args.cross_check:
        os.environ["KZOCR_ENABLE_CROSS_CHECK"] = "1"
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
        cfg.zai_db = "kzocr.db"
    if args.format == "json":
        from kzocr.export_zai import export_json
        data = export_json(args.book_code, db_path=cfg.zai_db)
        out = _safe_out_path(args.out or f"{args.book_code}.json", args.book_code)
        with open(out, "w", encoding="utf-8") as f:
            f.write(data)
        log.info("已导出 JSON：%s", out)
    else:
        md = export_book_markdown(args.book_code, db_path=cfg.zai_db)
        out = _safe_out_path(args.out, args.book_code)
        with open(out, "w", encoding="utf-8") as f:
            f.write(md)
        log.info("已导出 Markdown：%s (%d 字符)", out, len(md))
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
    log.info("==> [1/4] mock 引擎产出（v0.7 编排调度已启用）")
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


def cmd_web(args: argparse.Namespace) -> int:
    """启动 Web 管理面板。"""
    import uvicorn
    log.info("Web 面板启动于 http://%s:%d", args.host, args.port)
    uvicorn.run("kzocr.web.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_quality_check(args: argparse.Namespace) -> int:
    """运行质检并写入 DB。"""
    from kzocr.analysis.quality import QualityChecker
    from kzocr.analysis.recipe_parser import parse_recipes
    from kzocr.storage.db import BookDB
    import json
    import os
    dbd = os.environ.get("KZOCR_DB_DIR", "db")
    db = BookDB(args.book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        pages_text = [p["verify_details"] or "" for p in progress if p.get("verify_details")]
        if not pages_text:
            pages_text = [""] * len(progress)
        recipes = parse_recipes(pages_text)
        checker = QualityChecker()
        count = 0
        for r in recipes:
            result = checker.check(r)
            issues_json = json.dumps([
                {"field": i.field, "type": i.issue_type, "severity": i.severity, "detail": i.detail}
                for i in result.issues
            ], ensure_ascii=False)
            db.save_quality_result(r.recipe_no, result.status, result.confidence, issues_json)
            count += 1
        log.info("质检完成：%d 条已写入", count)
    finally:
        db.close()
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """输出 shell 自动补全脚本。"""
    import shtab
    parser = build_parser()
    print(shtab.complete(parser, shell=args.shell))
    return 0


def cmd_quality_list(args: argparse.Namespace) -> int:
    """列出质检结果。"""
    from kzocr.storage.db import BookDB
    import os
    dbd = os.environ.get("KZOCR_DB_DIR", "db")
    db = BookDB(args.book_code, db_dir=dbd)
    try:
        results = db.get_quality_results(status_filter=args.status)
        print(f"质检结果：{args.book_code}（{len(results)} 条）")
        for r in results:
            print(f"  {r['recipe_no']}: {r['status']} (confidence={r['confidence']})")
    finally:
        db.close()
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """批量处理目录内的所有 PDF。"""
    from pathlib import Path
    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.is_dir():
        log.error("目录不存在：%s", pdf_dir)
        return 1
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        log.warning("目录中无 PDF 文件：%s", pdf_dir)
        return 0
    log.info("批量处理 %d 个 PDF", len(pdfs))
    success = 0
    for i, pdf in enumerate(pdfs):
        book_code = pdf.stem
        log.info("[%d/%d] 处理：%s (%s)", i + 1, len(pdfs), book_code, pdf.name)
        try:
            from kzocr.engine.run import run_engine
            cfg_override = load_config()
            if args.db:
                cfg_override.zai_db = args.db
            result = run_engine(str(pdf), book_code=book_code, config=cfg_override)
            if result and len(result.pages) > 0:
                success += 1
                log.info("  ✅ 完成：%d 页", len(result.pages))
            else:
                log.warning("  ⚠ 无输出页")
        except Exception as exc:
            log.error("  ❌ 失败：%s", exc)
    log.info("批量处理完成：%d/%d 成功", success, len(pdfs))
    return 0 if success == len(pdfs) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kzocr", description="KZOCR 编排：kimi 引擎 + zai 校对台 + kHUB")
    p.add_argument("--version", action="version", version=f"kzocr {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("pipeline", help="运行引擎并写入 zai 库")
    pp.add_argument("pdf")
    pp.add_argument("--book-code")
    pp.add_argument("--db", help="zai 的 SQLite 路径（覆盖配置）")
    pp.add_argument("--cross-check", action="store_true", help="启用成功页跨引擎采样比对")
    pp.set_defaults(func=cmd_pipeline)

    pe = sub.add_parser("export", help="从 zai 库导出最终校正文档")
    pe.add_argument("book_code")
    pe.add_argument("--db")
    pe.add_argument("--out")
    pe.add_argument("--format", choices=["md", "json"], default="md", help="导出格式（默认 md）")
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

    pb = sub.add_parser("batch", help="批量处理目录内所有 PDF")
    pb.add_argument("pdf_dir", help="PDF 目录路径")
    pb.add_argument("--db")
    pb.set_defaults(func=cmd_batch)

    pw = sub.add_parser("web", help="启动 Web 管理面板")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8080)
    pw.set_defaults(func=cmd_web)

    # quality 子命令
    pq = sub.add_parser("quality", help="方剂质量质检")
    qsub = pq.add_subparsers(dest="quality_cmd", required=True)
    qc = qsub.add_parser("check", help="运行质检并写入 DB")
    qc.add_argument("book_code")
    qc.set_defaults(func=cmd_quality_check)
    ql = qsub.add_parser("list", help="列出质检结果")
    ql.add_argument("book_code")
    ql.add_argument("--status", choices=["verified", "corrected"], default=None)
    ql.set_defaults(func=cmd_quality_list)

    # completion 子命令
    pc = sub.add_parser("completion", help="输出 shell 自动补全脚本")
    pc.add_argument("shell", choices=["bash", "zsh", "fish"], help="shell 类型")
    pc.set_defaults(func=cmd_completion)

    # benchmark 子命令组（§7.1）
    from kzocr.cli_benchmark import cmd_bench_status, cmd_bench_history, cmd_bench_run, cmd_bench_reset
    pb = sub.add_parser("benchmark", help="引擎性能基准查询与管理")
    bsub = pb.add_subparsers(dest="bench_cmd", required=False)
    # status（默认）
    bs = bsub.add_parser("status", help="显示引擎统计")
    bs.set_defaults(bench_func=cmd_bench_status)
    # history
    bh = bsub.add_parser("history", help="显示原始 NDJSON 事件")
    bh.add_argument("--engine", default="", help="按引擎名筛选")
    bh.set_defaults(bench_func=cmd_bench_history)
    # run
    br = bsub.add_parser("run", help="运行一轮快速基准（probe + 注册）")
    br.set_defaults(bench_func=cmd_bench_run)
    # reset
    brs = bsub.add_parser("reset", help="清空 benchmark 目录")
    brs.add_argument("--force", action="store_true", help="跳过确认提示")
    brs.set_defaults(bench_func=cmd_bench_reset)

    from kzocr.cli_review import build_review_parser
    build_review_parser(sub)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "bench_func"):
            return args.bench_func(args)
        if hasattr(args, "review_func"):
            return args.review_func(args)
        return args.func(args)
    except khub_client.KHUBError as e:
        log.error("%s", e)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("执行失败：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
